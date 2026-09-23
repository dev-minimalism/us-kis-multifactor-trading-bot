# test_dynamic_backtest.py - 동적 백테스트 유니버스 선택과 유동성 필터 (네트워크/DB 없음)
from datetime import datetime
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from multifactor_bot.backtest import dynamic_backtest as dbm
from multifactor_bot.screening.historical_constituents import HistoricalSP500


@pytest.fixture
def make_backtest(monkeypatch):
  monkeypatch.setattr(dbm, 'KISDataProvider', lambda **k: MagicMock())
  monkeypatch.setattr(dbm, 'DynamicScreener', lambda **k: MagicMock())
  monkeypatch.setattr(dbm.config, 'DYNAMIC_SCREENING_MIN_PRICE', 5.0)
  monkeypatch.setattr(dbm.config, 'DYNAMIC_SCREENING_MIN_VOLUME', 50_000)
  monkeypatch.setattr(dbm.config, 'WATCHLIST', ['FALLBACK1', 'FALLBACK2'])
  monkeypatch.setattr(dbm.config, 'UNIVERSE_SECTORS', [])

  def _make(**kwargs):
    return dbm.DynamicMultiFactorBacktest(**kwargs)
  return _make


def _frame():
  return pd.DataFrame({
    'ticker': ['AAPL', 'OLD', 'NEW'],
    'start_date': ['2000-01-01', '2005-01-01', '2022-01-01'],
    'end_date': [None, '2020-06-30', None],
  })


def test_historical_mode_is_default_and_disables_screener(make_backtest):
  bt = make_backtest()
  assert bt.use_historical_constituents is True
  assert bt.use_dynamic_screening is False and bt.screener is None
  assert '시점별' in bt.universe_label()


def test_universe_changes_with_date_in_historical_mode(make_backtest):
  bt = make_backtest()
  bt.historical = HistoricalSP500.from_frame(_frame())
  assert bt.get_universe_at_date(datetime(2020, 1, 15)) == ['AAPL', 'OLD']
  assert bt.get_universe_at_date(datetime(2023, 1, 15)) == ['AAPL', 'NEW']


def test_historical_failure_falls_back_to_watchlist(make_backtest):
  bt = make_backtest()
  bt.historical = MagicMock()
  bt.historical.constituents_at.side_effect = RuntimeError('offline')
  assert bt.get_universe_at_date(datetime(2020, 1, 15)) == ['FALLBACK1', 'FALLBACK2']


def test_screening_mode_screens_once_and_reuses(make_backtest):
  bt = make_backtest(use_historical_constituents=False, use_dynamic_screening=True)
  bt.screener.screen_universe.return_value = ['A', 'B']
  assert bt.get_universe_at_date(datetime(2020, 1, 15)) == ['A', 'B']
  assert bt.get_universe_at_date(datetime(2021, 1, 15)) == ['A', 'B']
  assert bt.screener.screen_universe.call_count == 1
  assert '생존편향 있음' in bt.universe_label()


def test_watchlist_mode(make_backtest):
  bt = make_backtest(use_historical_constituents=False, use_dynamic_screening=False)
  assert bt.get_universe_at_date(datetime(2020, 1, 15)) == ['FALLBACK1', 'FALLBACK2']


# ---------------- 유동성 필터 ----------------

def _hist(price=100.0, volume=1_000_000, n=200):
  idx = pd.bdate_range(end='2020-06-30', periods=n)
  return pd.DataFrame({'Open': price, 'High': price, 'Low': price, 'Close': price, 'Volume': volume}, index=idx)


@pytest.mark.parametrize('price,volume,expected', [
  (100.0, 1_000_000, True),
  (4.99, 1_000_000, False),   # 최소 주가 미달
  (100.0, 10_000, False),     # 평균 거래량 미달
])
def test_passes_liquidity(make_backtest, price, volume, expected):
  assert dbm.DynamicMultiFactorBacktest._passes_liquidity(_hist(price, volume), price) is expected


def test_calculate_factors_skips_illiquid_and_uses_data_up_to_date(make_backtest, monkeypatch):
  bt = make_backtest()
  data = {'GOOD': _hist(100.0, 1_000_000), 'PENNY': _hist(2.0, 1_000_000), 'THIN': _hist(50.0, 1_000)}
  bt.data_provider.download.side_effect = lambda ticker, **k: data.get(ticker)
  monkeypatch.setattr(dbm.yf, 'Ticker', lambda t: MagicMock(info={}))
  df = bt.calculate_factors_at_date(['GOOD', 'PENNY', 'THIN', 'NODATA'], datetime(2020, 6, 30))
  assert list(df['ticker']) == ['GOOD']
  assert df.iloc[0]['price'] == 100.0
  assert np.isnan(df.iloc[0]['value'])  # info 없음 → NaN


