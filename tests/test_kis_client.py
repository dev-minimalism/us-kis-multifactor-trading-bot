# test_kis_client.py - KIS 토큰 관리 / 싱글톤
from datetime import datetime, timedelta

import pytest

from multifactor_bot.brokers import kis_client as kc
from tests.conftest import FakeResponse


@pytest.fixture(autouse=True)
def reset_singleton():
  kc.KISClient._instances = {}
  yield
  kc.KISClient._instances = {}


@pytest.fixture
def token_post(monkeypatch):
  calls = []

  def fake_post(url, headers=None, data=None, timeout=None):
    calls.append(url)
    return FakeResponse(200, {'access_token': f'tok{len(calls)}', 'expires_in': 3600})

  monkeypatch.setattr(kc.requests, 'post', fake_post)
  return calls


def test_virtual_flag_selects_base_url(token_post, kis_dict_config):
  c = kc.KISClient('US', config=kis_dict_config)
  assert c.base_url.startswith('https://openapivts.')
  real = kc.KISClient('US', config={**kis_dict_config, 'KIS_VIRTUAL': False})
  assert real.base_url.startswith('https://openapi.')


def test_token_issued_on_init_and_headers_carry_bearer(token_post, kis_dict_config):
  c = kc.KISClient('US', config=kis_dict_config)
  assert c.access_token == 'tok1'
  assert token_post[0].endswith('/oauth2/tokenP')
  h = c.get_headers('TTTS3012R')
  assert h['authorization'] == 'Bearer tok1'
  assert h['tr_id'] == 'TTTS3012R'
  assert len(token_post) == 1  # 유효한 토큰은 재발급하지 않음


def test_token_refreshes_within_five_minutes_of_expiry(token_post, kis_dict_config):
  c = kc.KISClient('US', config=kis_dict_config)
  c.token_expires_at = datetime.now() + timedelta(minutes=4)
  c.get_headers('X')
  assert len(token_post) == 2
  assert c.access_token == 'tok2'


def test_singleton_per_market_and_config(token_post, kis_dict_config):
  a = kc.KISClient('US', config=kis_dict_config)
  b = kc.KISClient('US', config=kis_dict_config)
  assert a is b
  kr = kc.KISClient('KR', config=kis_dict_config)
  assert kr is not a
  assert len(token_post) == 2  # 시장별 1회씩


def test_token_failure_raises_after_retries(monkeypatch, kis_dict_config):
  attempts = []
  monkeypatch.setattr(kc.requests, 'post',
                      lambda *a, **k: attempts.append(1) or FakeResponse(500, {}, text='boom'))
  monkeypatch.setattr(kc.time, 'sleep', lambda s: None)
  with pytest.raises(Exception, match='토큰 갱신 실패'):
    kc.KISClient('US', config=kis_dict_config)
  assert len(attempts) == 3
