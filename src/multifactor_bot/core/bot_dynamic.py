# bot_dynamic.py - 동적 스크리닝 멀티팩터 거래 봇

import sys
import time
import warnings
from datetime import datetime, timedelta
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
from multifactor_bot.brokers.kis_data_provider import KISDataProvider
from multifactor_bot.brokers.kis_trader import KISTrader

# 텔레그램 알림 (선택적)
try:
    import requests
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False


class DynamicMultiFactorBot:
    """
    동적 스크리닝 멀티팩터 거래 봇 (실제 거래용)
    - S&P500 + NASDAQ100에서 실시간 종목 선정
    - 멀티팩터 분석으로 포트폴리오 구성
    - 정기 리밸런싱 (설정 주기마다)
    """

    def __init__(self, dry_run: bool = True):
        """
        Args:
            dry_run: True면 모의 실행, False면 실제 거래
        """
        self.config = config
        self.dry_run = dry_run

        # 데이터 제공자
        self.data_provider = KISDataProvider(use_yfinance=True, config=config)
        print("📊 데이터 제공자 초기화 (yfinance + KIS API 백업)")

        # 동적 스크리너
        self.screener = DynamicScreener(
            min_market_cap=config.DYNAMIC_SCREENING_MIN_MARKET_CAP,
            min_price=config.DYNAMIC_SCREENING_MIN_PRICE,
            min_avg_volume=config.DYNAMIC_SCREENING_MIN_VOLUME,
            max_workers=5  # Rate limit 방지
        )
        print("🔍 동적 스크리너 초기화")

        # 거래 실행자
        if not dry_run:
            self.trader = KISTrader(config=config)
            print("💼 KIS Trader 초기화 (실제 거래 모드)")
        else:
            self.trader = None
            print("🎯 모의 실행 모드 (DRY RUN)")

        # 팩터 가중치
        self.weights = config.FACTOR_WEIGHTS
        self.top_n = config.TOP_N_STOCKS

        # 마지막 리밸런싱 시간
        self.last_rebalance = None

    def send_telegram(self, message: str):
        """텔레그램 알림 전송"""
        if not TELEGRAM_AVAILABLE:
            return

        try:
            url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
            data = {
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML"
            }
            requests.post(url, data=data, timeout=5)
        except Exception as e:
            print(f"⚠️ 텔레그램 전송 실패: {e}")

    def get_current_universe(self) -> List[str]:
        """현재 시점의 유니버스 생성 (동적 스크리닝)"""
        print("\n" + "="*60)
        print("🔍 동적 스크리닝 실행 중...")
        print("="*60)

        try:
            universe = self.screener.screen_universe(
                reference_date=datetime.now(),
                max_tickers=config.DYNAMIC_SCREENING_MAX_TICKERS
            )

            if universe and len(universe) > 0:
                print(f"✅ {len(universe)}개 종목 선정")
                return universe
            else:
                print(f"⚠️ 동적 스크리닝 결과 없음 → 고정 유니버스로 폴백")
                return config.WATCHLIST

        except Exception as e:
            print(f"❌ 동적 스크리닝 오류 ({e}) → 고정 유니버스로 폴백")
            return config.WATCHLIST

    def calculate_factors(self, universe: List[str]) -> pd.DataFrame:
        """현재 시점의 팩터 계산"""
        print(f"\n📊 팩터 계산 중... ({len(universe)}개 종목)")
        factors = []

        for ticker in universe:
            try:
                # 최근 1년 데이터
                end_date = datetime.now().strftime('%Y-%m-%d')
                start_date = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')

                df = self.data_provider.download(
                    ticker=ticker,
                    start=start_date,
                    end=end_date,
                    period='1y'
                )

                if df is None or df.empty or len(df) < 126:
                    continue

                # 가격
                price = float(df['Close'].iloc[-1])

                # stock.info는 선택적 (401 에러 방지)
                value_factor = np.nan
                quality_factor = np.nan

                try:
                    stock = yf.Ticker(ticker)
                    info = stock.info
                    value_factor = self._calculate_value(info)
                    quality_factor = self._calculate_quality(info)
                except:
                    pass

                factors.append({
                    'ticker': ticker,
                    'price': price,
                    'momentum': self._calculate_momentum(df),
                    'value': value_factor,
                    'quality': quality_factor,
                    'volatility': self._calculate_volatility(df)
                })

            except Exception as e:
                continue

        df_factors = pd.DataFrame(factors)
        print(f"✅ {len(df_factors)}개 종목 팩터 계산 완료")
        return df_factors

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

    def normalize_and_score(self, factors_df: pd.DataFrame) -> pd.DataFrame:
        """팩터 정규화 및 종합 점수 계산"""
        if factors_df.empty:
            return factors_df

        factor_cols = ['momentum', 'value', 'quality', 'volatility']

        for col in factor_cols:
            factors_df[col] = pd.to_numeric(factors_df[col], errors='coerce')

        for col in factor_cols:
            mean = factors_df[col].mean(skipna=True)
            std = factors_df[col].std(skipna=True)

            if pd.notna(std) and std > 0:
                factors_df[f'{col}_z'] = (factors_df[col] - mean) / std
            else:
                factors_df[f'{col}_z'] = 0

            # NaN을 0으로 채우기
            factors_df[f'{col}_z'] = factors_df[f'{col}_z'].fillna(0)

        factors_df['composite_score'] = (
            factors_df['momentum_z'] * self.weights['momentum'] +
            factors_df['value_z'] * self.weights['value'] +
            factors_df['quality_z'] * self.weights['quality'] +
            factors_df['volatility_z'] * self.weights['volatility']
        )

        valid_scores = factors_df[factors_df['composite_score'].notna()]
        return valid_scores.sort_values('composite_score', ascending=False)

    def get_current_holdings(self) -> Dict[str, int]:
        """현재 보유 종목 조회"""
        if self.dry_run:
            return {}

        try:
            # KIS API로 보유 종목 조회하는 로직 필요
            # 현재는 간단히 빈 dict 반환
            return {}
        except Exception as e:
            print(f"⚠️ 보유 종목 조회 실패: {e}")
            return {}

    def rebalance(self):
        """포트폴리오 리밸런싱"""
        print("\n" + "="*80)
        print(f"🔄 리밸런싱 시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*80)

        # 1. 동적 유니버스 생성
        universe = self.get_current_universe()
        if not universe:
            print("❌ 유니버스가 비어있습니다. 리밸런싱 중단")
            return

        # 2. 팩터 계산
        factors_df = self.calculate_factors(universe)
        if factors_df.empty:
            print("❌ 팩터 계산 실패. 리밸런싱 중단")
            return

        # 3. 스코어링 및 종목 선정
        factors_df = self.normalize_and_score(factors_df)
        selected = factors_df.head(self.top_n)

        print(f"\n✅ 선정 종목 ({len(selected)}개):")
        print("-" * 80)
        for idx, row in selected.iterrows():
            print(f"  {row['ticker']:6s} | Score: {row['composite_score']:+.2f} | "
                  f"Mom: {row['momentum']:+.1f}% | Vol: {row['volatility']:.2f}")

        # 4. 현재 보유 종목 조회
        current_holdings = self.get_current_holdings()
        current_tickers = set(current_holdings.keys())
        selected_tickers = set(selected['ticker'].tolist())

        to_sell = current_tickers - selected_tickers
        to_buy = selected_tickers - current_tickers

        print(f"\n📋 리밸런싱 계획:")
        print(f"   매도: {len(to_sell)}개 - {list(to_sell)}")
        print(f"   매수: {len(to_buy)}개 - {list(to_buy)}")
        print(f"   유지: {len(current_tickers & selected_tickers)}개")

        # 5. 실제 거래 실행
        if self.dry_run:
            print("\n🎯 [DRY RUN] 실제 거래는 실행되지 않습니다")
            message = f"🤖 <b>멀티팩터 봇 (DRY RUN)</b>\n\n"
            message += f"선정 종목: {len(selected)}개\n"
            message += f"매도: {len(to_sell)}개, 매수: {len(to_buy)}개\n\n"
            message += "선정 종목:\n" + "\n".join([f"  • {t}" for t in list(selected_tickers)[:10]])
        else:
            # 매도 실행
            for ticker in to_sell:
                quantity = current_holdings[ticker]
                result = self.trader.sell_stock(ticker, quantity, dry_run=False)
                print(f"   💰 매도: {ticker} {quantity}주 - {result['message']}")
                time.sleep(0.5)

            # 잔고 조회
            balance = self.trader.get_balance()
            available_cash = balance['available_cash'] if balance else 0
            if balance is None:
                print("⚠️ KIS 잔고 조회 실패 → 매수 건너뜀")

            # 매수 실행
            if len(to_buy) > 0 and available_cash > 0:
                position_size = available_cash / len(to_buy)

                for ticker in to_buy:
                    row = selected[selected['ticker'] == ticker].iloc[0]
                    price = row['price']
                    quantity = int(position_size / price)

                    if quantity > 0:
                        result = self.trader.buy_stock(ticker, quantity, dry_run=False)
                        print(f"   🛒 매수: {ticker} {quantity}주 @ ${price:.2f} - {result['message']}")
                        time.sleep(0.5)

            message = f"🤖 <b>멀티팩터 봇</b>\n\n"
            message += f"✅ 리밸런싱 완료\n"
            message += f"매도: {len(to_sell)}개, 매수: {len(to_buy)}개\n\n"
            message += "현재 포트폴리오:\n" + "\n".join([f"  • {t}" for t in list(selected_tickers)[:10]])

        self.send_telegram(message)
        self.last_rebalance = datetime.now()
        print("\n✅ 리밸런싱 완료")

    def should_rebalance(self) -> bool:
        """리밸런싱이 필요한지 확인"""
        if self.last_rebalance is None:
            return True

        days_since = (datetime.now() - self.last_rebalance).days
        return days_since >= config.REBALANCE_PERIOD_DAYS

    def run(self):
        """봇 메인 루프"""
        print("="*80)
        print("🤖 동적 스크리닝 멀티팩터 거래 봇 시작")
        print("="*80)
        print(f"모드: {'🎯 DRY RUN (모의 실행)' if self.dry_run else '💼 LIVE (실제 거래)'}")
        print(f"리밸런싱 주기: {config.REBALANCE_PERIOD_DAYS}일")
        print(f"스캔 간격: {config.SCAN_INTERVAL}초")
        print("="*80)

        self.send_telegram(
            f"🤖 <b>동적 스크리닝 멀티팩터 봇 시작</b>\n\n"
            f"모드: {'DRY RUN' if self.dry_run else 'LIVE'}\n"
            f"리밸런싱 주기: {config.REBALANCE_PERIOD_DAYS}일"
        )

        while True:
            try:
                if self.should_rebalance():
                    self.rebalance()
                else:
                    days_until = config.REBALANCE_PERIOD_DAYS - (datetime.now() - self.last_rebalance).days
                    print(f"⏰ 다음 리밸런싱까지 {days_until}일 남음")

                print(f"\n💤 {config.SCAN_INTERVAL}초 대기 중...")
                time.sleep(config.SCAN_INTERVAL)

            except KeyboardInterrupt:
                print("\n\n⚠️ 봇 종료 요청")
                self.send_telegram("🛑 <b>봇 종료</b>")
                break
            except Exception as e:
                error_msg = f"❌ 오류 발생: {e}"
                print(error_msg)
                self.send_telegram(f"🚨 <b>오류</b>\n\n{e}")
                time.sleep(60)  # 오류 발생 시 1분 대기


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='동적 스크리닝 멀티팩터 거래 봇')
    parser.add_argument(
        '--live',
        action='store_true',
        help='실제 거래 모드 (기본은 DRY RUN)'
    )
    args = parser.parse_args()

    bot = DynamicMultiFactorBot(dry_run=not args.live)
    bot.run()