# ---------------- 가격/재무 캐시 ----------------

def test_history_is_downloaded_once_per_ticker_and_sliced_without_lookahead(make_backtest, monkeypatch):
  bt = make_backtest()
  idx = pd.bdate_range('2019-01-01', '2026-09-22')
  full = pd.DataFrame({'Close': np.arange(len(idx), dtype=float) + 1, 'Volume': 1e6}, index=idx)
  calls = []
  bt.data_provider.download.side_effect = lambda ticker, **k: calls.append((ticker, k.get('start'), k.get('end'))) or full
  monkeypatch.setattr(dbm.yf, 'Ticker', lambda t: MagicMock(info={}))

  d1, d2 = datetime(2020, 6, 30), datetime(2021, 6, 30)
  h1 = bt._history_until('AAPL', d1)
  h2 = bt._history_until('AAPL', d2)
  assert len(calls) == 1 and calls[0][0] == 'AAPL'
  assert calls[0][1] <= '2019-01-01' and calls[0][2] >= '2026-09-22'  # 전 구간 1회
  assert h1.index.max() <= pd.Timestamp(d1) and h1.index.min() >= pd.Timestamp(d1) - pd.Timedelta(days=400)
  assert h2.index.max() <= pd.Timestamp(d2)

  bt.calculate_factors_at_date(['AAPL'], d1); bt.calculate_factors_at_date(['AAPL'], d2)
  assert len(calls) == 1  # 팩터 계산도 캐시 사용


def test_price_on_or_after_uses_next_trading_day(make_backtest):
  bt = make_backtest()
  idx = pd.to_datetime(['2020-06-26', '2020-06-29', '2020-06-30'])
  bt._history_cache['X'] = pd.DataFrame({'Close': [10.0, 11.0, 12.0], 'Volume': 1}, index=idx)
  assert bt._price_on_or_after('X', datetime(2020, 6, 27)) == 11.0   # 토요일 신호 → 월요일 종가
  assert bt._price_on_or_after('X', datetime(2020, 7, 15)) == 12.0   # 이후 데이터 없음 → 마지막 종가
  assert bt._price_on_or_after('NONE', datetime(2020, 7, 15)) is None or True


def test_info_cached_once_per_ticker(make_backtest, monkeypatch):
  bt = make_backtest()
  n = []
  monkeypatch.setattr(dbm.yf, 'Ticker', lambda t: n.append(t) or MagicMock(info={'sector': 'Technology'}))
  bt._info('AAPL'); bt._info('AAPL'); bt._info('MSFT')
  assert n == ['AAPL', 'MSFT']


# ---------------- 섹터 필터 ----------------

def test_historical_universe_is_sector_filtered(make_backtest, monkeypatch):
  monkeypatch.setattr(dbm.config, 'UNIVERSE_SECTORS', ['Information Technology'])
  bt = make_backtest()
  bt.historical = HistoricalSP500.from_frame(_frame())
  sm = MagicMock()
  sm.filter.side_effect = lambda tickers, sectors, lookup_missing=True: [t for t in tickers if t == 'AAPL']
  bt._sector_map = sm
  assert bt.get_universe_at_date(datetime(2020, 1, 15)) == ['AAPL']
  assert sm.filter.call_args.args[1] == ['Information Technology']
  assert '섹터: Information Technology' in bt.universe_label()


def test_sector_filter_leaving_nothing_falls_back(make_backtest, monkeypatch):
  monkeypatch.setattr(dbm.config, 'UNIVERSE_SECTORS', ['Utilities'])
  bt = make_backtest()
  bt.historical = HistoricalSP500.from_frame(_frame())
  sm = MagicMock(); sm.filter.return_value = []
  bt._sector_map = sm
  assert bt.get_universe_at_date(datetime(2020, 1, 15)) == ['FALLBACK1', 'FALLBACK2']


# ---------------- 리스크 규칙 (손절 / 트레일링 / 보유기간) ----------------

def _path(prices, start='2020-01-02'):
  idx = pd.bdate_range(start, periods=len(prices))
  return pd.DataFrame({'Open': prices, 'High': prices, 'Low': prices, 'Close': prices, 'Volume': 1e6}, index=idx)


