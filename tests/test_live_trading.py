# test_live_trading.py - Live 계좌 DB 매니저: 현금, 포지션 병합, KIS 동기화, 입출금 감지
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from multifactor_bot.database import live_trading as lt


@pytest.fixture
def mgr(monkeypatch, fake_db):
  monkeypatch.setattr(lt, 'get_db_manager', lambda: fake_db)
  return lt.LiveTradingManager(account_id=2)


# ---------------- 현금 ----------------

def test_update_cash_balance_synced_sets_last_synced_at(mgr, fake_db):
  mgr.update_cash_balance(1234.5, synced_from_kis=True)
  (kind, sql, params), = fake_db.executed('UPDATE live_account_balance')
  assert 'synced_from_kis = TRUE' in sql and 'last_synced_at = %s' in sql
  assert params[0] == 1234.5 and isinstance(params[1], datetime) and params[2] == 2


def test_update_cash_balance_estimate_preserves_last_synced_at(mgr, fake_db):
  """봇 매매로 인한 추정치 갱신은 last_synced_at 을 건드리면 안 된다.
  건드리면 매매 한 번 뒤 재시작 때 '첫 동기화'로 오인해 initial_capital 을 덮어쓴다."""
  mgr.update_cash_balance(999.0)
  (kind, sql, params), = fake_db.executed('UPDATE live_account_balance')
  assert 'last_synced_at' not in sql
  assert 'synced_from_kis = FALSE' in sql
  assert params == (999.0, 2)


def test_record_buy_deducts_cash_including_commission(mgr, fake_db):
  fake_db.one['SELECT current_cash FROM live_account_balance'] = {'current_cash': 10000.0}
  fake_db.one['INSERT INTO live_trades'] = {'id': 11}
  assert mgr.record_buy('AAPL', 10, 150.0, order_id='O1', commission=1.5) == 11
  (_, _, params), = fake_db.executed('UPDATE live_account_balance')
  assert params[0] == pytest.approx(10000 - (1500 + 1.5))


def test_record_sell_adds_cash_and_computes_pnl(mgr, fake_db):
  fake_db.one['SELECT current_cash FROM live_account_balance'] = {'current_cash': 1000.0}
  fake_db.one['INSERT INTO live_trades'] = {'id': 12}
  mgr.record_sell('AAPL', 10, 165.0, buy_price=150.0, buy_date=datetime(2026, 1, 1), commission=2.0, exit_reason='손절')
  (_, _, ins), = fake_db.fetched('INSERT INTO live_trades')
  # (account, date, ticker, action, shares, price, total, comm, pnl, pnl%, days, reason, order, notes)
  assert ins[3] == 'SELL' and ins[8] == pytest.approx(150.0) and ins[9] == pytest.approx(10.0) and ins[11] == '손절'
  (_, _, upd), = fake_db.executed('UPDATE live_account_balance')
  assert upd[0] == pytest.approx(1000 + 1650 - 2)


# ---------------- 첫 동기화 판정 ----------------

@pytest.mark.parametrize('row,expected', [
  (None, True),
  ({'last_synced_at': None}, True),
  ({'last_synced_at': datetime(2026, 9, 1)}, False),
])
def test_is_first_sync(mgr, fake_db, row, expected):
  fake_db.one['SELECT last_synced_at FROM live_account_balance'] = row
  assert mgr.is_first_sync() is expected


# ---------------- 포지션 병합 ----------------

def test_sync_positions_merges_keeping_db_buy_date(mgr, fake_db, monkeypatch):
  fake_db.all['FROM live_positions'] = [
    {'ticker': 'AAPL', 'shares': 8, 'avg_price': 140.0, 'buy_date': datetime(2026, 8, 1)},
    {'ticker': 'TSLA', 'shares': 3, 'avg_price': 200.0, 'buy_date': datetime(2026, 8, 5)},
  ]
  monkeypatch.setattr(mgr, 'update_position_price', MagicMock())
  result = mgr.sync_positions_from_kis([
    {'ticker': 'AAPL', 'shares': 10, 'avg_price': 150.0, 'current_price': 160.0},
    {'ticker': 'MSFT', 'shares': 5, 'avg_price': 400.0, 'current_price': 0},
  ])
  assert result == {'added': ['MSFT'], 'updated': ['AAPL'], 'removed': ['TSLA']}

  (_, upd_sql, upd_params), = fake_db.executed('UPDATE live_positions')
  assert 'buy_date' not in upd_sql  # DB 의 매수일 유지
  assert upd_params[:2] == (10, 150.0) and upd_params[-1] == 'AAPL'

  (_, ins_sql, ins_params), = fake_db.executed('INSERT INTO live_positions')
  assert 'ON CONFLICT' not in ins_sql  # 동기화는 누적(add_position)이 아니라 절대값
  assert ins_params[1] == 'MSFT' and ins_params[2] == 5
  assert ins_params[5] == 400.0  # current_price 0 → highest = avg_price

  (_, del_sql, del_params), = fake_db.executed('DELETE FROM live_positions')
  assert del_params == (2, 'TSLA')
  assert mgr.update_position_price.call_count == 2


