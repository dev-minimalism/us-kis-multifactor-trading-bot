# test_bot.py - MultiFactorBot 핵심 로직 (외부 의존성 전부 모킹)
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
import pytz

from multifactor_bot.core import bot as botmod


@pytest.fixture
def make_bot(monkeypatch):
  """외부 의존성을 모두 MagicMock 으로 바꾼 봇 팩토리"""
  cfg = botmod.config
  monkeypatch.setattr(cfg, 'DYNAMIC_SCREENING_ENABLED', False)
  monkeypatch.setattr(cfg, 'TELEGRAM_TOKEN', None)
  monkeypatch.setattr(cfg, 'TELEGRAM_CHAT_ID', None)
  monkeypatch.setattr(cfg, 'DRY_RUN', True)
  monkeypatch.setattr(cfg, 'STOP_LOSS_ENABLED', True)
  monkeypatch.setattr(cfg, 'STOP_LOSS_PERCENT', -15.0)
  monkeypatch.setattr(cfg, 'TRAILING_STOP_ENABLED', True)
  monkeypatch.setattr(cfg, 'TRAILING_STOP_PERCENT', -15.0)
  monkeypatch.setattr(cfg, 'MIN_HOLD_DAYS', 7)
  monkeypatch.setattr(cfg, 'MAX_HOLD_DAYS', 90)
  monkeypatch.setattr(cfg, 'REBALANCE_PERIOD_DAYS', 30)
  monkeypatch.setattr(cfg, 'LIVE_CASH_FLOW_THRESHOLD_USD', 100.0)
  monkeypatch.setattr(cfg, 'BACKTEST_COMMISSION', 0.001)
  monkeypatch.setattr(cfg, 'FACTOR_RETRY_WAIT_SECONDS', 0)
  monkeypatch.setattr(cfg, 'FACTOR_RETRY_MIN_SKIP_RATIO', 0.3)
  monkeypatch.setattr(cfg, 'FACTOR_WEIGHTS',
                      {'momentum': 0.4, 'value': 0.2, 'quality': 0.2, 'volatility': 0.2})

  monkeypatch.setattr(botmod, 'KISDataProvider', lambda **k: MagicMock())
  monkeypatch.setattr(botmod, 'DynamicScreener', lambda **k: MagicMock())

  def _make(db_enabled=False, paper=True, db_manager=None):
    monkeypatch.setattr(cfg, 'DB_ENABLED', db_enabled)
    monkeypatch.setattr(cfg, 'PAPER_TRADING_ENABLED', paper)
    # 생성자 안의 Live 동기화가 MagicMock 값으로 돌지 않도록, 시작 시점엔 잔고 조회 실패로 둔다
    trader = MagicMock()
    trader.get_balance.return_value = None
    monkeypatch.setattr(botmod, 'KISTrader', lambda **k: trader)
    if db_manager is None:
      dbm = MagicMock()
      dbm.get_positions.return_value = []
    else:
      dbm = db_manager
    monkeypatch.setattr(botmod, 'PaperTradingManager', lambda **k: dbm)
    monkeypatch.setattr(botmod, 'LiveTradingManager', lambda **k: dbm)
    b = botmod.MultiFactorBot()
    b.send_telegram = MagicMock()
    b.trader.reset_mock()
    return b

  return _make


def days_ago(n):
  return datetime.now() - timedelta(days=n)


# ---------------- 초기화 / DB 복원 ----------------

@pytest.mark.parametrize('dynamic,expected', [(False, '📋 고정'), (True, '🔍 동적')])
def test_startup_telegram_mentions_screening_mode(make_bot, monkeypatch, dynamic, expected):
  sent = []
  monkeypatch.setattr(botmod.MultiFactorBot, 'send_telegram', lambda self, msg: sent.append(msg))
  monkeypatch.setattr(botmod.config, 'DYNAMIC_SCREENING_ENABLED', dynamic)
  make_bot(db_enabled=True, paper=True)
  startup = [m for m in sent if m.startswith('🤖 봇 가동 시작')]
  assert len(startup) == 1
  assert expected in startup[0] and '📝 Paper' in startup[0] and '리밸런싱: 30일' in startup[0]


