# backtest_multifactor_realistic.py - 현실적인 멀티팩터 전략 (익일 시가 매매) + 상세 리포트

import sys
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

try:
  import yfinance as yf

  YFINANCE_AVAILABLE = True
except ImportError:
  YFINANCE_AVAILABLE = False
  print("⚠️  yfinance 미설치: pip install yfinance")
  sys.exit(1)

# config 모듈 임포트
from multifactor_bot import config


class MultiFactorBacktest:
  """멀티팩터 전략 백테스팅 (익일 시가 매매 버전)"""

  def __init__(self):
    self.config = config
    self.universe = config.WATCHLIST
    self.start_date = config.BACKTEST_START_DATE
    self.end_date = config.BACKTEST_END_DATE
    self.initial_capital = config.BACKTEST_INITIAL_CAPITAL
    self.commission = config.BACKTEST_COMMISSION
    self.rebalance_days = config.REBALANCE_PERIOD_DAYS
    self.top_n = config.TOP_N_STOCKS
    self.weights = config.FACTOR_WEIGHTS

    self.data = {}
    self.portfolio_history = []
    self.trades_history = []

  def run(self):
    """백테스팅 실행"""
    print("=" * 80)
    print("멀티팩터 전략 백테스팅 (익일 시가 매매 적용)")
    print("=" * 80)
    print(f"기간: {self.start_date} ~ {self.end_date}")
    print(f"초기 자본: ${self.initial_capital:,.0f}")
    print(f"유니버스: {len(self.universe)}개 종목")
    print(f"리밸런싱: {self.rebalance_days}일마다 신호 발생 -> 다음날 시가 매매")
    print("=" * 80)

    self.fetch_all_data()
    self.backtest()
    self.analyze_performance()

  def fetch_all_data(self):
    """모든 종목 데이터 수집"""
    print("\n데이터 수집 중...")
    success_count = 0
    for ticker in self.universe:
      try:
        stock = yf.Ticker(ticker)
        # Open 가격이 필수이므로 확인
        hist = stock.history(start=self.start_date, end=self.end_date)
        hist.index = hist.index.tz_localize(None)
        info = stock.info

        if len(hist) > 50:
          self.data[ticker] = {'history': hist, 'info': info}
          success_count += 1
      except Exception as e:
        print(f"{ticker} 오류: {e}")
    print(f"✅ {success_count}/{len(self.universe)}개 종목 수집 완료")

  def calculate_factors(self, date):
    """특정 날짜(Signal Date)의 팩터 계산"""
    factors = []
    for ticker, data in self.data.items():
      hist = data['history']
      info = data['info']

      # Signal Date까지의 데이터 사용
      hist_until = hist[hist.index <= date]
      if len(hist_until) < 126: continue

      momentum = self._calculate_momentum(hist_until)
      value = self._calculate_value(info)
      quality = self._calculate_quality(info)
      volatility = self._calculate_volatility(hist_until)

      # 가격은 참조용 (매매가는 아님)
      current_price = hist_until['Close'].iloc[-1]

      factors.append({
        'ticker': ticker,
        'price': current_price,
        'momentum': momentum,
        'value': value,
        'quality': quality,
        'volatility': volatility
      })
    return pd.DataFrame(factors)

  def _calculate_momentum(self, hist):
    try:
      period = min(config.MOMENTUM_PERIOD, len(hist) - 1)
      return (hist['Close'].iloc[-1] / hist['Close'].iloc[-period] - 1) * 100
    except:
      return np.nan

  def _calculate_value(self, info):
    try:
      pe = info.get('forwardPE', info.get('trailingPE', np.nan))
      pb = info.get('priceToBook', np.nan)
      if pd.isna(pe) or pd.isna(pb) or pe <= 0 or pb <= 0: return np.nan
      return (1 / pe + 1 / pb) / 2
    except:
      return np.nan

  def _calculate_quality(self, info):
    try:
      roe = info.get('returnOnEquity', np.nan)
      debt = info.get('debtToEquity', np.nan)
      if pd.isna(roe): return np.nan
      if pd.isna(debt): return roe * 100
      return roe * 100 - (debt / 100)
    except:
      return np.nan

  def _calculate_volatility(self, hist):
    try:
      period = min(config.VOLATILITY_PERIOD, len(hist))
      returns = hist['Close'].pct_change().dropna().tail(period)
      vol = returns.std() * np.sqrt(252) * 100
      return 100 / vol if vol > 0 else np.nan
    except:
      return np.nan

  def normalize_and_score(self, factors_df):
    factor_cols = ['momentum', 'value', 'quality', 'volatility']
    for col in factor_cols:
      mean = factors_df[col].mean()
      std = factors_df[col].std()
      factors_df[f'{col}_z'] = (factors_df[col] - mean) / std if std > 0 else 0

    factors_df['composite_score'] = (
        factors_df['momentum_z'] * self.weights['momentum'] +
        factors_df['value_z'] * self.weights['value'] +
        factors_df['quality_z'] * self.weights['quality'] +
        factors_df['volatility_z'] * self.weights['volatility']
    )
    return factors_df.dropna(subset=['composite_score']).sort_values(
        'composite_score', ascending=False)

  def backtest(self):
    """백테스팅 메인 로직 (수정됨: 익일 시가 매수 + 통계 데이터 수집)"""
    print("\n🔄 백테스팅 실행 중... (Signal -> Next Open Trade)\n")

    start = pd.to_datetime(self.start_date)
    end = pd.to_datetime(self.end_date)

    # 전체 거래일 리스트 확보 (샘플 종목 기준)
    sample_ticker = list(self.data.keys())[0]
    all_dates = self.data[sample_ticker]['history'].index
    trading_dates = [d for d in all_dates if start <= d <= end]

    # 리밸런싱 시그널 날짜
    signal_dates = trading_dates[::self.rebalance_days]

    cash = self.initial_capital
    holdings = {}  # {ticker: {'shares': N, 'avg_price': P, 'buy_date': Date}}

    print(f"리밸런싱 시그널: {len(signal_dates)}회")

    for i, date in enumerate(signal_dates):
      # 1. 신호 발생 (오늘 장 마감 후 계산 가정)
      signal_date = date

      # 2. 매매 실행일 찾기 (다음 거래일)
      try:
        curr_idx = list(trading_dates).index(signal_date)
        if curr_idx + 1 >= len(trading_dates):
          print(f"⚠️ {signal_date.date()} 이후 데이터 부족으로 매매 불가")
          continue
        execution_date = trading_dates[curr_idx + 1]
      except ValueError:
        continue

      print(
          f"\n[{i + 1}/{len(signal_dates)}] 신호: {signal_date.date()} → 매매: {execution_date.date()} (Open)")

      # 팩터 계산 & 종목 선정
      factors_df = self.calculate_factors(signal_date)
      if factors_df.empty: continue

      factors_df = self.normalize_and_score(factors_df)
      selected = factors_df.head(self.top_n)
      selected_tickers = set(selected['ticker'].tolist())

      # --- 매매 실행 (Execution Date Open Price 사용) ---

      # 1. 현재 자산 가치 평가 (매매일 시가 기준)
      current_equity = cash
      for ticker, pos in holdings.items():
        try:
          # 보유 종목의 매매일 시가
          curr_price = self.data[ticker]['history'].loc[execution_date]['Open']
          current_equity += pos['shares'] * curr_price
        except:
          # 데이터 없으면 직전 종가로 대용하거나 스킵
          pass

      print(f"  자산(매매전): ${current_equity:,.0f}")

      # 2. 매도 (포트폴리오에서 제외된 종목)
      for ticker in list(holdings.keys()):
        if ticker not in selected_tickers:
          pos = holdings[ticker]
          try:
            # 매도 가격: 시가(Open)
            sell_price = self.data[ticker]['history'].loc[execution_date][
              'Open']
            sell_val = pos['shares'] * sell_price
            fee = sell_val * self.commission

            cash += (sell_val - fee)

            # 수익률 (진입가 대비)
            pnl = (sell_price / pos['avg_price'] - 1) * 100

            # 보유 기간 계산
            buy_date = pos.get('buy_date', execution_date)
            days_held = (execution_date - buy_date).days

            self.trades_history.append({
              'date': execution_date,
              'ticker': ticker,
              'action': 'SELL',
              'shares': pos['shares'],
              'price': sell_price,
              'pnl': pnl,
              'days': days_held,
              'exit_reason': 'SIGNAL' # 리밸런싱에 의한 매도는 SIGNAL로 분류
            })
            print(f"  💰 매도: {ticker} ({pnl:+.1f}%)")
            del holdings[ticker]
          except:
            print(f"  ❌ 매도 실패: {ticker}")

      # 3. 매수 (신규 종목, 동일 비중)
      target_per_stock = current_equity / self.top_n

      for _, row in selected.iterrows():
        ticker = row['ticker']

        # 이미 보유중이면 패스 (리밸런싱 단순화)
        if ticker in holdings: continue

        try:
          # 매수 가격: 시가(Open)
          buy_price = self.data[ticker]['history'].loc[execution_date]['Open']
          shares = int(target_per_stock / buy_price)

          if shares > 0:
            cost = shares * buy_price
            fee = cost * self.commission

            if cash >= (cost + fee):
              cash -= (cost + fee)
              holdings[ticker] = {
                'shares': shares,
                'avg_price': buy_price,
                'buy_date': execution_date # 보유일 계산을 위해 추가
              }
              self.trades_history.append({
                'date': execution_date,
                'ticker': ticker,
                'action': 'BUY',
                'shares': shares,
                'price': buy_price,
                'pnl': 0,
                'days': 0,
                'exit_reason': 'ENTRY'
              })
              print(f"  🛒 매수: {ticker} {shares}주")
        except:
          print(f"  ❌ 매수 실패: {ticker}")

      # 4. 포트폴리오 기록 (매매일 종가 기준 평가)
      # 매매는 아침(Open)에 끝났고, 기록은 그날 장 마감(Close) 기준
      final_val = cash
      for ticker, pos in holdings.items():
        try:
          close_price = self.data[ticker]['history'].loc[execution_date][
            'Close']
          final_val += pos['shares'] * close_price
        except:
          pass

      self.portfolio_history.append({
        'date': execution_date,
        'total_value': final_val
      })
      print(f"  📊 자산(마감): ${final_val:,.0f}")

  def analyze_performance(self):
    """성과 분석 (상세 리포트)"""
    if not self.portfolio_history:
      print("❌ 포트폴리오 기록 없음")
      return

    df = pd.DataFrame(self.portfolio_history)
    df['date'] = pd.to_datetime(df['date'])

    # 기본 통계
    start_date = df['date'].iloc[0]
    end_date = df['date'].iloc[-1]
    initial = self.initial_capital # 초기 자본 사용 (첫 기록보다 정확함)
    final = df['total_value'].iloc[-1]
    total_return = (final / initial - 1) * 100

    # CAGR 계산
    days = (end_date - start_date).days
    years = days / 365.25
    cagr = ((final / initial) ** (1 / years) - 1) * 100 if years > 0 else 0

    # 거래 통계
    trades_df = pd.DataFrame(self.trades_history)
    buy_count = 0
    sell_count = 0
    win_rate = 0.0
    avg_pnl = 0.0
    avg_days = 0.0
    max_profit = 0.0
    max_loss = 0.0

    if not trades_df.empty:
      buy_trades = trades_df[trades_df['action'] == 'BUY']
      sell_trades = trades_df[trades_df['action'] == 'SELL']

      buy_count = len(buy_trades)
      sell_count = len(sell_trades)

      if sell_count > 0:
        wins = sell_trades[sell_trades['pnl'] > 0]
        win_rate = len(wins) / sell_count * 100
        avg_pnl = sell_trades['pnl'].mean()
        avg_days = sell_trades['days'].mean()
        max_profit = sell_trades['pnl'].max()
        max_loss = sell_trades['pnl'].min()

    # 로그 문자열 수집
    log_lines = []
    log_lines.append("=" * 80)
    log_lines.append(f"기간           : {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")
    log_lines.append(f"초기 자산      : ${initial:,.2f}")
    log_lines.append(f"최종 자산      : ${final:,.2f}")
    log_lines.append(f"총 수익률      : {total_return:+.2f}%")
    log_lines.append(f"연평균(CAGR)   : {cagr:+.2f}%")
    log_lines.append("-" * 80)
    log_lines.append(f"매수 횟수      : {buy_count}회")
    log_lines.append(f"매도 횟수      : {sell_count}회")
    log_lines.append(f"승률           : {win_rate:.1f}%")
    log_lines.append(f"평균 수익률    : {avg_pnl:+.2f}%")
    log_lines.append(f"평균 보유일    : {avg_days:.1f}일")
    log_lines.append(f"최대 수익      : {max_profit:+.2f}%")
    log_lines.append(f"최대 손실      : {max_loss:+.2f}%")
    log_lines.append("-" * 80)

    # 청산 사유별 통계
    log_lines.append("[청산 사유별 통계]")
    if sell_count > 0:
      reason_counts = sell_trades['exit_reason'].value_counts()
      for reason, count in reason_counts.items():
        pct = count / sell_count * 100
        # 한글 매핑
        reason_kor = reason
        if reason == 'SIGNAL': reason_kor = '기술적 신호 (리밸런싱)'
        elif reason == 'STOP_LOSS': reason_kor = '손절'
        elif reason == 'TRAILING_STOP': reason_kor = '트레일링 스탑'

        log_lines.append(f"{reason_kor:<14}: {count}회 ({pct:.1f}%)")

      log_lines.append("")
      log_lines.append("[청산 사유별 평균 수익률]")
      for reason in reason_counts.index:
        avg = sell_trades[sell_trades['exit_reason'] == reason]['pnl'].mean()
        log_lines.append(f"{reason:<14}: {avg:+.2f}% ({len(sell_trades[sell_trades['exit_reason'] == reason])}회)")
    else:
      log_lines.append("매도 기록 없음")

    log_lines.append("-" * 80)
    log_lines.append("[연도별 수익률]")

    # 연도별 수익률 계산
    df_equity = df.set_index('date')
    # resample('YE')는 최신 pandas, 구버전 호환을 위해 'Y' 사용 고려하거나 groupby 사용
    try:
      yearly_equity = df_equity['total_value'].resample('YE').last()
    except:
      yearly_equity = df_equity['total_value'].resample('Y').last()

    prev_val = initial
    # 첫 해의 시작이 1월 1일이 아닐 수 있으므로, 첫 해 수익률은 (연말값 - 초기값)/초기값
    # 그 이후는 (올해말 - 작년말)/작년말

    # 데이터프레임의 첫 해 확인
    first_year = df_equity.index[0].year

    # yearly_equity에는 각 연도 말의 자산이 들어있음
    for date, val in yearly_equity.items():
      year = date.year
      if year == first_year:
        # 첫 해는 초기 자본 대비 수익률
        year_ret = (val - initial) / initial * 100
        base_val = initial
      else:
        # 그 외는 전년도 말 자본 대비 수익률
        # 전년도 데이터 찾기
        prev_date = date - pd.DateOffset(years=1)
        # 정확한 전년도 말 데이터를 찾기 어려우므로 prev_val 변수 활용
        year_ret = (val - prev_val) / prev_val * 100
        base_val = prev_val

      log_lines.append(f"{year}년         : {year_ret:+.2f}%  (자산: ${val:,.2f})")
      prev_val = val

    log_lines.append("=" * 80)

    # 콘솔 출력
    print("\n" + "\n".join(log_lines))

    # CSV 및 로그 파일로 저장
    self._save_results_to_csv(
      df, trades_df, initial, final, total_return, cagr,
      buy_count, sell_count, win_rate, avg_pnl, avg_days,
      max_profit, max_loss, log_lines
    )

  def _save_results_to_csv(self, portfolio_df, trades_df, initial, final,
                           total_return, cagr, buy_count, sell_count,
                           win_rate, avg_pnl, avg_days, max_profit, max_loss, log_lines):
    """백테스트 결과를 CSV 및 로그 파일로 저장"""
    # 타임스탬프 생성
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 결과 폴더 생성
    result_dir = Path("backtest_result")
    result_dir.mkdir(exist_ok=True)

    # 1. 포트폴리오 히스토리 저장
    portfolio_file = result_dir / f"portfolio_{timestamp}.csv"
    portfolio_df.to_csv(portfolio_file, index=False, encoding='utf-8-sig')
    print(f"\n💾 포트폴리오 히스토리 저장: {portfolio_file}")

    # 2. 거래 내역 저장
    if not trades_df.empty:
      trades_file = result_dir / f"trades_{timestamp}.csv"
      trades_df.to_csv(trades_file, index=False, encoding='utf-8-sig')
      print(f"💾 거래 내역 저장: {trades_file}")

    # 3. 성과 요약 저장 (CSV)
    summary_data = {
      '항목': [
        '백테스트 기간 시작',
        '백테스트 기간 종료',
        '초기 자본 ($)',
        '최종 자산 ($)',
        '총 수익률 (%)',
        '연평균 수익률 CAGR (%)',
        '매수 횟수',
        '매도 횟수',
        '승률 (%)',
        '평균 수익률 (%)',
        '평균 보유일',
        '최대 수익 (%)',
        '최대 손실 (%)',
        '전략',
        '유니버스 종목 수',
        '포트폴리오 종목 수',
        '리밸런싱 주기 (일)',
        '거래 수수료 (%)'
      ],
      '값': [
        self.start_date,
        self.end_date,
        f"{initial:,.2f}",
        f"{final:,.2f}",
        f"{total_return:+.2f}",
        f"{cagr:+.2f}",
        buy_count,
        sell_count,
        f"{win_rate:.1f}",
        f"{avg_pnl:+.2f}",
        f"{avg_days:.1f}",
        f"{max_profit:+.2f}",
        f"{max_loss:+.2f}",
        '멀티팩터 (모멘텀/가치/퀄리티/변동성)',
        len(self.universe),
        self.top_n,
        self.rebalance_days,
        f"{self.commission * 100:.2f}"
      ]
    }

    summary_df = pd.DataFrame(summary_data)
    summary_file = result_dir / f"summary_{timestamp}.csv"
    summary_df.to_csv(summary_file, index=False, encoding='utf-8-sig')
    print(f"💾 성과 요약 저장: {summary_file}")

    # 4. 성과 분석 로그 저장 (TXT)
    log_file = result_dir / f"log_{timestamp}.txt"
    with open(log_file, 'w', encoding='utf-8') as f:
      f.write('\n'.join(log_lines))
    print(f"💾 성과 분석 로그 저장: {log_file}")

    print(f"\n✅ 모든 결과가 {result_dir}/ 폴더에 저장되었습니다.")


if __name__ == "__main__":
  # config_multifactor.py가 같은 폴더에 있어야 합니다.
  backtest = MultiFactorBacktest()
  backtest.run()
