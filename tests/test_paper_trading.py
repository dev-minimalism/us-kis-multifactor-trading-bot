# test_paper_trading.py - Paper 계좌 DB 매니저
from datetime import datetime

import pytest

from multifactor_bot.database import paper_trading as pt


@pytest.fixture
def mgr(monkeypatch, fake_db):
  fake_db.account = {'id': 1, 'mode': 'PAPER', 'initial_capital': 100000.0}
  monkeypatch.setattr(pt, 'get_db_manager', lambda: fake_db)
  return pt.PaperTradingManager(account_id=1)


def test_get_cash_balance_defaults_to_zero_without_row(mgr, fake_db):
  assert mgr.get_cash_balance() == 0.0


def test_record_buy_deducts_cash_and_returns_trade_id(mgr, fake_db):
  fake_db.one['SELECT current_cash FROM paper_account_balance'] = {'current_cash': 1000.0}
  fake_db.one['INSERT INTO paper_trades'] = {'id': 5}
  assert mgr.record_buy('AAPL', 2, 100.0, commission=0.2) == 5
  (_, _, params), = fake_db.executed('UPDATE paper_account_balance')
  assert params == (pytest.approx(1000 - 200.2), 1)


def test_record_sell_adds_cash_and_records_pnl(mgr, fake_db):
  fake_db.one['SELECT current_cash FROM paper_account_balance'] = {'current_cash': 500.0}
  fake_db.one['INSERT INTO paper_trades'] = {'id': 6}
  mgr.record_sell('AAPL', 2, 90.0, buy_price=100.0, buy_date=datetime(2026, 1, 1), exit_reason='손절')
  (_, _, ins), = fake_db.fetched('INSERT INTO paper_trades')
  assert ins[8] == pytest.approx(-20.0) and ins[9] == pytest.approx(-10.0) and ins[11] == '손절'
  (_, _, upd), = fake_db.executed('UPDATE paper_account_balance')
  assert upd[0] == pytest.approx(500 + 180)


def test_add_position_upserts_with_weighted_average(mgr, fake_db):
  mgr.add_position('AAPL', 3, 100.0, buy_date=datetime(2026, 9, 1))
  (_, sql, params), = fake_db.executed('INSERT INTO paper_positions')
  assert 'ON CONFLICT (account_id, ticker) DO UPDATE' in sql
  assert params[1:4] == ('AAPL', 3, 100.0)


def test_reset_trading_data_clears_tables_and_restores_initial_capital(mgr, fake_db):
  fake_db.all['FROM paper_positions'] = [{'ticker': 'AAPL'}, {'ticker': 'MSFT'}]
  fake_db.one['SELECT COUNT(*) as cnt FROM paper_trades'] = {'cnt': 7}
  fake_db.one['SELECT COUNT(*) as cnt FROM paper_portfolio_snapshots'] = {'cnt': 3}
  fake_db.one['SELECT current_cash FROM paper_account_balance'] = {'current_cash': 12.0}

  result = mgr.reset_trading_data()

  for table in ('paper_positions', 'paper_trades', 'paper_portfolio_snapshots'):
    assert fake_db.executed(f'DELETE FROM {table}'), table
  (_, _, upd), = fake_db.executed('UPDATE paper_account_balance')
  assert upd[0] == 100000.0  # accounts.initial_capital
  assert result['positions_deleted'] == 2 and result['trades_deleted'] == 7 and result['snapshots_deleted'] == 3


def test_reset_trading_data_accepts_explicit_capital(mgr, fake_db):
  mgr.reset_trading_data(initial_cash=25000)
  (_, _, upd), = fake_db.executed('UPDATE paper_account_balance')
  assert upd[0] == 25000