def test_bot_without_db_starts_empty(make_bot):
  b = make_bot(db_enabled=False)
  assert b.holdings == {} and b.last_rebalance is None and b.db_manager is None


def test_restore_holdings_from_db_sets_holdings_and_last_rebalance(make_bot):
  dbm = MagicMock()
  dbm.get_positions.return_value = [
    {'ticker': 'AAPL', 'shares': 10, 'avg_price': 150.0, 'buy_date': days_ago(20), 'highest_price': 170.0},
    {'ticker': 'MSFT', 'shares': 5, 'avg_price': 400.0,
     'buy_date': pytz.utc.localize(days_ago(3)), 'highest_price': None},  # tz-aware
    {'ticker': 'BAD', 'shares': 0, 'avg_price': 1.0, 'buy_date': days_ago(1)},  # 무효 행
  ]
  b = make_bot(db_enabled=True, paper=True, db_manager=dbm)
  assert set(b.holdings) == {'AAPL', 'MSFT'}
  assert b.holdings['AAPL']['high_price'] == 170.0
  assert b.holdings['MSFT']['high_price'] == 400.0          # highest 없으면 평단
  assert b.holdings['MSFT']['buy_date'].tzinfo is None       # naive 로 변환
  assert abs((b.last_rebalance - days_ago(3)).total_seconds()) < 5  # 가장 최근 매수일
  assert b.should_rebalance() is False


def test_restore_holdings_survives_db_error(make_bot):
  dbm = MagicMock()
  dbm.get_positions.side_effect = RuntimeError('db down')
  b = make_bot(db_enabled=True, paper=True, db_manager=dbm)
  assert b.holdings == {}


# ---------------- Live 동기화 ----------------

def test_live_sync_is_noop_in_paper_mode(make_bot):
  b = make_bot(db_enabled=True, paper=True)
  assert b._sync_live_account_from_kis('t') is None
  b.trader.get_balance.assert_not_called()
  b.db_manager.sync_from_kis.assert_not_called()


def test_live_sync_skips_db_when_balance_query_fails(make_bot):
  b = make_bot(db_enabled=True, paper=False)
  b.trader.get_balance.return_value = None
  assert b._sync_live_account_from_kis('t') is None
  b.db_manager.sync_from_kis.assert_not_called()


def test_live_sync_passes_cash_and_positions_and_alerts_on_first_sync(make_bot):
  b = make_bot(db_enabled=True, paper=False)
  b.trader.get_balance.return_value = {'total_cash': 1.0, 'available_cash': 3000.0}
  b.trader.get_positions.return_value = [{'ticker': 'AAPL', 'shares': 1, 'avg_price': 1, 'current_price': 1}]
  b.db_manager.sync_from_kis.return_value = {
    'first_sync': True, 'kis_cash': 3000.0, 'initial_capital': 3001.0, 'cash_flow': 0.0,
    'positions': {'added': ['AAPL'], 'updated': [], 'removed': []}}
  r = b._sync_live_account_from_kis('봇 시작')
  assert r['first_sync'] is True
  kwargs = b.db_manager.sync_from_kis.call_args.kwargs
  assert kwargs['kis_cash'] == 3000.0 and kwargs['kis_positions'][0]['ticker'] == 'AAPL'
  assert kwargs['cash_flow_threshold'] == 100.0
  b.send_telegram.assert_called_once()
  assert '첫 동기화' in b.send_telegram.call_args.args[0]


def test_live_sync_with_positions_failure_syncs_cash_only(make_bot):
  b = make_bot(db_enabled=True, paper=False)
  b.trader.get_balance.return_value = {'available_cash': 10.0}
  b.trader.get_positions.return_value = None
  b.db_manager.sync_from_kis.return_value = {
    'first_sync': False, 'kis_cash': 10.0, 'initial_capital': 1.0, 'cash_flow': 0.0, 'positions': None}
  b._sync_live_account_from_kis('t')
  assert b.db_manager.sync_from_kis.call_args.kwargs['kis_positions'] is None
  b.send_telegram.assert_not_called()  # 조용한 재동기화


