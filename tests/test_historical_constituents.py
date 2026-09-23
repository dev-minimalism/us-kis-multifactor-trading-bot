# test_historical_constituents.py - 시점별 S&P 500 구성종목 (네트워크 없음)
import time

import pandas as pd
import pytest

from multifactor_bot.screening import historical_constituents as hc


@pytest.fixture
def frame():
  return pd.DataFrame({
    'ticker': ['AAPL', 'BRK.B', 'OLD', 'NEW', 'BRIEF', 'BAD'],
    'start_date': ['2000-01-01', '2010-06-01', '2005-01-01', '2021-03-15', '2019-01-01', None],
    'end_date': [None, None, '2020-06-30', None, '2019-12-31', None],
  })


def test_membership_window_is_start_inclusive_end_exclusive(frame):
  h = hc.HistoricalSP500.from_frame(frame)
  assert h.constituents_at('2020-01-01') == ['AAPL', 'BRK-B', 'OLD']
  assert 'OLD' in h.constituents_at('2020-06-29')
  assert 'OLD' not in h.constituents_at('2020-06-30')          # 제외일 당일은 빠짐
  assert 'NEW' not in h.constituents_at('2021-03-14')
  assert 'NEW' in h.constituents_at('2021-03-15')              # 편입일 당일부터 포함
  assert 'BRIEF' in h.constituents_at('2019-06-01') and 'BRIEF' not in h.constituents_at('2020-01-01')


def test_ticker_normalized_to_yfinance_style(frame):
  assert 'BRK-B' in hc.HistoricalSP500.from_frame(frame).constituents_at('2026-01-01')
  assert hc.normalize_ticker(' brk.b ') == 'BRK-B'


def test_rows_without_start_date_are_dropped(frame):
  assert 'BAD' not in hc.HistoricalSP500.from_frame(frame).constituents_at('2026-01-01')


def test_accepts_datetime_and_timestamp(frame):
  h = hc.HistoricalSP500.from_frame(frame)
  from datetime import datetime
  assert h.constituents_at(datetime(2020, 1, 1, 15, 30)) == h.constituents_at(pd.Timestamp('2020-01-01'))


def test_missing_columns_raise():
  with pytest.raises(ValueError, match='컬럼'):
    hc.HistoricalSP500.from_frame(pd.DataFrame({'ticker': ['A']}))


def test_coverage(frame):
  first, last = hc.HistoricalSP500.from_frame(frame).coverage()
  assert first == pd.Timestamp('2000-01-01') and last == pd.Timestamp('2021-03-15')


CSV = b"ticker,start_date,end_date\nAAPL,2000-01-01,\nOLD,2005-01-01,2020-06-30\n"


def test_load_downloads_and_writes_cache(tmp_path, monkeypatch):
  h = hc.HistoricalSP500(cache_dir=tmp_path)
  calls = []
  monkeypatch.setattr(h, '_fetch', lambda: calls.append(1) or CSV)
  assert h.constituents_at('2010-01-01') == ['AAPL', 'OLD']
  assert (tmp_path / hc.CACHE_FILENAME).read_bytes() == CSV
  assert len(calls) == 1
  h.constituents_at('2011-01-01')
  assert len(calls) == 1  # 메모리 캐시


def test_load_uses_fresh_cache_without_network(tmp_path, monkeypatch):
  (tmp_path / hc.CACHE_FILENAME).write_bytes(CSV)
  h = hc.HistoricalSP500(cache_dir=tmp_path, max_cache_age_days=7)
  monkeypatch.setattr(h, '_fetch', lambda: pytest.fail('should not download when cache is fresh'))
  assert h.constituents_at('2010-01-01') == ['AAPL', 'OLD']


def test_stale_cache_triggers_refresh(tmp_path, monkeypatch):
  cache = tmp_path / hc.CACHE_FILENAME
  cache.write_bytes(CSV)
  old = time.time() - 30 * 86400
  import os; os.utime(cache, (old, old))
  h = hc.HistoricalSP500(cache_dir=tmp_path, max_cache_age_days=7)
  monkeypatch.setattr(h, '_fetch', lambda: CSV.replace(b'OLD,2005-01-01,2020-06-30\n', b''))
  assert h.constituents_at('2010-01-01') == ['AAPL']  # 새 데이터 반영


def test_download_failure_falls_back_to_stale_cache(tmp_path, monkeypatch):
  cache = tmp_path / hc.CACHE_FILENAME
  cache.write_bytes(CSV)
  old = time.time() - 30 * 86400
  import os; os.utime(cache, (old, old))
  h = hc.HistoricalSP500(cache_dir=tmp_path)

  def boom():
    raise ConnectionError('offline')
  monkeypatch.setattr(h, '_fetch', boom)
  assert h.constituents_at('2010-01-01') == ['AAPL', 'OLD']


def test_download_failure_without_cache_raises(tmp_path, monkeypatch):
  h = hc.HistoricalSP500(cache_dir=tmp_path)

  def boom():
    raise ConnectionError('offline')
  monkeypatch.setattr(h, '_fetch', boom)
  with pytest.raises(RuntimeError, match='구성종목'):
    h.constituents_at('2010-01-01')