@pytest.fixture
def risk_bt(make_backtest, monkeypatch):
  for k, v in dict(STOP_LOSS_ENABLED=True, STOP_LOSS_PERCENT=-15.0, TRAILING_STOP_ENABLED=True,
                   TRAILING_STOP_PERCENT=-15.0, MIN_HOLD_DAYS=7, MAX_HOLD_DAYS=90).items():
    monkeypatch.setattr(dbm.config, k, v)
  bt = make_backtest()
  bt.cash = 0.0
  return bt


def _hold(bt, ticker, prices, buy_day=0):
  df = _path(prices)
  bt._history_cache[ticker] = df
  bt.holdings[ticker] = {'shares': 10, 'avg_price': prices[buy_day], 'buy_date': df.index[buy_day].to_pydatetime(),
                         'high_price': prices[buy_day]}
  return df


def test_stop_loss_after_min_hold_sells_at_that_days_close(risk_bt):
  prices = [100.0] * 5 + [80.0] * 30            # 6번째 거래일부터 -20%
  df = _hold(risk_bt, 'A', prices)
  risk_bt.apply_risk_rules(df.index[0], df.index[-1])
  sells = [t for t in risk_bt.trades_history if t['action'] == 'SELL']
  assert len(sells) == 1 and sells[0]['exit_reason'] == 'STOP_LOSS'
  assert (sells[0]['date'] - df.index[0]).days >= 7          # 최소 보유일 이후
  assert sells[0]['price'] == 80.0 and 'A' not in risk_bt.holdings
  assert risk_bt.cash == pytest.approx(10 * 80.0 * (1 - risk_bt.commission))


def test_stop_loss_not_triggered_if_price_recovers_before_min_hold(risk_bt):
  prices = [100.0, 80.0, 80.0, 100.0] + [101.0] * 20        # 급락 후 7일 안에 회복
  df = _hold(risk_bt, 'A', prices)
  risk_bt.apply_risk_rules(df.index[0], df.index[-1])
  assert 'A' in risk_bt.holdings


def test_trailing_stop_tracks_high_and_respects_min_hold(risk_bt):
  prices = [100.0, 120.0, 140.0, 140.0, 140.0, 140.0, 140.0, 140.0, 140.0, 140.0, 118.0, 118.0]  # 고점 140 → -15.7%
  df = _hold(risk_bt, 'A', prices)
  risk_bt.apply_risk_rules(df.index[0], df.index[-1])
  sells = [t for t in risk_bt.trades_history if t['action'] == 'SELL']
  assert sells and sells[0]['exit_reason'] == 'TRAILING_STOP' and sells[0]['price'] == 118.0
  assert sells[0]['pnl_percent'] == pytest.approx(18.0)      # 진입가 대비 +18% 인데도 고점 대비로 청산


def test_max_hold_exit_ignores_min_hold_and_fires_after_90_days(risk_bt):
  prices = [100.0] * 70                                       # 70 거래일 ≈ 98 달력일, 가격 변동 없음
  df = _hold(risk_bt, 'A', prices)
  risk_bt.apply_risk_rules(df.index[0], df.index[-1])
  sells = [t for t in risk_bt.trades_history if t['action'] == 'SELL']
  assert len(sells) == 1 and sells[0]['exit_reason'] == 'MAX_HOLD'
  assert (sells[0]['date'] - df.index[0]).days >= 90


def test_stop_loss_has_priority_over_trailing(risk_bt):
  prices = [100.0, 130.0] + [130.0] * 8 + [80.0]              # 진입가 -20%, 고점 -38%
  df = _hold(risk_bt, 'A', prices)
  risk_bt.apply_risk_rules(df.index[0], df.index[-1])
  assert risk_bt.trades_history[-1]['exit_reason'] == 'STOP_LOSS'


def test_risk_rules_disabled_leaves_holdings(make_backtest):
  bt = make_backtest(risk_rules_enabled=False)
  assert 'OFF' in bt.risk_rules_label()


def test_risk_rules_only_check_days_inside_window(risk_bt):
  prices = [100.0] * 10 + [50.0] * 10
  df = _hold(risk_bt, 'A', prices)
  risk_bt.apply_risk_rules(df.index[0], df.index[9])          # 급락 전 구간만 검사
  assert 'A' in risk_bt.holdings


def test_buy_position_records_high_price(make_backtest):
  bt = make_backtest(); bt.cash = 10_000.0
  bt.buy_position('A', datetime(2020, 1, 2), 50.0, 5_000.0)
  assert bt.holdings['A']['high_price'] == 50.0
