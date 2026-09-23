# conftest.py - 공용 픽스처
#
# 모든 테스트는 네트워크(KIS/텔레그램/yfinance)와 PostgreSQL 없이 돌아야 한다.
# - FakeDB      : SQL 호출을 기록하고, 서브스트링으로 매칭한 canned row 를 돌려준다
# - FakeResponse: requests.Response 대체
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest


def norm(sql: str) -> str:
  """공백 정규화 (여러 줄 SQL 비교용)"""
  return ' '.join(sql.split())


class FakeDB:
  """db_manager.DatabaseManager 대체.

  one[substr] / all[substr] 에 등록한 값(또는 params 를 받는 callable)이
  쿼리 문자열에 substr 이 포함될 때 반환된다.
  """

  def __init__(self, account: Optional[Dict] = None):
    self.calls: List[tuple] = []
    self.one: Dict[str, Any] = {}
    self.all: Dict[str, Any] = {}
    self.account = account or {'id': 2, 'mode': 'LIVE', 'initial_capital': 50000.0}
    self.initial_capital_updates: List[tuple] = []

  def _lookup(self, table, query, params):
    for key, val in table.items():
      if key in norm(query):
        return val(params) if callable(val) else val
    return None

  def fetch_one(self, query, params=None):
    self.calls.append(('fetch_one', norm(query), params))
    return self._lookup(self.one, query, params)

  def fetch_all(self, query, params=None):
    self.calls.append(('fetch_all', norm(query), params))
    rows = self._lookup(self.all, query, params)
    return [] if rows is None else list(rows)

  def execute_query(self, query, params=None):
    self.calls.append(('execute', norm(query), params))

  def get_account_info(self, account_id):
    return dict(self.account) if self.account['id'] == account_id else None

  def set_initial_capital(self, account_id, initial_capital):
    self.initial_capital_updates.append((account_id, float(initial_capital)))
    self.account['initial_capital'] = float(initial_capital)

  # --- 조회 헬퍼 ---
  def executed(self, substr: str) -> List[tuple]:
    return [c for c in self.calls if c[0] == 'execute' and substr in c[1]]

  def fetched(self, substr: str) -> List[tuple]:
    return [c for c in self.calls if c[0] != 'execute' and substr in c[1]]


class FakeResponse:
  """requests.Response 대체"""

  def __init__(self, status_code=200, json_body=None, headers=None, text=''):
    self.status_code = status_code
    self._json = json_body if json_body is not None else {}
    self.headers = headers or {}
    self.text = text or str(self._json)

  def json(self):
    return self._json


@pytest.fixture
def fake_db():
  return FakeDB()


@pytest.fixture
def kis_dict_config():
  return {
    'KIS_APP_KEY': 'test-key',
    'KIS_APP_SECRET': 'test-secret',
    'KIS_VIRTUAL': True,
    'KIS_ACCOUNT': '12345678-01',
  }


@pytest.fixture
def fake_kis_client(monkeypatch):
  """kis_trader 가 쓰는 KISClient 를 MagicMock 으로 대체"""
  client = MagicMock()
  client.virtual = True
  client.base_url = 'https://vts.example'
  client.get_headers.side_effect = lambda tr_id: {'tr_id': tr_id}
  monkeypatch.setattr('multifactor_bot.brokers.kis_trader.KISClient', lambda *a, **k: client)
  return client