def test_live_sync_alerts_on_cash_flow(make_bot):
  b = make_bot(db_enabled=True, paper=False)
  b.trader.get_balance.return_value = {'available_cash': 10.0}
  b.trader.get_positions.return_value = []
  b.db_manager.sync_from_kis.return_value = {
    'first_sync': False, 'kis_cash': 10.0, 'initial_capital': 1.0, 'cash_flow': 5000.0, 'positions': None}
  b._sync_live_account_from_kis('t')
  assert '입출금' in b.send_telegram.call_args.args[0]


def test_live_sync_swallows_db_exception(make_bot):
  b = make_bot(db_enabled=True, paper=False)
  b.trader.get_balance.return_value = {'available_cash': 10.0}
  b.trader.get_positions.return_value = []
  b.db_manager.sync_from_kis.side_effect = RuntimeError('db')
  assert b._sync_live_account_from_kis('t') is None


# ---------------- 리밸런싱 판단 ----------------

@pytest.mark.parametrize('last,expected', [(None, True), (10, False), (30, True), (45, True)])
def test_should_rebalance(make_bot, last, expected):
  b = make_bot()
  b.last_rebalance = None if last is None else days_ago(last)
  assert b.should_rebalance() is expected


# ---------------- 스코어링 ----------------

def test_normalize_and_score_ranks_by_weighted_zscore(make_bot):
  b = make_bot()
  df = pd.DataFrame({
    'ticker': ['A', 'B', 'C'],
    'momentum': [30.0, 10.0, -10.0],
    'value': [0.1, 0.1, 0.1],        # 부동소수 오차로 std≈1e-17 이어도 z = 0 이어야 한다
    'quality': [5.0, 15.0, 10.0],
    'volatility': [2.0, 2.0, 8.0],
  })
  out = b.normalize_and_score(df.copy())
  assert list(out['ticker']) == ['A', 'C', 'B'] or list(out['ticker'])[0] == 'A'
  assert (out['value_z'] == 0).all()
  # composite = 0.4*mz + 0.2*vz + 0.2*qz + 0.2*volz 재계산
  for _, row in out.iterrows():
    expected = 0.4 * row['momentum_z'] + 0.2 * row['value_z'] + 0.2 * row['quality_z'] + 0.2 * row['volatility_z']
    assert row['composite_score'] == pytest.approx(expected)
  assert out['composite_score'].is_monotonic_decreasing


def test_normalize_and_score_drops_nan_rows(make_bot):
  b = make_bot()
  df = pd.DataFrame({'ticker': ['A', 'B', 'C'], 'momentum': [1.0, 2.0, np.nan],
                     'value': [1.0, 2.0, 3.0], 'quality': [1.0, 2.0, 3.0], 'volatility': [1.0, 2.0, 3.0]})
  assert list(b.normalize_and_score(df)['ticker']) == ['B', 'A']


# ---------------- 팩터 계산 ----------------

def test_factor_helpers(make_bot, monkeypatch):
  b = make_bot()
  monkeypatch.setattr(botmod.config, 'MOMENTUM_PERIOD', 3)
  monkeypatch.setattr(botmod.config, 'VOLATILITY_PERIOD', 10)
  df = pd.DataFrame({'Close': [100.0, 105.0, 110.0, 120.0]})
  assert b._calculate_momentum(df) == pytest.approx((120 / 105 - 1) * 100)
  assert b._calculate_value({'forwardPE': 20, 'priceToBook': 4}) == pytest.approx((1 / 20 + 1 / 4) / 2)
  assert np.isnan(b._calculate_value({'forwardPE': -5, 'priceToBook': 4}))
  assert b._calculate_quality({'returnOnEquity': 0.2, 'debtToEquity': 50}) == pytest.approx(20 - 0.5)
  assert b._calculate_quality({'returnOnEquity': 0.2}) == pytest.approx(20)
  assert b._calculate_volatility(df) > 0
  assert np.isnan(b._calculate_volatility(pd.DataFrame({'Close': [1.0, 1.0, 1.0]})))