# ---------------- sync_from_kis 오케스트레이션 ----------------

@pytest.fixture
def sync_mgr(mgr, monkeypatch):
  """SQL 대신 메서드 수준으로 스텁해 오케스트레이션 로직만 검증"""
  monkeypatch.setattr(mgr, 'sync_positions_from_kis', MagicMock(return_value={'added': [], 'updated': [], 'removed': []}))
  monkeypatch.setattr(mgr, 'sync_balance_from_kis', MagicMock())
  monkeypatch.setattr(mgr, 'record_cash_flow', MagicMock())
  return mgr


def test_first_sync_sets_initial_capital_from_cash_plus_holdings(sync_mgr, fake_db, monkeypatch):
  monkeypatch.setattr(sync_mgr, 'is_first_sync', lambda: True)
  monkeypatch.setattr(sync_mgr, 'get_cash_balance', lambda: 50000.0)
  r = sync_mgr.sync_from_kis(30000.0, [{'ticker': 'AAPL', 'shares': 10, 'avg_price': 150, 'current_price': 160}], 100)
  assert r['first_sync'] is True
  assert r['initial_capital'] == pytest.approx(30000 + 1600)
  assert fake_db.initial_capital_updates == [(2, 31600.0)]
  assert r['cash_flow'] == 0
  sync_mgr.record_cash_flow.assert_not_called()
  sync_mgr.sync_balance_from_kis.assert_called_once_with(30000.0)
  sync_mgr.sync_positions_from_kis.assert_called_once()


def test_drift_within_threshold_only_overwrites_cash(sync_mgr, fake_db, monkeypatch):
  monkeypatch.setattr(sync_mgr, 'is_first_sync', lambda: False)
  monkeypatch.setattr(sync_mgr, 'get_cash_balance', lambda: 28000.0)
  r = sync_mgr.sync_from_kis(28020.0, [], 100)
  assert r['cash_flow'] == 0
  assert r['initial_capital'] == 50000.0 and fake_db.initial_capital_updates == []
  sync_mgr.record_cash_flow.assert_not_called()
  sync_mgr.sync_balance_from_kis.assert_called_once_with(28020.0)


@pytest.mark.parametrize('kis_cash,expected_flow', [(33000.0, 5000.0), (18000.0, -10000.0)])
def test_cash_diff_above_threshold_is_recorded_as_cash_flow(sync_mgr, fake_db, monkeypatch, kis_cash, expected_flow):
  monkeypatch.setattr(sync_mgr, 'is_first_sync', lambda: False)
  monkeypatch.setattr(sync_mgr, 'get_cash_balance', lambda: 28000.0)
  r = sync_mgr.sync_from_kis(kis_cash, [], 100)
  assert r['cash_flow'] == pytest.approx(expected_flow)
  assert r['initial_capital'] == pytest.approx(50000 + expected_flow)
  assert fake_db.initial_capital_updates == [(2, 50000 + expected_flow)]
  args = sync_mgr.record_cash_flow.call_args
  assert args.args[:3] == (pytest.approx(expected_flow), kis_cash, 28000.0)


def test_threshold_is_exclusive_boundary(sync_mgr, monkeypatch):
  monkeypatch.setattr(sync_mgr, 'is_first_sync', lambda: False)
  monkeypatch.setattr(sync_mgr, 'get_cash_balance', lambda: 1000.0)
  assert sync_mgr.sync_from_kis(1100.0, [], 100)['cash_flow'] == 0      # == threshold → 드리프트
  assert sync_mgr.sync_from_kis(1100.01, [], 100)['cash_flow'] != 0    # > threshold → 입출금


def test_positions_none_skips_position_sync_but_syncs_cash(sync_mgr, monkeypatch):
  monkeypatch.setattr(sync_mgr, 'is_first_sync', lambda: False)
  monkeypatch.setattr(sync_mgr, 'get_cash_balance', lambda: 1000.0)
  r = sync_mgr.sync_from_kis(1000.0, None, 100)
  sync_mgr.sync_positions_from_kis.assert_not_called()
  sync_mgr.sync_balance_from_kis.assert_called_once()
  assert r['positions'] is None


def test_record_cash_flow_sql(mgr, fake_db):
  mgr.record_cash_flow(5000.0, 33000.0, 28000.0, note='n')
  (_, sql, params), = fake_db.executed('INSERT INTO live_cash_flows')
  assert params[0] == 2 and params[2:5] == (5000.0, 33000.0, 28000.0) and params[5] == 'n'
