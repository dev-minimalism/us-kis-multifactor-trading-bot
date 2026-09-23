# test_kis_trader.py - 잔고/보유종목 조회, 주문 (requests 모킹)
import json

import pytest

from multifactor_bot.brokers import kis_trader as kt
from tests.conftest import FakeResponse


@pytest.fixture
def trader(fake_kis_client, kis_dict_config):
  return kt.KISTrader(config=kis_dict_config)


def test_account_parsing(trader):
  assert trader.account_no == '12345678'
  assert trader.product_code == '01'


# ---------------- get_balance ----------------

def test_get_balance_parses_fields_and_sends_required_params(monkeypatch, trader):
  seen = {}

  def fake_get(url, headers=None, params=None, timeout=None):
    seen['url'], seen['params'], seen['tr_id'] = url, params, headers['tr_id']
    return FakeResponse(200, {'rt_cd': '0', 'output': {
      'ord_psbl_frcr_amt': '1234.56', 'ovrs_ord_psbl_amt': '2000.00', 'exrt': '1360.0'}})

  monkeypatch.setattr(kt.requests, 'get', fake_get)
  b = trader.get_balance()
  assert b == {'total_cash': 1234.56, 'available_cash': 2000.0, 'exchange_rate': 1360.0}
  assert seen['url'].endswith('/trading/inquire-psamount')
  # KIS 가 필수로 요구하는 종목코드/주문단가가 빠지면 "상품이 없습니다" 로 실패한다
  assert seen['params']['ITEM_CD'] == 'AAPL'
  assert seen['params']['OVRS_ORD_UNPR'] == '1'
  assert seen['tr_id'] == 'VTTS3007R'  # 모의


def test_get_balance_uses_real_tr_id_when_not_virtual(monkeypatch, fake_kis_client, kis_dict_config):
  fake_kis_client.virtual = False
  trader = kt.KISTrader(config=kis_dict_config)
  seen = {}
  monkeypatch.setattr(kt.requests, 'get', lambda url, headers=None, params=None, timeout=None:
                      seen.update(tr=headers['tr_id']) or FakeResponse(200, {'rt_cd': '0', 'output': {}}))
  trader.get_balance()
  assert seen['tr'] == 'TTTS3007R'


@pytest.mark.parametrize('resp', [
  FakeResponse(200, {'rt_cd': '1', 'msg1': '상품이 없습니다'}),
  FakeResponse(500, {'rt_cd': '1', 'msg1': '서버 오류'}),
])
def test_get_balance_returns_none_on_api_error(monkeypatch, trader, resp):
  monkeypatch.setattr(kt.requests, 'get', lambda *a, **k: resp)
  assert trader.get_balance() is None


def test_get_balance_returns_none_on_exception(monkeypatch, trader):
  def boom(*a, **k):
    raise ConnectionError('down')
  monkeypatch.setattr(kt.requests, 'get', boom)
  assert trader.get_balance() is None


# ---------------- get_positions ----------------

def test_get_positions_follows_pagination_and_skips_zero_qty(monkeypatch, trader):
  pages = [
    FakeResponse(200, {'rt_cd': '0', 'ctx_area_fk200': 'FK1', 'ctx_area_nk200': 'NK1', 'output1': [
      {'ovrs_pdno': 'AAPL ', 'ovrs_cblc_qty': '10', 'pchs_avg_pric': '150.5', 'now_pric2': '160', 'ovrs_excg_cd': 'NASD'},
      {'ovrs_pdno': 'ZERO', 'ovrs_cblc_qty': '0', 'pchs_avg_pric': '1', 'now_pric2': '1', 'ovrs_excg_cd': 'NASD'},
    ]}, headers={'tr_cont': 'M'}),
    FakeResponse(200, {'rt_cd': '0', 'output1': [
      {'ovrs_pdno': 'MSFT', 'ovrs_cblc_qty': '5', 'pchs_avg_pric': '400', 'now_pric2': '', 'ovrs_excg_cd': 'NASD'},
    ]}, headers={'tr_cont': ''}),
  ]
  seen_params = []

  def fake_get(url, headers=None, params=None, timeout=None):
    seen_params.append(params)
    return pages.pop(0)

  monkeypatch.setattr(kt.requests, 'get', fake_get)
  pos = trader.get_positions()
  assert [p['ticker'] for p in pos] == ['AAPL', 'MSFT']
  assert pos[0] == {'ticker': 'AAPL', 'shares': 10, 'avg_price': 150.5, 'current_price': 160.0, 'exchange': 'NASD'}
  assert pos[1]['current_price'] == 0.0  # 빈 문자열 → 0
  assert seen_params[0]['CTX_AREA_FK200'] == ''
  assert seen_params[1]['CTX_AREA_FK200'] == 'FK1' and seen_params[1]['CTX_AREA_NK200'] == 'NK1'
  assert seen_params[0]['OVRS_EXCG_CD'] == 'NASD' and seen_params[0]['TR_CRCY_CD'] == 'USD'


def test_get_positions_returns_empty_list_when_no_holdings(monkeypatch, trader):
  monkeypatch.setattr(kt.requests, 'get', lambda *a, **k: FakeResponse(200, {'rt_cd': '0', 'output1': []}))
  assert trader.get_positions() == []


def test_get_positions_returns_none_on_error(monkeypatch, trader):
  monkeypatch.setattr(kt.requests, 'get', lambda *a, **k: FakeResponse(200, {'rt_cd': '7', 'msg1': 'err'}))
  assert trader.get_positions() is None


# ---------------- 주문 ----------------

def test_buy_and_sell_dry_run_do_not_call_api(monkeypatch, trader):
  monkeypatch.setattr(kt.requests, 'post', lambda *a, **k: pytest.fail('API must not be called in dry run'))
  assert trader.buy_stock('NVDA', 3, dry_run=True)['success'] is True
  assert trader.sell_stock('NVDA', 3, dry_run=True)['success'] is True


def test_buy_stock_real_posts_order_and_returns_order_no(monkeypatch, trader):
  seen = {}

  def fake_post(url, headers=None, data=None, timeout=None):
    seen['url'], seen['data'], seen['tr'] = url, json.loads(data), headers['tr_id']
    return FakeResponse(200, {'rt_cd': '0', 'msg1': 'OK', 'output': {'ODNO': '0001'}})

  monkeypatch.setattr(kt.requests, 'post', fake_post)
  r = trader.buy_stock('NVDA', 3, dry_run=False)
  assert r == {'success': True, 'message': 'OK', 'order_no': '0001'}
  assert seen['url'].endswith('/trading/order')
  assert seen['data']['PDNO'] == 'NVDA' and seen['data']['ORD_QTY'] == '3'
  assert seen['tr'] == 'VTTT1002U'


def test_sell_stock_failure_message(monkeypatch, trader):
  monkeypatch.setattr(kt.requests, 'post', lambda *a, **k: FakeResponse(200, {'rt_cd': '1', 'msg1': '잔고부족'}))
  r = trader.sell_stock('NVDA', 3, dry_run=False)
  assert r['success'] is False and r['message'] == '잔고부족'