# ---------------- 매수 / 매도 ----------------

def test_buy_position_records_holding_and_db(make_bot):
  b = make_bot(db_enabled=True, paper=True)
  b.trader.buy_stock.return_value = {'success': True}
  b.buy_position('AAPL', price=150.0, amount=1000.0)
  b.trader.buy_stock.assert_called_once_with('AAPL', 6, dry_run=True)
  assert b.holdings['AAPL']['shares'] == 6 and b.holdings['AAPL']['high_price'] == 150.0
  assert b.db_manager.record_buy.call_args.kwargs['shares'] == 6
  assert b.db_manager.add_position.call_args.kwargs['ticker'] == 'AAPL'
  assert b.total_trades == 1


@pytest.mark.parametrize('bad', [float('nan'), 0.0, -3.0, None, 'abc'])
def test_buy_position_rejects_invalid_price(make_bot, bad):
  """NaN 가격은 int() 에서 ValueError → 리밸런싱 전체 중단 사고 방지"""
  b = make_bot()
  b.buy_position('AAPL', price=bad, amount=1000.0)
  b.trader.buy_stock.assert_not_called()
  assert 'AAPL' not in b.holdings


def test_calculate_all_factors_retries_once_when_many_skipped(make_bot, monkeypatch):
  """다운로드 대량 실패(일시 제한)면 대기 후 누락 종목만 재시도한다"""
  b = make_bot()
  monkeypatch.setattr(botmod.config, 'WATCHLIST', ['A', 'B', 'C', 'D'])
  monkeypatch.setattr(botmod.config, 'FACTOR_RETRY_MIN_SKIP_RATIO', 0.3)
  monkeypatch.setattr(botmod.config, 'FACTOR_RETRY_WAIT_SECONDS', 0)
  slept = []
  monkeypatch.setattr(botmod.time, 'sleep', lambda s: slept.append(s))
  idx = pd.bdate_range(end='2026-09-22', periods=200)
  good = pd.DataFrame({'Close': 100.0, 'Volume': 1e6}, index=idx)
  attempts = {}

  def download(t, *a, **k):
    attempts[t] = attempts.get(t, 0) + 1
    if t == 'A' or attempts[t] >= 2:   # A 는 처음부터 성공, 나머지는 재시도에서 성공
      return good
    return None
  b.data_provider.download.side_effect = download
  monkeypatch.setattr(botmod.yf, 'Ticker', lambda t: MagicMock(info={}))
  df = b.calculate_all_factors()
  assert sorted(df['ticker']) == ['A', 'B', 'C', 'D']
  assert slept == [0] and attempts == {'A': 1, 'B': 2, 'C': 2, 'D': 2}


def test_calculate_all_factors_no_retry_below_ratio(make_bot, monkeypatch):
  b = make_bot()
  monkeypatch.setattr(botmod.config, 'WATCHLIST', ['A', 'B', 'C', 'D', 'E'])
  monkeypatch.setattr(botmod.config, 'FACTOR_RETRY_MIN_SKIP_RATIO', 0.3)
  monkeypatch.setattr(botmod.time, 'sleep', lambda s: pytest.fail('must not retry'))
  idx = pd.bdate_range(end='2026-09-22', periods=200)
  good = pd.DataFrame({'Close': 100.0, 'Volume': 1e6}, index=idx)
  b.data_provider.download.side_effect = lambda t, *a, **k: None if t == 'E' else good  # 1/5 = 20% 누락
  monkeypatch.setattr(botmod.yf, 'Ticker', lambda t: MagicMock(info={}))
  assert len(b.calculate_all_factors()) == 4


def test_calculate_all_factors_uses_last_valid_close(make_bot, monkeypatch):
  b = make_bot()
  monkeypatch.setattr(botmod.config, 'WATCHLIST', ['NANLAST', 'ALLNAN'])
  idx = pd.bdate_range(end='2026-09-22', periods=200)
  ok = pd.DataFrame({'Close': 100.0, 'Volume': 1e6}, index=idx); ok.iloc[-1, ok.columns.get_loc('Close')] = np.nan
  allnan = pd.DataFrame({'Close': np.nan, 'Volume': 1e6}, index=idx)
  b.data_provider.download.side_effect = lambda t, *a, **k: {'NANLAST': ok, 'ALLNAN': allnan}[t]
  monkeypatch.setattr(botmod.yf, 'Ticker', lambda t: MagicMock(info={}))
  df = b.calculate_all_factors()
  assert list(df['ticker']) == ['NANLAST'] and df.iloc[0]['price'] == 100.0


