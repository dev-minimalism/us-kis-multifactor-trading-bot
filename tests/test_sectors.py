# test_sectors.py - 섹터 정규화, 필터, yfinance 폴백 캐시 (네트워크 없음)
import json
from unittest.mock import MagicMock

import pytest

from multifactor_bot.screening import sectors as sec


@pytest.mark.parametrize('label,expected', [
  ('Information Technology', 'Information Technology'),
  ('technology', 'Information Technology'),           # ICB / yfinance
  ('Telecommunications', 'Communication Services'),   # ICB
  ('Consumer Cyclical', 'Consumer Discretionary'),    # yfinance
  ('Basic Materials', 'Materials'),
  ('Health Care', 'Health Care'),
  ('Healthcare', 'Health Care'),
  ('nan', None), ('', None), (None, None), ('Space Mining', None),
])
def test_canonical_sector(label, expected):
  assert sec.canonical_sector(label) == expected


def test_parse_sectors_env_string():
  assert sec.parse_sectors('Information Technology, technology ,Consumer Cyclical') == \
    ['Information Technology', 'Consumer Discretionary']
  assert sec.parse_sectors('') == [] and sec.parse_sectors(None) == []


def test_update_and_filter_without_network(tmp_path):
  m = sec.SectorMap(cache_dir=tmp_path, use_yfinance_fallback=False)
  n = m.update({'AAPL': 'Information Technology', 'JPM': 'Financials', 'brk.b': 'Financials', 'X': 'Unknown'})
  assert n == 3
  assert m.filter(['AAPL', 'JPM', 'BRK-B', 'ZZZ'], ['Information Technology']) == ['AAPL']
  assert m.filter(['AAPL', 'JPM'], ['technology', 'Financials']) == ['AAPL', 'JPM']
  assert m.filter(['AAPL', 'JPM'], []) == ['AAPL', 'JPM']  # 섹터 미지정 = 통과


def test_overwrite_false_keeps_existing_label(tmp_path):
  m = sec.SectorMap(cache_dir=tmp_path, use_yfinance_fallback=False)
  m.update({'GOOGL': 'Communication Services'})            # S&P GICS
  m.update({'GOOGL': 'Technology'}, overwrite=False)       # NASDAQ ICB 는 덮어쓰지 않음
  assert m.sector_of('GOOGL') == 'Communication Services'


def test_yfinance_fallback_is_cached_to_disk(tmp_path, monkeypatch):
  m = sec.SectorMap(cache_dir=tmp_path)
  calls = []

  def fake_lookup(t):
    calls.append(t)
    return 'Information Technology' if t == 'OLDTECH' else None
  monkeypatch.setattr(m, '_lookup_yfinance', fake_lookup)

  assert m.filter(['OLDTECH', 'OLDBANK'], ['Information Technology']) == ['OLDTECH']
  assert calls == ['OLDTECH', 'OLDBANK']
  cache = json.loads((tmp_path / sec.CACHE_FILENAME).read_text())
  assert cache == {'OLDBANK': None, 'OLDTECH': 'Information Technology'}

  # 새 인스턴스는 캐시만 읽고 조회하지 않는다
  m2 = sec.SectorMap(cache_dir=tmp_path)
  monkeypatch.setattr(m2, '_lookup_yfinance', lambda t: pytest.fail('cached lookup must not hit network'))
  assert m2.sector_of('OLDTECH') == 'Information Technology' and m2.sector_of('OLDBANK') is None


def test_lookup_missing_false_skips_network(tmp_path, monkeypatch):
  m = sec.SectorMap(cache_dir=tmp_path)
  monkeypatch.setattr(m, '_lookup_yfinance', lambda t: pytest.fail('should not be called'))
  assert m.filter(['UNKNOWN'], ['Information Technology'], lookup_missing=False) == []


def test_yfinance_lookup_reads_info_sector(monkeypatch, tmp_path):
  import yfinance
  monkeypatch.setattr(yfinance, 'Ticker', lambda t: MagicMock(info={'sector': 'Technology'}))
  m = sec.SectorMap(cache_dir=tmp_path)
  assert m.sector_of('ANY') == 'Information Technology'
