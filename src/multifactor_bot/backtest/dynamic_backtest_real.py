# dynamic_backtest_real.py  ← 새 파일명 추천 (실전 모드 전용)

import sys
import warnings
import logging
from datetime import timedelta
from typing import List, Dict, Optional

import pandas as pd
import pandas_market_calendars as mcal

warnings.filterwarnings('ignore')

# yfinance 에러 메시지 억제
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

try:
  import yfinance as yf
except ImportError:
  print("⚠️ yfinance 미설치: pip install yfinance")
  sys.exit(1)

from multifactor_bot import config
from multifactor_bot.screening.dynamic_screener import DynamicScreener
from multifactor_bot.brokers.kis_data_provider import KISDataProvider


class RealWorldDynamicBacktest:
  """진짜 실전처럼 정직한 동적 유니버스 백테스트 (Look-ahead bias 완전 제거)"""

  def __init__(self,
      use_dynamic_screening: bool = True,
      slippage_bps: float = 5.0,  # 5bp 슬리피지 기본
      db_recording: bool = False,
      account_id: int = None):

    self.config = config
    self.start_date = pd.to_datetime(config.BACKTEST_START_DATE)
    self.end_date = pd.to_datetime(config.BACKTEST_END_DATE)
    self.initial_capital = config.BACKTEST_INITIAL_CAPITAL
    self.commission = config.BACKTEST_COMMISSION
    self.slippage_bps = slippage_bps / 10000  # 5bp → 0.0005
    self.rebalance_days = config.REBALANCE_PERIOD_DAYS
    self.top_n = config.TOP_N_STOCKS
    self.weights = config.FACTOR_WEIGHTS

    # 미국 거래일 캘린더 (NYSE)
    self.nyse = mcal.get_calendar('NYSE')

    self.data_provider = KISDataProvider(use_yfinance=True, config=config)
    self.screener = DynamicScreener(
        min_market_cap=config.DYNAMIC_SCREENING_MIN_MARKET_CAP,
        min_price=config.DYNAMIC_SCREENING_MIN_PRICE,
        min_avg_volume=config.DYNAMIC_SCREENING_MIN_VOLUME,
        max_workers=config.DYNAMIC_SCREENING_WORKERS
    ) if use_dynamic_screening else None

    # 상태
    self.cash = self.initial_capital
    self.holdings: Dict[str, Dict] = {}
    self.trades_history = []
    self.portfolio_history = []

    # 가격 데이터 캐시 (배치 다운로드 결과 재사용)
    self._price_cache: Dict[str, pd.DataFrame] = {}

    # DB 관련 (기존과 동일)
    self.db_recording = db_recording
    self.db_manager = None
    if db_recording:
      try:
        from multifactor_bot.database.paper_trading import PaperTradingManager
        self.db_manager = PaperTradingManager(
            account_id=account_id or config.PAPER_ACCOUNT_ID)
      except Exception as e:
        print(f"DB 초기화 실패: {e}")
        self.db_recording = False

  def is_trading_day(self, date: pd.Timestamp) -> bool:
    """미국 NYSE 거래일인지 확인"""
    schedule = self.nyse.schedule(start_date=date, end_date=date)
    return len(schedule) > 0

  def get_next_trading_day(self, date: pd.Timestamp) -> pd.Timestamp:
    """다음 거래일 반환"""
    schedule = self.nyse.schedule(start_date=date + timedelta(days=1),
                                  end_date=date + timedelta(days=30))
    if not schedule.empty:
      return schedule.index[0]  # index는 이미 Timestamp
    return date + timedelta(days=1)

  def get_universe_at_date(self, date: pd.Timestamp) -> List[str]:
    """매 리밸런싱마다 완전히 새로 스크리닝 (캐시 없음!)"""
    # 동적 스크리닝 비활성화 시 바로 고정 유니버스 반환
    if self.screener is None:
      print(f"   📋 고정 WATCHLIST 사용 ({len(config.WATCHLIST)}개)")
      return config.WATCHLIST

    print(f"\n🔍 [{date.date()}] 동적 유니버스 재생성 중...")
    try:
      universe = self.screener.screen_universe(
          reference_date=date,
          max_tickers=config.DYNAMIC_SCREENING_MAX_TICKERS
      )
      if universe and len(universe) >= self.top_n:
        # 충분한 종목이 있으면 그대로 사용
        print(f"   ✅ 동적 스크리닝 성공: {len(universe)}개 종목")
        return universe
      elif universe and len(universe) > 0:
        # 종목이 있지만 top_n보다 적으면 WATCHLIST와 합침
        combined = list(set(universe + config.WATCHLIST))
        print(f"   ⚠️ 동적 스크리닝 부분 성공: {len(universe)}개 + WATCHLIST 보완 → {len(combined)}개")
        return combined
    except Exception as e:
      print(f"   스크리닝 실패 ({e}) → 고정 WATCHLIST 폴백")

    print("   폴백: 고정 WATCHLIST 사용")
    return config.WATCHLIST

  def get_price_at_date(self, ticker: str, target_date: pd.Timestamp,
      side: str = 'close') -> Optional[float]:
    """target_date 또는 그 이후 첫 거래일의 가격 반환 (캐시 우선)"""
    # 1. 캐시에서 먼저 확인
    if ticker in self._price_cache:
      df = self._price_cache[ticker]
      if df is not None and not df.empty:
        df_after = df[df.index >= target_date]
        if not df_after.empty:
          price = float(df_after.iloc[0]['Close'])
          if side == 'buy':
            price *= (1 + self.slippage_bps)
          elif side == 'sell':
            price *= (1 - self.slippage_bps)
          return round(price, 6)

    # 2. 캐시에 없으면 개별 다운로드
    df = self.data_provider.download(
        ticker=ticker,
        start=(target_date - timedelta(days=30)).strftime('%Y-%m-%d'),
        end=(target_date + timedelta(days=30)).strftime('%Y-%m-%d')
    )
    if df is None or df.empty:
      return None

    df_after = df[df.index >= target_date]
    if df_after.empty:
      return None

    price = float(df_after.iloc[0]['Close'])

    # 슬리피지 적용
    if side == 'buy':
      price *= (1 + self.slippage_bps)  # 매수: 더 비싸게
    elif side == 'sell':
      price *= (1 - self.slippage_bps)  # 매도: 더 싸게

    return round(price, 6)

  def run(self):
    print("=" * 90)
    print("진짜 실전 수준 동적 백테스트 (Look-ahead bias 완전 제거)")
    print("=" * 90)

    current = self.start_date
    rebalance_dates = []

    while current <= self.end_date:
      if self.is_trading_day(current):
        rebalance_dates.append(current)
      current += timedelta(days=self.rebalance_days)

    print(f"총 리밸런싱: {len(rebalance_dates)}회")

    for i, signal_date in enumerate(rebalance_dates):
      print(f"\n{'=' * 70}")
      print(f"[{i + 1}/{len(rebalance_dates)}] 리밸런싱 신호: {signal_date.date()}")
      print(f"{'=' * 70}")

      # 다음 거래일에 실제 체결
      execution_date = self.get_next_trading_day(signal_date)
      if execution_date > self.end_date:
        break
      print(f"실제 체결일: {execution_date.date()} (다음 거래일)")

      # 1. 유니버스 완전 재생성
      universe = self.get_universe_at_date(signal_date)

      # 2. 팩터 계산 (signal_date까지 데이터만)
      factors_df = self.calculate_factors_at_date(universe, signal_date)
      if factors_df is None or factors_df.empty:
        print(f"   ⚠️ 팩터 계산 결과 없음, 스킵")
        continue

      print(f"   📈 팩터 계산 결과: {len(factors_df)}개 종목")

      # 3. 스코어링 및 선정
      scored = self.normalize_and_score(factors_df)
      if scored is None or scored.empty:
        print(f"   ⚠️ 스코어링 결과 없음, 스킵")
        continue

      selected = scored.head(self.top_n)
      selected_tickers = set(selected['ticker'].tolist())
      print(f"   🎯 선정 종목: {len(selected_tickers)}개 - {list(selected_tickers)[:5]}...")

      # 4. 포트폴리오 리밸런싱 (execution_date 가격으로)
      self.rebalance_portfolio(execution_date, selected, selected_tickers)

      # 5. 스냅샷 기록
      self.record_portfolio_snapshot(execution_date)

    self.analyze_performance()

  # calculate_factors_at_date, normalize_and_score 등은 기존 코드와 거의 동일
  # (단, df_until = df[df.index <= signal_date] 는 그대로 유지)

  def rebalance_portfolio(self, execution_date: pd.Timestamp,
      selected_df: pd.DataFrame, selected_tickers: set):
    current_tickers = set(self.holdings.keys())
    to_sell = current_tickers - selected_tickers
    to_buy = selected_tickers - current_tickers

    print(
        f"매도: {len(to_sell)}, 매수: {len(to_buy)}, 유지: {len(current_tickers & selected_tickers)}")

    # 1. 매도 (execution_date 가격으로)
    for ticker in to_sell:
      self.sell_position(ticker, execution_date, "리밸런싱 제외")

    # 2. 매수
    if to_buy and self.cash > self.initial_capital * 0.01:  # 최소 현금 보유
      cash_per_stock = self.cash / len(to_buy)
      print(f"   💵 매수 예산: ${cash_per_stock:,.2f}/종목 (현금: ${self.cash:,.2f})")
      bought_count = 0
      for ticker in to_buy:
        price = self.get_price_at_date(ticker, execution_date, side='buy')
        if price is None:
          print(f"   ⚠️ {ticker}: 가격 조회 실패 (execution_date={execution_date.date()})")
          continue
        if cash_per_stock < 1000:
          print(f"   ⚠️ {ticker}: 매수 예산 부족 (${cash_per_stock:,.2f} < $1,000)")
          continue
        self.buy_position(ticker, execution_date, price, cash_per_stock)
        bought_count += 1
      if bought_count == 0:
        print(f"   ❌ 매수 실패: 가격 조회 불가 또는 예산 부족")

  # buy_position, sell_position, record_portfolio_snapshot, analyze_performance 등
  # → 기존 로직 유지하되, 가격 조회 시 self.get_price_at_date(..., execution_date) 사용

  def calculate_factors_at_date(self, universe: List[str],
      date: pd.Timestamp) -> pd.DataFrame:
    """특정 날짜의 팩터 계산 (배치 다운로드로 Rate Limit 회피)"""
    import numpy as np
    import time

    factors = []
    no_data_count = 0
    insufficient_data_count = 0

    start_date = (date - timedelta(days=400)).strftime('%Y-%m-%d')
    end_date = (date + timedelta(days=7)).strftime('%Y-%m-%d')

    print(f"   📊 {len(universe)}개 종목 배치 다운로드 중...")

    # 배치 다운로드 (yfinance는 여러 종목 한 번에 지원)
    max_retries = 3
    all_data = None

    for attempt in range(max_retries):
      try:
        all_data = yf.download(
            tickers=universe,
            start=start_date,
            end=end_date,
            group_by='ticker',
            progress=False,
            threads=True
        )
        if all_data is not None and not all_data.empty:
          break
      except Exception as e:
        print(f"   ⚠️ 배치 다운로드 실패 (시도 {attempt + 1}/{max_retries}): {e}")
        if attempt < max_retries - 1:
          time.sleep(5 * (attempt + 1))  # 점진적 대기

    if all_data is None or all_data.empty:
      print(f"   ❌ 배치 다운로드 완전 실패")
      return pd.DataFrame()

    # 단일 종목인 경우 컬럼 구조가 다름
    is_single_ticker = len(universe) == 1

    for ticker in universe:
      try:
        # 단일 종목 vs 다중 종목 처리
        if is_single_ticker:
          df = all_data.copy()
        else:
          if ticker not in all_data.columns.get_level_values(0):
            no_data_count += 1
            continue
          df = all_data[ticker].copy()

        if df is None or df.empty or df['Close'].isna().all():
          no_data_count += 1
          continue

        # NaN 행 제거
        df = df.dropna(subset=['Close'])

        # 캐시에 저장 (매수/매도 시 재사용)
        self._price_cache[ticker] = df

        if len(df) < 126:
          insufficient_data_count += 1
          continue

        # 그날까지의 데이터만 사용
        df_until = df[df.index <= date]

        if len(df_until) < 126:
          insufficient_data_count += 1
          continue

        price = float(df_until['Close'].iloc[-1])

        # value/quality는 현재 info 기반 (백테스트에서는 momentum/volatility 위주로)
        # info 조회는 rate limit 유발하므로 스킵
        value_factor = np.nan
        quality_factor = np.nan

        factors.append({
            'ticker': ticker,
            'price': price,
            'momentum': self._calculate_momentum(df_until),
            'value': value_factor,
            'quality': quality_factor,
            'volatility': self._calculate_volatility(df_until)
        })
      except Exception:
        continue

    success_count = len(factors)
    print(f"   ✅ 팩터 계산 완료: {success_count}개 성공, {no_data_count}개 데이터없음, {insufficient_data_count}개 데이터부족")

    if len(factors) == 0:
      print(f"   ❌ 유효한 팩터 데이터가 없습니다")

    return pd.DataFrame(factors)

  def _calculate_momentum(self, df):
    import numpy as np
    try:
      period = min(config.MOMENTUM_PERIOD, len(df) - 1)
      return (df['Close'].iloc[-1] / df['Close'].iloc[-period] - 1) * 100
    except:
      return np.nan

  def _calculate_value(self, info):
    import numpy as np
    try:
      pe = info.get('forwardPE', info.get('trailingPE', np.nan))
      pb = info.get('priceToBook', np.nan)
      if pd.isna(pe) or pd.isna(pb) or pe <= 0 or pb <= 0:
        return np.nan
      return (1 / pe + 1 / pb) / 2
    except:
      return np.nan

  def _calculate_quality(self, info):
    import numpy as np
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
    import numpy as np
    try:
      period = min(config.VOLATILITY_PERIOD, len(df))
      returns = df['Close'].pct_change().dropna().tail(period)
      vol = returns.std() * np.sqrt(252) * 100
      return 100 / vol if vol > 0 else np.nan
    except:
      return np.nan

  def normalize_and_score(self, factors_df):
    """팩터 정규화 및 종합 점수 계산"""
    if factors_df is None or factors_df.empty:
      return pd.DataFrame()

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

      factors_df[f'{col}_z'] = factors_df[f'{col}_z'].fillna(0)

    factors_df['composite_score'] = (
        factors_df['momentum_z'] * self.weights['momentum'] +
        factors_df['value_z'] * self.weights['value'] +
        factors_df['quality_z'] * self.weights['quality'] +
        factors_df['volatility_z'] * self.weights['volatility']
    )

    valid_scores = factors_df[factors_df['composite_score'].notna()]

    if valid_scores.empty:
      print(f"   ❌ 유효한 점수를 가진 종목이 없습니다")
      return pd.DataFrame()

    return valid_scores.sort_values('composite_score', ascending=False)

  def buy_position(self, ticker: str, date: pd.Timestamp, price: float, amount: float):
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

    if shares <= 0:
      return

    self.cash -= total_payment
    self.holdings[ticker] = {
        'shares': shares,
        'avg_price': price,
        'buy_date': date
    }

    self.trades_history.append({
        'date': date,
        'ticker': ticker,
        'action': 'BUY',
        'shares': shares,
        'price': price,
        'amount': total_payment
    })

    print(f"   🛒 매수: {ticker} {shares}주 @ ${price:.2f}")

  def sell_position(self, ticker: str, date: pd.Timestamp, reason: str):
    """매도 실행"""
    if ticker not in self.holdings:
      return

    pos = self.holdings[ticker]
    current_price = self.get_price_at_date(ticker, date, side='sell')

    if current_price is None:
      current_price = pos['avg_price']

    shares = pos['shares']
    total_value = shares * current_price
    commission = total_value * self.commission
    proceeds = total_value - commission

    self.cash += proceeds

    pnl = (current_price - pos['avg_price']) * shares
    pnl_percent = ((current_price / pos['avg_price']) - 1) * 100
    days_held = (date - pos['buy_date']).days

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

    del self.holdings[ticker]
    print(f"   💰 매도: {ticker} ({pnl_percent:+.2f}%) - {reason}")

  def record_portfolio_snapshot(self, date: pd.Timestamp):
    """포트폴리오 스냅샷 기록"""
    positions_value = 0

    for ticker, pos in self.holdings.items():
      current_price = self.get_price_at_date(ticker, date)
      if current_price:
        positions_value += pos['shares'] * current_price
      else:
        positions_value += pos['shares'] * pos['avg_price']

    total_value = self.cash + positions_value
    daily_return = 0.0

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

  def analyze_performance(self):
    """성과 분석 및 리포트 생성"""
    import numpy as np
    from datetime import datetime
    from pathlib import Path

    if not self.portfolio_history:
      print("포트폴리오 데이터가 없습니다.")
      return

    result_dir = Path(__file__).parent.parent.parent.parent / 'backtest_result'
    result_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    final_value = self.portfolio_history[-1]['total_value']
    total_return = ((final_value - self.initial_capital) / self.initial_capital) * 100

    days = (self.end_date - self.start_date).days
    years = days / 365.25
    cagr = (pow(final_value / self.initial_capital, 1 / years) - 1) * 100 if years > 0 else 0

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

    exit_reasons = {}
    exit_reason_returns = {}

    for t in sells:
      reason = t.get('exit_reason', 'UNKNOWN')
      exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
      if reason not in exit_reason_returns:
        exit_reason_returns[reason] = []
      exit_reason_returns[reason].append(t['pnl_percent'])

    df_portfolio = pd.DataFrame(self.portfolio_history)
    df_portfolio['year'] = pd.to_datetime(df_portfolio['date']).dt.year

    yearly_returns = {}
    for year in sorted(df_portfolio['year'].unique()):
      year_data = df_portfolio[df_portfolio['year'] == year]
      if len(year_data) > 0:
        year_end_value = year_data.iloc[-1]['total_value']
        prev_year_data = df_portfolio[df_portfolio['year'] == year - 1]
        if len(prev_year_data) > 0:
          prev_year_end_value = prev_year_data.iloc[-1]['total_value']
        else:
          prev_year_end_value = self.initial_capital
        year_return = ((year_end_value - prev_year_end_value) / prev_year_end_value) * 100
        yearly_returns[year] = {'return': year_return, 'value': year_end_value}

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

    lines.append("[청산 사유별 통계]")
    total_exits = sum(exit_reasons.values())
    for reason, count in sorted(exit_reasons.items(), key=lambda x: x[1], reverse=True):
      percentage = (count / total_exits) * 100 if total_exits > 0 else 0
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

    print("\n" + "\n".join(lines))

    log_file = result_dir / f"log_real_{timestamp}.txt"
    with open(log_file, 'w', encoding='utf-8') as f:
      f.write("\n".join(lines))
    print(f"\n💾 리포트 저장: {log_file}")

    if self.trades_history:
      df_trades = pd.DataFrame(self.trades_history)
      trades_file = result_dir / f"trades_real_{timestamp}.csv"
      df_trades.to_csv(trades_file, index=False, encoding='utf-8-sig')
      print(f"💾 거래 내역 저장: {trades_file}")

    portfolio_file = result_dir / f"portfolio_real_{timestamp}.csv"
    df_portfolio.to_csv(portfolio_file, index=False, encoding='utf-8-sig')
    print(f"💾 포트폴리오 이력 저장: {portfolio_file}")