def test_rebalance_continues_when_one_buy_raises(make_bot, monkeypatch):
  b = make_bot(db_enabled=True, paper=True)
  monkeypatch.setattr(botmod.config, 'TOP_N_STOCKS', 3)
  monkeypatch.setattr(botmod.config, 'MIN_ORDER_AMOUNT_USD', 50)
  b.calculate_all_factors = MagicMock(return_value=pd.DataFrame({
    'ticker': ['A', 'B', 'C'], 'price': [10.0, 20.0, 30.0],
    'momentum': [3.0, 2.0, 1.0], 'value': [1.0, 1.0, 1.0], 'quality': [1.0, 1.0, 1.0], 'volatility': [1.0, 1.0, 1.0]}))
  b.db_manager.get_cash_balance.return_value = 3000.0
  calls = []

  def buy(ticker, price, amount):
    calls.append(ticker)
    if ticker == 'B':
      raise RuntimeError('boom')
  b.buy_position = buy
  b.rebalance()  # 예외가 밖으로 나오면 안 된다
  assert sorted(calls) == ['A', 'B', 'C']


def test_buy_position_skips_when_amount_below_one_share(make_bot):
  b = make_bot()
  b.buy_position('AAPL', price=150.0, amount=100.0)
  b.trader.buy_stock.assert_not_called()
  assert 'AAPL' not in b.holdings


def test_buy_position_failure_leaves_no_holding(make_bot):
  b = make_bot()
  b.trader.buy_stock.return_value = {'success': False, 'message': 'rejected'}
  b.buy_position('AAPL', 150.0, 1000.0)
  assert 'AAPL' not in b.holdings and b.total_trades == 0


def test_sell_position_records_db_and_removes_holding(make_bot):
  b = make_bot(db_enabled=True, paper=True)
  b.holdings['AAPL'] = {'shares': 6, 'avg_price': 150.0, 'buy_date': days_ago(10), 'high_price': 160.0}
  b.data_provider.get_us_stock_price.return_value = {'price': 165.0}
  b.trader.sell_stock.return_value = {'success': True}
  b.sell_position('AAPL', '손절')
  b.trader.sell_stock.assert_called_once_with('AAPL', 6, dry_run=True)
  kw = b.db_manager.record_sell.call_args.kwargs
  assert kw['price'] == 165.0 and kw['buy_price'] == 150.0 and kw['exit_reason'] == '손절'
  b.db_manager.remove_position.assert_called_once_with('AAPL')
  assert 'AAPL' not in b.holdings and b.successful_trades == 1


def test_sell_position_uses_avg_price_when_quote_missing(make_bot):
  b = make_bot()
  b.holdings['AAPL'] = {'shares': 1, 'avg_price': 150.0, 'buy_date': days_ago(1), 'high_price': 150.0}
  b.data_provider.get_us_stock_price.return_value = None
  b.trader.sell_stock.return_value = {'success': True}
  b.sell_position('AAPL', 'x')
  assert 'AAPL' not in b.holdings and b.successful_trades == 0  # 0% → 성공 아님


def test_sell_position_ignores_unknown_ticker(make_bot):
  b = make_bot()
  b.sell_position('NOPE', 'x')
  b.trader.sell_stock.assert_not_called()


# ---------------- 리스크 관리 ----------------

def _hold(avg, high, days):
  return {'shares': 1, 'avg_price': avg, 'buy_date': days_ago(days), 'high_price': high}


def test_stop_loss_triggers_after_min_hold(make_bot):
  b = make_bot()
  b.holdings['A'] = _hold(100, 100, days=10)
  b.data_provider.get_us_stock_price.return_value = {'price': 80.0}
  b.sell_position = MagicMock()
  b.check_risk_management()
  assert '손절' in b.sell_position.call_args.args[1]


