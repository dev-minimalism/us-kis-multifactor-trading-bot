# test_kis_data_provider.py - yfinance tz-aware 인덱스 처리 (네트워크 없음)
from unittest.mock import MagicMock

import pandas as pd
import pytest

from multifactor_bot.brokers import kis_data_provider as kdp


@pytest.fixture
def provider(monkeypatch):
  monkeypatch.setattr(kdp, 'KISClient', lambda *a, **k: None)
  p = kdp.KISDataProvider.__new__(kdp.KISDataProvider)
  p.use_yfinance = True; p.us_client = None; p._cache = {}
  p._get_cached = lambda k: None; p._set_cache = lambda k, v: None
  return p


def _tz_frame():
  idx = pd.date_range('2020-01-02 09:30', periods=5, freq='B', tz='America/New_York')
  return pd.DataFrame({'Open': 1.0, 'High': 1.0, 'Low': 1.0, 'Close': [1, 2, 3, 4, 5.0], 'Volume': 10, 'Dividends': 0}, index=idx)


def test_start_end_path_returns_naive_index_filtered_to_end(provider, monkeypatch):
  """yfinance 의 tz-aware 인덱스를 naive 날짜와 비교하다 TypeError → None 이던 버그"""
  monkeypatch.setattr(kdp.yf, 'Ticker', lambda t: MagicMock(history=lambda **k: _tz_frame()))
  df = provider.download('AAPL', start='2020-01-01', end='2020-01-06')
  assert df is not None and list(df.columns) == ['Open', 'High', 'Low', 'Close', 'Volume']
  assert df.index.tz is None
  assert df.index.max() == pd.Timestamp('2020-01-06') and len(df) == 3


def test_period_path_also_naive(provider, monkeypatch):
  monkeypatch.setattr(kdp.yf, 'Ticker', lambda t: MagicMock(history=lambda **k: _tz_frame()))
  df = provider.download('AAPL', period='1y')
  assert df is not None and df.index.tz is None and len(df) == 5


def test_empty_history_returns_none(provider, monkeypatch):
  monkeypatch.setattr(kdp.yf, 'Ticker', lambda t: MagicMock(history=lambda **k: pd.DataFrame()))
  assert provider.download('AAPL', start='2020-01-01', end='2020-01-06') is None
