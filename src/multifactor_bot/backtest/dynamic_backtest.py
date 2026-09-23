# dynamic_backtest.py - 동적 유니버스 백테스팅 (Look-ahead bias 제거)

import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    print("⚠️ yfinance 미설치: pip install yfinance")
    sys.exit(1)

from multifactor_bot import config
from multifactor_bot.screening.dynamic_screener import DynamicScreener
from multifactor_bot.screening.historical_constituents import HistoricalSP500
from multifactor_bot.brokers.kis_data_provider import KISDataProvider


class DynamicMultiFactorBacktest:
    """
    동적 유니버스 멀티팩터 백테스팅

    유니버스 선택지 (우선순위 순):
      1. use_historical_constituents=True : 리밸런싱 날짜마다 "그날 S&P 500 에 실제로 있던 종목"
         (HistoricalSP500). 생존 편향과 사후 정보 편향을 제거하는 기본 모드.
      2. use_dynamic_screening=True        : 현재 S&P500+NASDAQ100 구성종목을 백테스트 시작 시
         한 번 스크리닝해 전 기간 재사용. 유니버스가 "현재" 기준이라 생존 편향이 남는다.
      3. 둘 다 False                        : config.WATCHLIST (2025-11 시총 상위 50, 편향 가장 큼)

    편향 제거 범위
      - 가격 데이터는 신호일까지만 사용, 체결은 다음 거래일 시가
      - 유니버스는 시점별 구성종목 (모드 1)
      - 유동성 필터(최소 주가/거래량)는 신호일 시점 데이터로 판정
    남아 있는 편향
      - value/quality 팩터는 yfinance 의 "현재" 재무 정보(info)를 쓴다. 과거 재무 이력은 무료로
        얻기 어려워 그대로 두었다. 이 두 팩터는 과거 시점에서 알 수 없던 정보를 쓴다.
      - 상장폐지/합병으로 가격 이력이 사라진 종목은 후보에 있어도 계산에서 빠진다.
    """

    def __init__(self,
                 use_dynamic_screening: bool = True,
                 db_recording: bool = False,
                 account_id: int = None,
                 use_historical_constituents: bool = True,
                 risk_rules_enabled: bool = True):
        """
        Args:
            use_dynamic_screening: 현재 구성종목 스크리닝 사용 여부 (historical 이 꺼졌을 때만 의미)
            db_recording: DB 기록 여부
            account_id: DB 기록할 계좌 ID (backtest 전용 계좌)
            use_historical_constituents: 시점별 S&P 500 구성종목 사용 (기본 True)
            risk_rules_enabled: 리밸런싱 사이 거래일마다 손절/트레일링/보유기간 만료 검사 (실봇 규칙)
        """
        self.config = config
        self.start_date = pd.to_datetime(config.BACKTEST_START_DATE)
        self.end_date = pd.to_datetime(config.BACKTEST_END_DATE)
        self.initial_capital = config.BACKTEST_INITIAL_CAPITAL
        self.commission = config.BACKTEST_COMMISSION
        self.rebalance_days = config.REBALANCE_PERIOD_DAYS
        self.top_n = config.TOP_N_STOCKS
        self.weights = config.FACTOR_WEIGHTS

        # 리스크 규칙 (core/bot.check_risk_management 와 동일한 의미)
        #   손절/트레일링은 MIN_HOLD_DAYS 이후에만, 보유기간 만료는 독립. 우선순위 손절 > 트레일링 > 만료
        #   실봇은 30분마다 검사하지만 백테스트는 일봉 종가로 하루 1회 검사한다.
        self.risk_rules_enabled = risk_rules_enabled
        self.stop_loss_enabled = getattr(config, 'STOP_LOSS_ENABLED', False)
        self.stop_loss_pct = getattr(config, 'STOP_LOSS_PERCENT', -15.0)
        self.trailing_enabled = getattr(config, 'TRAILING_STOP_ENABLED', False)
        self.trailing_pct = getattr(config, 'TRAILING_STOP_PERCENT', -15.0)
        self.min_hold_days = getattr(config, 'MIN_HOLD_DAYS', 0)
        self.max_hold_days = getattr(config, 'MAX_HOLD_DAYS', 9999)

        # 데이터 제공자 (yfinance + KIS API 백업)
        self.data_provider = KISDataProvider(use_yfinance=True, config=config)
        print("📊 데이터 제공자 초기화 (yfinance + KIS API 백업)")

        # 시점별 구성종목 (생존 편향 제거) - 실제 로드는 첫 리밸런싱 때
        self.use_historical_constituents = use_historical_constituents
        self.historical = HistoricalSP500() if use_historical_constituents else None

        # 동적 스크리닝 (현재 구성종목 기준)
        self.use_dynamic_screening = use_dynamic_screening and not use_historical_constituents
        self.screener = None
        self.sectors = list(getattr(config, 'UNIVERSE_SECTORS', []))
        self._sector_map = None  # 시점별 모드에서 첫 사용 시 Wikipedia 표로 채움

        if self.use_dynamic_screening:
            self.screener = DynamicScreener(
                min_market_cap=config.DYNAMIC_SCREENING_MIN_MARKET_CAP,
                min_price=config.DYNAMIC_SCREENING_MIN_PRICE,
                min_avg_volume=config.DYNAMIC_SCREENING_MIN_VOLUME,
                max_workers=config.DYNAMIC_SCREENING_WORKERS,
                sectors=self.sectors,
            )

        # DB 기록
        self.db_recording = db_recording
        self.account_id = account_id
        self.db_manager = None

        if self.db_recording:
            try:
                from multifactor_bot.database.paper_trading import PaperTradingManager
                self.db_manager = PaperTradingManager(account_id=account_id or config.PAPER_ACCOUNT_ID)
                print("📊 백테스트 DB 기록 활성화")
            except Exception as e:
                print(f"⚠️ DB 초기화 실패 (기록 없이 진행): {e}")
                self.db_recording = False

        # 백테스트 상태
        self.cash = self.initial_capital
        self.holdings = {}  # {ticker: {'shares', 'avg_price', 'buy_date'}}
        self.trades_history = []
        self.portfolio_history = []

        # 동적 유니버스 캐시 (백테스트 전체 기간 동안 재사용)
        self._backtest_universe = None

        # 가격/재무 캐시: 종목당 한 번만 내려받고 날짜로 잘라 쓴다 (리밸런싱 70회 × 종목 80개 반복 다운로드 방지)
        # look-ahead 방지는 _history_until() 이 date 이하로 자르는 것으로 보장한다.
        self._history_cache: Dict[str, Optional[pd.DataFrame]] = {}
        self._info_cache: Dict[str, dict] = {}

    def run(self):
        """백테스팅 메인 실행"""
        print("=" * 80)
        print("동적 유니버스 멀티팩터 백테스팅 (Look-ahead Bias 제거)")
        print("=" * 80)
        print(f"기간: {self.start_date.date()} ~ {self.end_date.date()}")
        print(f"초기 자본: ${self.initial_capital:,.0f}")
        print(f"유니버스: {self.universe_label()}")
        print(f"리밸런싱: {self.rebalance_days}일마다")
        print(f"DB 기록: {'✅ ON' if self.db_recording else '❌ OFF'}")
        print(f"리스크 규칙: {self.risk_rules_label()}")
        print("=" * 80)

        self.backtest_with_dynamic_universe()
        self.analyze_performance()

    def risk_rules_label(self) -> str:
        if not self.risk_rules_enabled:
            return '❌ OFF (리밸런싱 매도만)'
        parts = []
        if self.stop_loss_enabled:
            parts.append(f"손절 {self.stop_loss_pct:+.0f}%")
        if self.trailing_enabled:
            parts.append(f"트레일링 {self.trailing_pct:+.0f}%")
        parts.append(f"최소 {self.min_hold_days}일 / 최대 {self.max_hold_days}일")
        return '✅ ' + ', '.join(parts) + ' (일봉 종가 기준)'

    def universe_label(self) -> str:
        if self.use_historical_constituents:
            label = '📜 시점별 S&P500 구성종목 (생존편향 제거)'
        elif self.use_dynamic_screening:
            label = '🔍 현재 구성종목 스크리닝 (생존편향 있음)'
        else:
            label = f'📋 고정 WATCHLIST ({len(config.WATCHLIST)}개, 생존편향 큼)'
        if self.sectors and not (not self.use_historical_constituents and not self.use_dynamic_screening):
            label += f" | 섹터: {', '.join(self.sectors)}"
        return label

    def _get_sector_map(self):
        """Wikipedia S&P500/NASDAQ100 표에서 섹터를 채운 SectorMap (없는 종목은 yfinance 로 보충·캐시)"""
        if self._sector_map is None:
            screener = DynamicScreener(sectors=self.sectors)
            screener.get_sp500_tickers()
            screener.get_nasdaq100_tickers()
            self._sector_map = screener.sector_map
        return self._sector_map

    def _apply_sectors(self, universe: List[str], date: datetime) -> List[str]:
        if not self.sectors:
            return universe
        kept = self._get_sector_map().filter(universe, self.sectors, lookup_missing=True)
        print(f"🏷️ [{date.date()}] 섹터 필터 {self.sectors}: {len(universe)}개 → {len(kept)}개")
        return kept

    def get_universe_at_date(self, date: datetime) -> List[str]:
        """특정 날짜의 유니버스 생성 (폴백 로직 포함)"""
        if self.use_historical_constituents and self.historical is not None:
            try:
                universe = self.historical.constituents_at(date)
                if universe:
                    print(f"📜 [{date.date()}] 당시 S&P500 구성종목 {len(universe)}개")
                    universe = self._apply_sectors(universe, date)
                    if universe:
                        return universe
                    print(f"   ⚠️ [{date.date()}] 섹터 필터 후 종목 없음 → 고정 유니버스로 폴백")
                    return config.WATCHLIST
                print(f"   ⚠️ [{date.date()}] 시점별 구성종목 없음 → 고정 유니버스로 폴백")
            except Exception as e:
                print(f"   ❌ 시점별 구성종목 조회 오류 ({e}) → 고정 유니버스로 폴백")
            return config.WATCHLIST

        if self.use_dynamic_screening and self.screener:
            # 백테스트 시작 시 한 번만 스크리닝 (전체 기간 동안 재사용)
            if self._backtest_universe is None:
                print(f"\n🔍 [{date.date()}] 동적 스크리닝 실행 (백테스트 유니버스 생성)...")
                try:
                    universe = self.screener.screen_universe(
                        reference_date=date,
                        max_tickers=config.DYNAMIC_SCREENING_MAX_TICKERS
                    )

                    # 동적 스크리닝 성공 시
                    if universe and len(universe) > 0:
                        print(f"   ✅ {len(universe)}개 종목 선정 (전체 백테스트 기간 동안 사용)")
                        self._backtest_universe = universe
                        return universe
                    else:
                        # 빈 리스트 반환 시 폴백
                        print(f"   ⚠️ 동적 스크리닝 결과 없음 → 고정 유니버스로 폴백")
                        self._backtest_universe = config.WATCHLIST
                        return config.WATCHLIST

                except Exception as e:
                    print(f"   ❌ 동적 스크리닝 오류 ({e}) → 고정 유니버스로 폴백")
                    self._backtest_universe = config.WATCHLIST
                    return config.WATCHLIST
            else:
                # 이미 스크리닝 완료 → 캐시된 유니버스 재사용
                return self._backtest_universe

        # 동적 스크리닝 미사용 시 고정 유니버스
        return config.WATCHLIST

    # ------------------------------------------------------------------ 데이터 캐시
    def _history(self, ticker: str) -> Optional[pd.DataFrame]:
        """백테스트 전 구간(+400일 선행) 가격 이력. 실패하면 None 을 캐시해 재시도하지 않는다."""
        if ticker not in self._history_cache:
            start = (self.start_date - timedelta(days=400)).strftime('%Y-%m-%d')
            end = (self.end_date + timedelta(days=10)).strftime('%Y-%m-%d')
            try:
                df = self.data_provider.download(ticker=ticker, start=start, end=end, period='1y')
                if df is not None and not df.empty and getattr(df.index, 'tz', None) is not None:
                    df.index = df.index.tz_localize(None)
                self._history_cache[ticker] = df if (df is not None and not df.empty) else None
            except Exception:
                self._history_cache[ticker] = None
        return self._history_cache[ticker]

    def _history_until(self, ticker: str, date: datetime, lookback_days: int = 400) -> Optional[pd.DataFrame]:
        """date 까지의 최근 lookback_days 일 데이터 (look-ahead 없음)"""
        df = self._history(ticker)
        if df is None:
            return None
        start = pd.Timestamp(date) - timedelta(days=lookback_days)
        out = df[(df.index >= start) & (df.index <= pd.Timestamp(date))]
        return out if not out.empty else None

    def _price_on_or_after(self, ticker: str, date: datetime, max_days: int = 7) -> Optional[float]:
        """date 이후 첫 거래일 종가 (체결가). 없으면 date 이전 마지막 종가."""
        df = self._history(ticker)
        if df is None:
            return None
        ts = pd.Timestamp(date)
        after = df[(df.index >= ts) & (df.index <= ts + timedelta(days=max_days))]['Close'].dropna()
        if not after.empty:
            return float(after.iloc[0])
        before = df[df.index < ts]['Close'].dropna()
        return float(before.iloc[-1]) if not before.empty else None

    def _info(self, ticker: str) -> dict:
        """yfinance 재무 정보 (종목당 1회). 실패 시 빈 dict."""
        if ticker not in self._info_cache:
            try:
                self._info_cache[ticker] = yf.Ticker(ticker).info or {}
            except Exception:
                self._info_cache[ticker] = {}
        return self._info_cache[ticker]

    def calculate_factors_at_date(self, universe: List[str], date: datetime) -> pd.DataFrame:
        """특정 날짜의 팩터 계산 (그날까지의 데이터만 사용)"""
        factors = []
        failed_count = 0
        no_data_count = 0
        insufficient_data_count = 0
        illiquid_count = 0

        for ticker in universe:
            try:
                # 그날까지의 데이터만 사용 (Look-ahead bias 제거) - 캐시에서 슬라이스
                df = self._history_until(ticker, date, lookback_days=400)

                # 빈 데이터프레임 체크
                if df is None or df.empty:
                    no_data_count += 1
                    continue

                if len(df) < 126:
                    insufficient_data_count += 1
                    continue

                # 그날까지의 데이터만 사용
                df_until = df[df.index <= date]

                if len(df_until) < 126:
                    insufficient_data_count += 1
                    continue

                # 가격 추출: 마지막 유효 종가 (NaN 행 방어)
                close = df_until['Close'].dropna()
                if close.empty:
                    no_data_count += 1
                    continue
                price = float(close.iloc[-1])
                if not np.isfinite(price) or price <= 0:
                    no_data_count += 1
                    continue

                # 유동성 필터: 신호일 시점의 주가/20일 평균 거래량 (look-ahead 없음)
                if not self._passes_liquidity(df_until, price):
                    illiquid_count += 1
                    continue

                # stock.info는 선택적 (401 에러 방지)
                value_factor = np.nan
                quality_factor = np.nan

                info = self._info(ticker)
                if info:
                    value_factor = self._calculate_value(info)
                    quality_factor = self._calculate_quality(info)

                factors.append({
                    'ticker': ticker,
                    'price': price,
                    'momentum': self._calculate_momentum(df_until),
                    'value': value_factor,
                    'quality': quality_factor,
                    'volatility': self._calculate_volatility(df_until)
                })
            except Exception as e:
                # 에러는 조용히 스킵 (해당 종목 제외)
                failed_count += 1
                continue

        # 디버깅 정보 출력
        if len(factors) == 0:
            print(f"   ❌ 디버깅 정보:")
            print(f"      - 데이터 없음: {no_data_count}개")
            print(f"      - 데이터 부족(<126일): {insufficient_data_count}개")
            print(f"      - 유동성 미달: {illiquid_count}개")
            print(f"      - 예외 발생: {failed_count}개")
            print(f"      - 성공: {len(factors)}개")

        if illiquid_count:
            print(f"   유동성 필터 제외: {illiquid_count}개 (주가 ${config.DYNAMIC_SCREENING_MIN_PRICE}+, "
                  f"20일 평균 거래량 {config.DYNAMIC_SCREENING_MIN_VOLUME:,}+)")
        return pd.DataFrame(factors)

    @staticmethod
    def _passes_liquidity(df_until: pd.DataFrame, price: float) -> bool:
        """신호일 기준 최소 주가 / 20일 평균 거래량 필터"""
        if price < config.DYNAMIC_SCREENING_MIN_PRICE:
            return False
        if 'Volume' in df_until.columns:
            avg_vol = float(df_until['Volume'].tail(20).mean())
            if pd.notna(avg_vol) and avg_vol < config.DYNAMIC_SCREENING_MIN_VOLUME:
                return False
        return True

    def _calculate_momentum(self, df):
        try:
            period = min(config.MOMENTUM_PERIOD, len(df) - 1)
            return (df['Close'].iloc[-1] / df['Close'].iloc[-period] - 1) * 100
        except:
            return np.nan

    def _calculate_value(self, info):
        try:
            pe = info.get('forwardPE', info.get('trailingPE', np.nan))
            pb = info.get('priceToBook', np.nan)
            if pd.isna(pe) or pd.isna(pb) or pe <= 0 or pb <= 0:
                return np.nan
            return (1 / pe + 1 / pb) / 2
        except:
            return np.nan

    def _calculate_quality(self, info):
        try:
            roe = info.get('returnOnEquity', np.nan)
            debt = info.get('debtToEquity', np.nan)
            if pd.isna(roe):
                return np.nan
            if pd.isna(debt):
                return roe * 100
            return roe * 100 - (debt / 100)
        except:
            return np.nan

    def _calculate_volatility(self, df):
        try:
            period = min(config.VOLATILITY_PERIOD, len(df))
            returns = df['Close'].pct_change().dropna().tail(period)
            vol = returns.std() * np.sqrt(252) * 100
            return 100 / vol if vol > 0 else np.nan
        except:
            return np.nan

    def normalize_and_score(self, factors_df):
        """팩터 정규화 및 종합 점수 계산"""
        if factors_df.empty:
            return factors_df

        factor_cols = ['momentum', 'value', 'quality', 'volatility']

        # 데이터 타입을 float으로 명시적 변환
        for col in factor_cols:
            factors_df[col] = pd.to_numeric(factors_df[col], errors='coerce')

        for col in factor_cols:
            mean = factors_df[col].mean(skipna=True)
            std = factors_df[col].std(skipna=True)

            if pd.notna(std) and std > 0:
                factors_df[f'{col}_z'] = (factors_df[col] - mean) / std
            else:
                factors_df[f'{col}_z'] = 0

            # NaN을 0으로 채우기 (해당 팩터가 없는 종목은 중립으로 처리)
            factors_df[f'{col}_z'] = factors_df[f'{col}_z'].fillna(0)

        factors_df['composite_score'] = (
            factors_df['momentum_z'] * self.weights['momentum'] +
            factors_df['value_z'] * self.weights['value'] +
            factors_df['quality_z'] * self.weights['quality'] +
            factors_df['volatility_z'] * self.weights['volatility']
        )

        # composite_score가 유효한 종목만 반환
        valid_scores = factors_df[factors_df['composite_score'].notna()]

        if valid_scores.empty:
            print(f"   ❌ 유효한 점수를 가진 종목이 없습니다")
            return factors_df.iloc[0:0]  # 빈 DataFrame

        return valid_scores.sort_values('composite_score', ascending=False)

    def backtest_with_dynamic_universe(self):
        """동적 유니버스 백테스팅 메인 로직"""
        print("\n🔄 백테스팅 실행 중...\n")

        # 리밸런싱 날짜 생성 (N일마다)
        current_date = self.start_date
        rebalance_dates = []

        while current_date <= self.end_date:
            rebalance_dates.append(current_date)
            current_date += timedelta(days=self.rebalance_days)

        print(f"리밸런싱 스케줄: {len(rebalance_dates)}회\n")

        for i, signal_date in enumerate(rebalance_dates):
            print(f"\n{'='*60}")
            print(f"[{i+1}/{len(rebalance_dates)}] 리밸런싱: {signal_date.date()}")
            print(f"{'='*60}")

            # 1. 유니버스 생성 (그날 기준)
            universe = self.get_universe_at_date(signal_date)

            if not universe:
                print("⚠️ 유니버스가 비어있습니다. 스킵")
                continue

            # 2. 팩터 계산 (그날까지의 데이터만)
            print(f"📊 팩터 계산 중... ({len(universe)}개 종목)")
            factors_df = self.calculate_factors_at_date(universe, signal_date)

            if factors_df.empty:
                print("⚠️ 팩터 계산 실패. 스킵")
                continue

            # 3. 스코어링 및 종목 선정
            factors_df = self.normalize_and_score(factors_df)
            selected = factors_df.head(self.top_n)
            selected_tickers = set(selected['ticker'].tolist())

            print(f"✅ 선정 종목: {len(selected_tickers)}개")

            # 4. 포트폴리오 리밸런싱
            self.rebalance_portfolio(signal_date, selected, selected_tickers)

            # 5. 포트폴리오 기록
            self.record_portfolio_snapshot(signal_date)

            # 6. 다음 리밸런싱까지 거래일마다 손절/트레일링/보유기간 만료 검사
            if self.risk_rules_enabled:
                next_date = rebalance_dates[i + 1] if i + 1 < len(rebalance_dates) else self.end_date
                self.apply_risk_rules(signal_date, next_date)

        print(f"\n{'='*80}")
        print("✅ 백테스팅 완료")
        print(f"{'='*80}\n")

    def rebalance_portfolio(self, date: datetime, selected_df: pd.DataFrame, selected_tickers: set):
        """포트폴리오 리밸런싱"""
        current_tickers = set(self.holdings.keys())
        to_sell = current_tickers - selected_tickers
        to_buy = selected_tickers - current_tickers

        print(f"\n📋 리밸런싱 계획:")
        print(f"   매도: {len(to_sell)}개")
        print(f"   매수: {len(to_buy)}개")
        print(f"   유지: {len(current_tickers & selected_tickers)}개")

        # 1. 매도
        for ticker in to_sell:
            self.sell_position(ticker, date, "리밸런싱 제외")

        # 2. 매수
        if len(to_buy) > 0 and self.cash > 0:
            position_size = self.cash / len(to_buy)

            for ticker in to_buy:
                row = selected_df[selected_df['ticker'] == ticker].iloc[0]
                price = row['price']

                if position_size >= config.MIN_ORDER_AMOUNT_USD:
                    self.buy_position(ticker, date, price, position_size)

    def buy_position(self, ticker: str, date: datetime, price: float, amount: float):
        """매수 실행"""
        shares = int(amount / price)
        if shares <= 0:
            return

        total_cost = shares * price
        commission = total_cost * self.commission
        total_payment = total_cost + commission

        if total_payment > self.cash:
            shares = int(self.cash / (price * (1 + self.commission)))
            total_cost = shares * price
            commission = total_cost * self.commission
            total_payment = total_cost + commission

        self.cash -= total_payment
        self.holdings[ticker] = {
            'shares': shares,
            'avg_price': price,
            'buy_date': date,
            'high_price': price,
        }

        # 거래 기록
        self.trades_history.append({
            'date': date,
            'ticker': ticker,
            'action': 'BUY',
            'shares': shares,
            'price': price,
            'amount': total_payment
        })

        # DB 기록
        if self.db_recording and self.db_manager:
            try:
                self.db_manager.record_buy(
                    ticker=ticker,
                    shares=shares,
                    price=price,
                    commission=commission,
                    notes=f"백테스트 매수 ({date.date()})"
                )
                self.db_manager.add_position(
                    ticker=ticker,
                    shares=shares,
                    avg_price=price,
                    buy_date=date
                )
            except Exception as e:
                print(f"⚠️ DB 기록 실패: {e}")

        print(f"   🛒 매수: {ticker} {shares}주 @ ${price:.2f}")

    def sell_position(self, ticker: str, date: datetime, reason: str):
        """매도 실행"""
        if ticker not in self.holdings:
            return

        pos = self.holdings[ticker]

        # 체결가: 신호일 이후 첫 거래일 종가 (캐시)
        current_price = self._price_on_or_after(ticker, date)
        if current_price is None:
            current_price = pos['avg_price']

        shares = pos['shares']
        total_value = shares * current_price
        commission = total_value * self.commission
        proceeds = total_value - commission

        self.cash += proceeds

        # P&L 계산
        pnl = (current_price - pos['avg_price']) * shares
        pnl_percent = ((current_price / pos['avg_price']) - 1) * 100
        days_held = (date - pos['buy_date']).days

        # 거래 기록
        self.trades_history.append({
            'date': date,
            'ticker': ticker,
            'action': 'SELL',
            'shares': shares,
            'price': current_price,
            'pnl': pnl,
            'pnl_percent': pnl_percent,
            'days_held': days_held,
            'exit_reason': reason
        })

        # DB 기록
        if self.db_recording and self.db_manager:
            try:
                self.db_manager.record_sell(
                    ticker=ticker,
                    shares=shares,
                    price=current_price,
                    buy_price=pos['avg_price'],
                    buy_date=pos['buy_date'],
                    commission=commission,
                    exit_reason=reason,
                    notes=f"백테스트 매도 ({date.date()})"
                )
                self.db_manager.remove_position(ticker)
            except Exception as e:
                print(f"⚠️ DB 기록 실패: {e}")

        del self.holdings[ticker]
        print(f"   💰 매도: {ticker} ({pnl_percent:+.2f}%) - {reason}")

    # ------------------------------------------------------------------ 리스크 규칙
    def _close_on(self, ticker: str, day: pd.Timestamp) -> Optional[float]:
        df = self._history(ticker)
        if df is None or day not in df.index:
            return None
        close = df.at[day, 'Close']
        return float(close) if pd.notna(close) else None

    def evaluate_exit(self, pos: dict, current_price: float, day: datetime) -> Optional[str]:
        """실봇 check_risk_management 와 같은 판정. 매도 사유 키 또는 None."""
        if current_price > pos.get('high_price', 0):
            pos['high_price'] = current_price
        profit_pct = (current_price / pos['avg_price'] - 1) * 100
        from_high_pct = (current_price / pos['high_price'] - 1) * 100
        hold_days = (pd.Timestamp(day) - pd.Timestamp(pos['buy_date'])).days
        min_hold_met = hold_days >= self.min_hold_days

        if min_hold_met and self.stop_loss_enabled and profit_pct <= self.stop_loss_pct:
            return 'STOP_LOSS'
        if min_hold_met and self.trailing_enabled and from_high_pct <= self.trailing_pct:
            return 'TRAILING_STOP'
        if hold_days >= self.max_hold_days:
            return 'MAX_HOLD'
        return None

    def apply_risk_rules(self, start: datetime, end: datetime) -> int:
        """(start, end] 구간의 거래일마다 보유 종목을 검사해 매도. 반환: 매도 건수"""
        if not self.holdings:
            return 0
        days = set()
        for ticker in list(self.holdings):
            df = self._history(ticker)
            if df is not None:
                days.update(df.index[(df.index > pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))])
        sold = 0
        for day in sorted(days):
            for ticker in list(self.holdings):
                price = self._close_on(ticker, day)
                if price is None:
                    continue
                reason = self.evaluate_exit(self.holdings[ticker], price, day)
                if reason:
                    self.sell_position(ticker, day.to_pydatetime(), reason)
                    sold += 1
        if sold:
            print(f"   🛡️ 리스크 규칙 매도 {sold}건 ({start.date()} ~ {end.date()})")
        return sold

    def record_portfolio_snapshot(self, date: datetime):
        """포트폴리오 스냅샷 기록"""
        positions_value = 0

        for ticker, pos in self.holdings.items():
            try:
                current_price = self._price_on_or_after(ticker, date)
                if current_price is None:
                    raise ValueError('no price')

                positions_value += pos['shares'] * current_price

                # DB에 현재가 업데이트
                if self.db_recording and self.db_manager:
                    self.db_manager.update_position_price(ticker, current_price)
            except:
                positions_value += pos['shares'] * pos['avg_price']

        total_value = self.cash + positions_value
        daily_return = 0.0  # 일일 수익률은 이전 스냅샷과 비교 필요

        if self.portfolio_history:
            prev_value = self.portfolio_history[-1]['total_value']
            daily_return = ((total_value - prev_value) / prev_value) * 100

        cumulative_return = ((total_value - self.initial_capital) / self.initial_capital) * 100

        snapshot = {
            'date': date,
            'cash': self.cash,
            'positions_value': positions_value,
            'total_value': total_value,
            'daily_return': daily_return,
            'cumulative_return': cumulative_return,
            'num_positions': len(self.holdings)
        }

        self.portfolio_history.append(snapshot)

        # DB 기록
        if self.db_recording and self.db_manager:
            try:
                self.db_manager.save_snapshot(snapshot_date=date)
            except Exception as e:
                print(f"⚠️ 스냅샷 DB 기록 실패: {e}")

    def analyze_performance(self):
        """성과 분석 및 리포트 생성"""
        if not self.portfolio_history:
            print("포트폴리오 데이터가 없습니다.")
            return

        # backtest_result 폴더 생성
        result_dir = Path(__file__).parent.parent.parent.parent / 'backtest_result'
        result_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        # 기본 통계 계산
        final_value = self.portfolio_history[-1]['total_value']
        total_return = ((final_value - self.initial_capital) / self.initial_capital) * 100

        # CAGR 계산
        days = (self.end_date - self.start_date).days
        years = days / 365.25
        cagr = (pow(final_value / self.initial_capital, 1 / years) - 1) * 100 if years > 0 else 0

        # 거래 통계
        buys = [t for t in self.trades_history if t.get('action') == 'BUY']
        sells = [t for t in self.trades_history if t.get('action') == 'SELL']

        buy_count = len(buys)
        sell_count = len(sells)

        win_rate = 0.0
        avg_return = 0.0
        avg_hold_days = 0.0
        max_gain = 0.0
        max_loss = 0.0

        if sells:
            winning = len([t for t in sells if t['pnl'] > 0])
            win_rate = (winning / len(sells)) * 100
            avg_return = np.mean([t['pnl_percent'] for t in sells])
            avg_hold_days = np.mean([t['days_held'] for t in sells])
            max_gain = max([t['pnl_percent'] for t in sells])
            max_loss = min([t['pnl_percent'] for t in sells])

        # 청산 사유별 통계
        exit_reasons = {}
        exit_reason_returns = {}

        for t in sells:
            reason = t.get('exit_reason', 'UNKNOWN')
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
            if reason not in exit_reason_returns:
                exit_reason_returns[reason] = []
            exit_reason_returns[reason].append(t['pnl_percent'])

        # 연도별 수익률 계산
        df_portfolio = pd.DataFrame(self.portfolio_history)
        df_portfolio['year'] = pd.to_datetime(df_portfolio['date']).dt.year

        yearly_returns = {}
        for year in sorted(df_portfolio['year'].unique()):
            year_data = df_portfolio[df_portfolio['year'] == year]
            if len(year_data) > 0:
                year_start_value = year_data.iloc[0]['total_value']
                year_end_value = year_data.iloc[-1]['total_value']

                # 작년 말 자산 찾기
                prev_year_data = df_portfolio[df_portfolio['year'] == year - 1]
                if len(prev_year_data) > 0:
                    prev_year_end_value = prev_year_data.iloc[-1]['total_value']
                else:
                    prev_year_end_value = self.initial_capital

                year_return = ((year_end_value - prev_year_end_value) / prev_year_end_value) * 100
                yearly_returns[year] = {'return': year_return, 'value': year_end_value}

        # 리포트 생성
        lines = []
        lines.append("=" * 80)
        lines.append(f"기간           : {self.start_date.date()} ~ {self.end_date.date()}")
        lines.append(f"초기 자산      : ${self.initial_capital:,.2f}")
        lines.append(f"최종 자산      : ${final_value:,.2f}")
        lines.append(f"총 수익률      : {total_return:+.2f}%")
        lines.append(f"연평균(CAGR)   : {cagr:+.2f}%")
        lines.append("-" * 80)
        lines.append(f"매수 횟수      : {buy_count}회")
        lines.append(f"매도 횟수      : {sell_count}회")
        lines.append(f"승률           : {win_rate:.1f}%")
        lines.append(f"평균 수익률    : {avg_return:+.2f}%")
        lines.append(f"평균 보유일    : {avg_hold_days:.1f}일")
        lines.append(f"최대 수익      : {max_gain:+.2f}%")
        lines.append(f"최대 손실      : {max_loss:+.2f}%")
        lines.append("-" * 80)

        # 청산 사유별 통계
        lines.append("[청산 사유별 통계]")
        total_exits = sum(exit_reasons.values())
        for reason, count in sorted(exit_reasons.items(), key=lambda x: x[1], reverse=True):
            percentage = (count / total_exits) * 100 if total_exits > 0 else 0

            # 한글 사유 매핑
            reason_map = {
                '리밸런싱 제외': '기술적 신호 (리밸런싱)',
                'REBALANCE': '기술적 신호 (리밸런싱)',
                'STOP_LOSS': '손절',
                'TRAILING_STOP': '트레일링 스탑',
                'MAX_HOLD': '최대 보유기간',
            }
            display_reason = reason_map.get(reason, reason)
            lines.append(f"{display_reason} : {count}회 ({percentage:.1f}%)")

        lines.append("")
        lines.append("[청산 사유별 평균 수익률]")
        for reason, returns_list in sorted(exit_reason_returns.items(), key=lambda x: len(x[1]), reverse=True):
            avg = np.mean(returns_list)
            count = len(returns_list)

            # 사유를 간략히 표시 (영문 약자)
            reason_short_map = {
                '리밸런싱 제외': 'SIGNAL',
                'REBALANCE': 'SIGNAL',
                'STOP_LOSS': 'STOP_LOSS',
                'TRAILING_STOP': 'TRAIL_STOP',
                'MAX_HOLD': 'MAX_HOLD',
            }
            short_reason = reason_short_map.get(reason, reason)
            lines.append(f"{short_reason:<14} : {avg:+.2f}% ({count}회)")

        lines.append("-" * 80)
        lines.append("[연도별 수익률]")
        for year in sorted(yearly_returns.keys()):
            data = yearly_returns[year]
            lines.append(f"{year}년         : {data['return']:+.2f}%  (자산: ${data['value']:,.2f})")

        lines.append("=" * 80)

        # 콘솔 출력
        print("\n" + "\n".join(lines))

        # 로그 파일 저장
        log_file = result_dir / f"log_{timestamp}.txt"
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))
        print(f"\n💾 리포트 저장: {log_file}")

        # 거래 내역 CSV 저장
        if self.trades_history:
            df_trades = pd.DataFrame(self.trades_history)
            trades_file = result_dir / f"trades_{timestamp}.csv"
            df_trades.to_csv(trades_file, index=False, encoding='utf-8-sig')
            print(f"💾 거래 내역 저장: {trades_file}")

        # 포트폴리오 이력 CSV 저장
        portfolio_file = result_dir / f"portfolio_{timestamp}.csv"
        df_portfolio.to_csv(portfolio_file, index=False, encoding='utf-8-sig')
        print(f"💾 포트폴리오 이력 저장: {portfolio_file}")
