# sectors.py - 섹터 분류와 섹터 필터 (실봇 스크리너 + 백테스트 공용)
#
# 세 가지 출처의 섹터 라벨을 하나의 이름으로 맞춘다.
#   - Wikipedia S&P 500 표의 "GICS Sector"      (예: Information Technology)
#   - Wikipedia NASDAQ-100 표의 "ICB Industry"  (예: Technology, Telecommunications)
#   - yfinance info['sector']                    (예: Technology, Consumer Cyclical)
# 표에 없는 종목(지수에서 빠진 과거 구성종목 등)은 yfinance 로 조회하고 .cache/sector_map.json 에 저장한다.

import json
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path('.cache')
CACHE_FILENAME = 'sector_map.json'

# 표준 섹터명(GICS) → 동의어 (모두 소문자 비교)
SECTOR_ALIASES: Dict[str, set] = {
  'Information Technology': {'information technology', 'technology', 'tech'},
  'Communication Services': {'communication services', 'telecommunications', 'communication'},
  'Consumer Discretionary': {'consumer discretionary', 'consumer cyclical'},
  'Consumer Staples': {'consumer staples', 'consumer defensive'},
  'Health Care': {'health care', 'healthcare'},
  'Financials': {'financials', 'financial services', 'financial'},
  'Industrials': {'industrials', 'industrial goods and services', 'industrial'},
  'Energy': {'energy'},
  'Materials': {'materials', 'basic materials'},
  'Utilities': {'utilities'},
  'Real Estate': {'real estate'},
}


def canonical_sector(label: Optional[str]) -> Optional[str]:
  """어떤 출처의 라벨이든 표준 섹터명으로. 모르는 라벨은 None."""
  if label is None:
    return None
  key = str(label).strip().lower()
  if not key or key == 'nan':
    return None
  for canonical, aliases in SECTOR_ALIASES.items():
    if key == canonical.lower() or key in aliases:
      return canonical
  return None


def normalize_ticker(ticker: str) -> str:
  return str(ticker).strip().upper().replace('.', '-')


class SectorMap:
  """ticker → 표준 섹터명. 표에서 채우고, 없는 종목은 yfinance 로 보충(캐시)."""

  def __init__(self, cache_dir: Union[str, Path] = DEFAULT_CACHE_DIR, use_yfinance_fallback: bool = True):
    self.cache_path = Path(cache_dir) / CACHE_FILENAME
    self.use_yfinance_fallback = use_yfinance_fallback
    self._map: Dict[str, Optional[str]] = {}
    self._fallback_cache: Dict[str, Optional[str]] = self._load_cache()

  # -------------------------------------------------------------- 채우기
  def update(self, mapping: Dict[str, str], overwrite: bool = True) -> int:
    """표에서 읽은 {ticker: 라벨} 을 반영. 반환: 반영된 개수"""
    n = 0
    for ticker, label in mapping.items():
      t = normalize_ticker(ticker)
      sector = canonical_sector(label)
      if sector is None:
        continue
      if overwrite or t not in self._map:
        self._map[t] = sector
        n += 1
    return n

  # -------------------------------------------------------------- 조회
  def sector_of(self, ticker: str, lookup_missing: bool = True) -> Optional[str]:
    t = normalize_ticker(ticker)
    if t in self._map:
      return self._map[t]
    if t in self._fallback_cache:
      return self._fallback_cache[t]
    if not (lookup_missing and self.use_yfinance_fallback):
      return None
    sector = self._lookup_yfinance(t)
    self._fallback_cache[t] = sector
    self._save_cache()
    return sector

  def filter(self, tickers: Iterable[str], sectors: Iterable[str], lookup_missing: bool = True) -> List[str]:
    """요청 섹터에 속하는 종목만. sectors 가 비어 있으면 그대로 반환."""
    wanted = {canonical_sector(s) for s in sectors}
    wanted.discard(None)
    tickers = list(tickers)
    if not wanted:
      return tickers
    return [t for t in tickers if self.sector_of(t, lookup_missing) in wanted]

  def known(self) -> int:
    return len(self._map)

  # -------------------------------------------------------------- yfinance 폴백 + 캐시
  def _lookup_yfinance(self, ticker: str) -> Optional[str]:
    try:
      import yfinance as yf
      info = yf.Ticker(ticker).info or {}
      return canonical_sector(info.get('sector'))
    except Exception as e:
      logger.debug(f"섹터 조회 실패 {ticker}: {e}")
      return None

  def _load_cache(self) -> Dict[str, Optional[str]]:
    try:
      if self.cache_path.exists():
        return json.loads(self.cache_path.read_text())
    except Exception:
      pass
    return {}

  def _save_cache(self) -> None:
    try:
      self.cache_path.parent.mkdir(parents=True, exist_ok=True)
      self.cache_path.write_text(json.dumps(self._fallback_cache, ensure_ascii=False, indent=0, sort_keys=True))
    except Exception as e:
      logger.debug(f"섹터 캐시 저장 실패: {e}")


def parse_sectors(value: Optional[str]) -> List[str]:
  """환경변수 'Information Technology, Communication Services' → 표준 섹터명 리스트"""
  if not value:
    return []
  out = []
  for part in str(value).split(','):
    sector = canonical_sector(part)
    if sector and sector not in out:
      out.append(sector)
  return out
