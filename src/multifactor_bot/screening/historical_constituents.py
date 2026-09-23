# historical_constituents.py - 시점별(point-in-time) S&P 500 구성종목
#
# 왜 필요한가
#   백테스트 유니버스를 "현재" 구성종목이나 config.WATCHLIST(2025년 시총 상위)로 잡으면
#   과거 시점에 아직 지수에 없던 종목, 결과적으로 살아남은 종목만 후보가 되어
#   생존 편향(survivorship bias)과 사후 정보 편향이 생긴다.
#   리밸런싱 날짜마다 "그날 지수에 실제로 들어 있던 종목"을 써야 한다.
#
# 데이터 소스
#   Wikipedia 의 "Selected changes" 표는 2025-11 이후 페이지에서 사라졌다.
#   대신 GitHub fja05680/sp500 의 sp500_ticker_start_end.csv (ticker, start_date, end_date) 를 쓴다.
#   28KB 짜리 작은 파일이라 로컬 캐시(.cache/)에 저장하고 max_cache_age_days 마다 갱신한다.
#
# 한계
#   - 지수에서 빠진 뒤 상장폐지/합병된 종목은 yfinance 에 가격이 없어 결국 팩터 계산에서 빠진다.
#     구성종목은 맞아도 "가격 데이터가 남아 있는 종목" 쪽으로 약한 생존 편향이 남는다.
#   - NASDAQ-100 은 무료 시점별 자료가 없어 포함하지 않는다.

import io
import ssl
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

import certifi
import pandas as pd

DEFAULT_URL = 'https://raw.githubusercontent.com/fja05680/sp500/master/sp500_ticker_start_end.csv'
DEFAULT_CACHE_DIR = Path('.cache')
CACHE_FILENAME = 'sp500_ticker_start_end.csv'


def normalize_ticker(ticker: str) -> str:
  """yfinance 표기로 통일 (BRK.B → BRK-B)"""
  return str(ticker).strip().upper().replace('.', '-')


class HistoricalSP500:
  """리밸런싱 시점별 S&P 500 구성종목 조회"""

  def __init__(self,
               url: str = DEFAULT_URL,
               cache_dir: Union[str, Path] = DEFAULT_CACHE_DIR,
               max_cache_age_days: int = 7):
    self.url = url
    self.cache_path = Path(cache_dir) / CACHE_FILENAME
    self.max_cache_age_days = max_cache_age_days
    self._frame: Optional[pd.DataFrame] = None

  # ------------------------------------------------------------------ 로드
  @classmethod
  def from_frame(cls, frame: pd.DataFrame) -> 'HistoricalSP500':
    """테스트/오프라인용: ticker,start_date,end_date 프레임을 직접 주입"""
    inst = cls(url='', cache_dir=DEFAULT_CACHE_DIR)
    inst._frame = cls._prepare(frame)
    return inst

  @staticmethod
  def _prepare(frame: pd.DataFrame) -> pd.DataFrame:
    required = {'ticker', 'start_date', 'end_date'}
    missing = required - set(frame.columns)
    if missing:
      raise ValueError(f"구성종목 데이터에 컬럼이 없습니다: {sorted(missing)}")
    df = frame[['ticker', 'start_date', 'end_date']].copy()
    df['ticker'] = df['ticker'].map(normalize_ticker)
    df['start_date'] = pd.to_datetime(df['start_date'], errors='coerce')
    df['end_date'] = pd.to_datetime(df['end_date'], errors='coerce')  # NaT = 아직 편입 중
    return df.dropna(subset=['ticker', 'start_date'])

  def _fetch(self) -> bytes:
    req = urllib.request.Request(self.url, headers={'User-Agent': 'us-kis-multifactor-trading-bot'})
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(req, context=ctx, timeout=30) as res:
      return res.read()

  def _cache_is_fresh(self) -> bool:
    if not self.cache_path.exists():
      return False
    age_days = (time.time() - self.cache_path.stat().st_mtime) / 86400
    return age_days <= self.max_cache_age_days

  def load(self, force_refresh: bool = False) -> pd.DataFrame:
    """캐시가 신선하면 캐시, 아니면 다운로드. 다운로드 실패 시 오래된 캐시라도 사용."""
    if self._frame is not None and not force_refresh:
      return self._frame

    raw: Optional[bytes] = None
    if not force_refresh and self._cache_is_fresh():
      raw = self.cache_path.read_bytes()
    else:
      try:
        raw = self._fetch()
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_bytes(raw)
      except Exception as e:
        if self.cache_path.exists():
          print(f"⚠️ S&P500 구성종목 다운로드 실패 ({e}) → 캐시 사용: {self.cache_path}")
          raw = self.cache_path.read_bytes()
        else:
          raise RuntimeError(f"S&P500 시점별 구성종목을 가져올 수 없습니다: {e}") from e

    self._frame = self._prepare(pd.read_csv(io.BytesIO(raw)))
    return self._frame

  # ------------------------------------------------------------------ 조회
  def constituents_at(self, date: Union[str, datetime, pd.Timestamp]) -> List[str]:
    """해당 날짜에 S&P 500 에 편입되어 있던 종목 (정렬된 리스트)

    편입일 <= date < 제외일. 제외일이 NaT 이면 현재까지 편입 중.
    """
    df = self.load()
    ts = pd.Timestamp(date).normalize()
    active = df[(df['start_date'] <= ts) & (df['end_date'].isna() | (df['end_date'] > ts))]
    return sorted(active['ticker'].unique().tolist())

  def coverage(self) -> tuple:
    """데이터가 커버하는 (최초 편입일, 최근 변경일)"""
    df = self.load()
    last = pd.concat([df['start_date'], df['end_date'].dropna()]).max()
    return df['start_date'].min(), last