def test_stop_loss_blocked_by_min_hold_days(make_bot):
  """MIN_HOLD_DAYS 이전에는 손절하지 않는다"""
  b = make_bot()
  b.holdings['A'] = _hold(100, 100, days=2)
  b.data_provider.get_us_stock_price.return_value = {'price': 80.0}
  b.sell_position = MagicMock()
  b.check_risk_management()
  b.sell_position.assert_not_called()


def test_trailing_stop_blocked_by_min_hold_days(make_bot):
  """최소 보유일은 트레일링 스탑에도 적용된다 (급등 후 급락해도 첫 주엔 보유)"""
  b = make_bot()
  b.holdings['A'] = _hold(100, 130, days=2)
  b.data_provider.get_us_stock_price.return_value = {'price': 110.0}
  b.sell_position = MagicMock()
  b.check_risk_management()
  b.sell_position.assert_not_called()


def test_max_hold_exit_not_blocked_by_min_hold_gate(make_bot, monkeypatch):
  """손절 조건이 맞지만 최소 보유일 미달이어도 보유기간 만료 규칙은 독립적으로 평가된다"""
  b = make_bot()
  monkeypatch.setattr(botmod.config, 'MIN_HOLD_DAYS', 10)
  monkeypatch.setattr(botmod.config, 'MAX_HOLD_DAYS', 5)
  b.holdings['A'] = _hold(100, 100, days=6)
  b.data_provider.get_us_stock_price.return_value = {'price': 80.0}
  b.sell_position = MagicMock()
  b.check_risk_management()
  assert '보유기간' in b.sell_position.call_args.args[1]


def test_stop_loss_takes_priority_over_trailing(make_bot):
  b = make_bot()
  b.holdings['A'] = _hold(100, 130, days=10)
  b.data_provider.get_us_stock_price.return_value = {'price': 80.0}  # -20% 진입가, -38% 고점
  b.sell_position = MagicMock()
  b.check_risk_management()
  assert '손절' in b.sell_position.call_args.args[1]


def test_trailing_stop_triggers_from_high(make_bot):
  b = make_bot()
  b.holdings['A'] = _hold(100, 130, days=10)
  b.data_provider.get_us_stock_price.return_value = {'price': 110.0}  # +10% 수익, 고점 대비 -15.4%
  b.sell_position = MagicMock()
  b.check_risk_management()
  assert '트레일링' in b.sell_position.call_args.args[1]


def test_max_hold_days_exit(make_bot):
  b = make_bot()
  b.holdings['A'] = _hold(100, 100, days=91)
  b.data_provider.get_us_stock_price.return_value = {'price': 101.0}
  b.sell_position = MagicMock()
  b.check_risk_management()
  assert '보유기간' in b.sell_position.call_args.args[1]


def test_high_price_tracks_new_highs_without_selling(make_bot):
  b = make_bot()
  b.holdings['A'] = _hold(100, 100, days=10)
  b.data_provider.get_us_stock_price.return_value = {'price': 120.0}
  b.sell_position = MagicMock()
  b.check_risk_management()
  b.sell_position.assert_not_called()
  assert b.holdings['A']['high_price'] == 120.0


def test_risk_check_skips_ticker_without_quote(make_bot):
  b = make_bot()
  b.holdings['A'] = _hold(100, 100, days=100)
  b.data_provider.get_us_stock_price.return_value = None
  b.sell_position = MagicMock()
  b.check_risk_management()
  b.sell_position.assert_not_called()


# ---------------- 리밸런싱 (Paper) ----------------

