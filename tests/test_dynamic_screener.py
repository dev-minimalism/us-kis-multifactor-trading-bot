# test_dynamic_screener.py - 스크리너 섹터 필터 (네트워크 없음)
from multifactor_bot.screening.dynamic_screener import DynamicScreener


def test_apply_sector_filter_uses_table_sectors_only(tmp_path, monkeypatch):
  s = DynamicScreener(sectors=['Information Technology'])
  s.sector_map.use_yfinance_fallback = False
  s.sector_map.update({'AAPL': 'Information Technology', 'JPM': 'Financials'})
  s.sector_map.update({'NVDA': 'Technology'}, overwrite=False)  # NASDAQ ICB 라벨
  assert sorted(s.apply_sector_filter(['AAPL', 'JPM', 'NVDA', 'UNKNOWN'])) == ['AAPL', 'NVDA']


def test_no_sectors_means_passthrough():
  s = DynamicScreener()
  assert s.apply_sector_filter(['AAPL', 'JPM']) == ['AAPL', 'JPM']