def test_rebalance_paper_sells_dropped_and_buys_new_equally(make_bot, monkeypatch):
  b = make_bot(db_enabled=True, paper=True)
  monkeypatch.setattr(botmod.config, 'TOP_N_STOCKS', 2)
  monkeypatch.setattr(botmod.config, 'MIN_ORDER_AMOUNT_USD', 50)
  b.holdings['OLD'] = _hold(10, 10, 20)
  b.holdings['KEEP'] = _hold(10, 10, 20)
  b.calculate_all_factors = MagicMock(return_value=pd.DataFrame({
    'ticker': ['KEEP', 'NEW', 'LOSER'], 'price': [10.0, 20.0, 5.0],
    'momentum': [3.0, 2.0, -5.0], 'value': [1.0, 1.0, 0.0], 'quality': [1.0, 1.0, 0.0], 'volatility': [1.0, 1.0, 0.0]}))
  b.db_manager.get_cash_balance.return_value = 1000.0
  b.sell_position = MagicMock()
  b.buy_position = MagicMock()

  b.rebalance()

  b.sell_position.assert_called_once_with('OLD', '리밸런싱 제외')
  b.buy_position.assert_called_once_with('NEW', 20.0, 1000.0)  # to_buy 1개 → 전액
  b.trader.get_balance.assert_not_called()  # paper 는 DB 잔고


def test_rebalance_live_skips_buys_when_balance_unavailable(make_bot):
  b = make_bot(db_enabled=True, paper=False)
  b.calculate_all_factors = MagicMock(return_value=pd.DataFrame({
    'ticker': ['NEW'], 'price': [20.0], 'momentum': [1.0], 'value': [1.0], 'quality': [1.0], 'volatility': [1.0]}))
  b.trader.get_balance.return_value = None
  b.buy_position = MagicMock()
  b.rebalance()
  b.buy_position.assert_not_called()


def test_rebalance_skips_orders_below_minimum(make_bot, monkeypatch):
  b = make_bot(db_enabled=True, paper=True)
  monkeypatch.setattr(botmod.config, 'TOP_N_STOCKS', 2)
  monkeypatch.setattr(botmod.config, 'MIN_ORDER_AMOUNT_USD', 2000)
  b.calculate_all_factors = MagicMock(return_value=pd.DataFrame({
    'ticker': ['A', 'B'], 'price': [1.0, 1.0], 'momentum': [2.0, 1.0], 'value': [1.0, 1.0], 'quality': [1.0, 1.0], 'volatility': [1.0, 1.0]}))
  b.db_manager.get_cash_balance.return_value = 1000.0  # 500씩 → 최소 주문금액 미달
  b.buy_position = MagicMock()
  b.rebalance()
  b.buy_position.assert_not_called()


# ---------------- 장 운영 시간 (미국 동부시간 기준) ----------------

SESSIONS = {
  'day_market': {'start': '20:00', 'end': '03:50'},
  'pre_market': {'start': '04:00', 'end': '09:30'},
  'regular': {'start': '09:30', 'end': '16:00'},
  'after_market': {'start': '16:00', 'end': '19:50'},
}
ET = pytz.timezone('America/New_York')
KST = pytz.timezone('Asia/Seoul')


def _et(y, mo, d, h, mi):
  return ET.localize(datetime(y, mo, d, h, mi))


@pytest.fixture
def market_cfg(monkeypatch):
  monkeypatch.setattr(botmod.config, 'MARKET_TIMEZONE', 'America/New_York')
  monkeypatch.setattr(botmod.config, 'MARKET_SESSIONS', SESSIONS)
  monkeypatch.setattr(botmod.config, 'WEEKEND_TRADING_ENABLED', False)


@pytest.mark.parametrize('when,expected', [
  (_et(2026, 9, 23, 12, 0), (True, 'regular')),        # 수 정오 ET
  (_et(2026, 9, 23, 5, 0), (True, 'pre_market')),
  (_et(2026, 9, 23, 17, 0), (True, 'after_market')),
  (_et(2026, 9, 23, 23, 0), (True, 'day_market')),      # 수 밤 → 주간거래
  (_et(2026, 9, 24, 2, 0), (True, 'day_market')),       # 목 새벽, 자정 넘김
  (_et(2026, 9, 23, 3, 55), (False, None)),             # 03:50~04:00 공백
  (_et(2026, 9, 23, 19, 55), (False, None)),            # 19:50~20:00 공백
  (_et(2026, 9, 26, 12, 0), (False, None)),             # 토
  (_et(2026, 9, 27, 12, 0), (False, None)),             # 일 낮
  (_et(2026, 9, 27, 21, 0), (True, 'day_market')),      # 일 밤 → 월요일 KST 주간거래
  (_et(2026, 9, 25, 21, 0), (False, None)),             # 금 밤: 주간거래 없음
  (_et(2026, 9, 25, 15, 0), (True, 'regular')),         # 금 오후 = 토요일 04:00 KST (예전 버그)
  (_et(2026, 12, 2, 12, 0), (True, 'regular')),         # 겨울(EST)에도 동일
])
def test_is_market_open_eastern(make_bot, market_cfg, when, expected):
  b = make_bot()
  assert b.is_market_open(now=when) == expected


def test_friday_regular_session_visible_from_kst_saturday(make_bot, market_cfg):
  """KST 로는 토요일 04:00 이지만 미국은 금요일 15:00 ET 정규장 → 열려 있어야 한다"""
  b = make_bot()
  sat_kst = KST.localize(datetime(2026, 9, 26, 4, 0))
  assert b.is_market_open(now=sat_kst) == (True, 'regular')


def test_dst_shift_moves_kst_boundary_but_not_et(make_bot, market_cfg):
  b = make_bot()
  # 여름: 22:30 KST = 09:30 EDT → 정규장 시작. 겨울: 22:30 KST = 08:30 EST → 프리마켓
  assert b.is_market_open(now=KST.localize(datetime(2026, 9, 23, 22, 30)))[1] == 'regular'
  assert b.is_market_open(now=KST.localize(datetime(2026, 12, 2, 22, 30)))[1] == 'pre_market'


def test_is_market_open_uses_current_time_when_not_given(make_bot, market_cfg, monkeypatch):
  fixed = _et(2026, 9, 23, 12, 0)

  class FrozenDT(datetime):
    @classmethod
    def now(cls, tz=None):
      return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

  monkeypatch.setattr(botmod, 'datetime', FrozenDT)
  b = make_bot()
  assert b.is_market_open() == (True, 'regular')


def test_weekend_trading_can_be_enabled(make_bot, market_cfg, monkeypatch):
  b = make_bot()
  monkeypatch.setattr(botmod.config, 'WEEKEND_TRADING_ENABLED', True)
  assert b.is_market_open(now=_et(2026, 9, 26, 12, 0)) == (True, 'regular')


# ---------------- 텔레그램 /status ----------------

def test_status_command_shows_updated_per_holding_returns(make_bot, monkeypatch):
  """현재가 갱신 후 다시 읽은 포지션으로 종목별 수익률을 표시해야 한다 (갱신 전 목록 사용 시 0%)"""
  import asyncio
  b = make_bot(db_enabled=True, paper=True)
  stale = [{'ticker': 'AAPL', 'avg_price': 100.0, 'current_price': 100.0, 'unrealized_pnl_percent': 0.0}]
  fresh = [{'ticker': 'AAPL', 'avg_price': 100.0, 'current_price': 110.0, 'unrealized_pnl_percent': 10.0}]
  calls = {'n': 0}

  def get_positions():
    calls['n'] += 1
    return stale if calls['n'] == 1 else fresh
  b.db_manager.get_positions.side_effect = get_positions
  b.db_manager.get_portfolio_status.return_value = {
    'total_value': 1100.0, 'current_cash': 0.0, 'positions_value': 1100.0, 'total_return_percent': 10.0, 'num_positions': 1}
  b.data_provider.get_us_stock_price.return_value = {'price': 110.0}
  b.is_market_open = lambda: (True, 'regular')
  b.get_current_session = lambda: '정규장'

  update = MagicMock(); update.message.reply_text = MagicMock()
  async def reply(text, **k): update.message.text_out = text
  update.message.reply_text = reply
  asyncio.run(botmod.status_command(update, MagicMock(), b))

  b.db_manager.update_position_price.assert_called_once_with('AAPL', 110.0)
  assert '▫️ AAPL: +10.00%' in update.message.text_out
  assert '평균 수익률: +10.00%' in update.message.text_out
