# kis_data_provider.py - 데이터 제공자 (미국 주식 전용)
"""
yfinance 우선 사용 (빠르고 안정적)
- 미국 주식: yfinance (무료, 제한 없음)
- yfinance 실패시: KIS API 백업
"""
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests

try:
  import yfinance as yf

  YFINANCE_AVAILABLE = True
except ImportError:
  YFINANCE_AVAILABLE = False
  print("⚠️  yfinance 미설치: pip install yfinance")

from multifactor_bot.brokers.kis_client import KISClient


class KISDataProvider:
  """데이터 제공자"""

  def __init__(self, use_yfinance=True, config=None):
    """
    Args:
        use_yfinance: yfinance 사용 여부
        config: KIS API 설정
    """
    self.use_yfinance = use_yfinance and YFINANCE_AVAILABLE
    self.cache = {}
    self.cache_timeout = 300  # 5분

    # KIS Client (미국 주식 백업용)
    try:
      self.us_client = KISClient('US', config=config)
    except:
      self.us_client = None

    if not self.use_yfinance:
      print("⚠️  yfinance 비활성화 - KIS API만 사용")

  def _get_cached(self, key: str):
    """캐시 확인"""
    if key in self.cache:
      data, ts = self.cache[key]
      if time.time() - ts < self.cache_timeout:
        return data
    return None

  def _set_cache(self, key: str, data):
    """캐시 저장"""
    self.cache[key] = (data, time.time())

  # ============================================================
  # 미국 주식 - yfinance 우선
  # ============================================================

  def download(
      self,
      ticker: str,
      start: Optional[str] = None,
      end: Optional[str] = None,
      period: str = '3mo'
  ) -> Optional[pd.DataFrame]:
    """
    미국 주식 데이터 다운로드

    Args:
        ticker: 종목 코드
        start: 시작일 (YYYY-MM-DD)
        end: 종료일 (YYYY-MM-DD)
        period: 기간 ('1mo', '3mo', '6mo', '1y')

    Returns:
        DataFrame with columns: Open, High, Low, Close, Volume
    """
    # 캐시 확인
    cache_key = f"US_{ticker}_{start}_{end}_{period}"
    cached = self._get_cached(cache_key)
    if cached is not None:
      return cached

    # yfinance 우선
    if self.use_yfinance:
      df = self._download_yfinance(ticker, start, end, period)
      if df is not None:
        self._set_cache(cache_key, df)
        return df

    # yfinance 실패 시 KIS API 백업
    if self.us_client:
      df = self._download_us_kis(ticker, start, end, period)
      if df is not None:
        self._set_cache(cache_key, df)
      return df

    return None

  def _download_yfinance(
      self,
      ticker: str,
      start: Optional[str] = None,
      end: Optional[str] = None,
      period: str = '3mo'
  ) -> Optional[pd.DataFrame]:
    """yfinance로 다운로드 (주말/공휴일 대비)"""
    try:
      stock = yf.Ticker(ticker)

      if start and end:
        # 날짜 형식 변환
        if len(start) == 8:
          start = f"{start[:4]}-{start[4:6]}-{start[6:]}"
        if len(end) == 8:
          end = f"{end[:4]}-{end[4:6]}-{end[6:]}"

        # 주말/공휴일 대비: 종료일을 7일 뒤까지 확장
        end_dt = pd.to_datetime(end)
        extended_end = (end_dt + timedelta(days=7)).strftime('%Y-%m-%d')

        df = stock.history(start=start, end=extended_end)
        df = self._naive_index(df)

        # 원래 종료일까지만 필터링 (인덱스를 tz 없는 날짜로 맞춘 뒤 비교해야 TypeError 가 나지 않는다)
        if not df.empty and end:
          df = df[df.index <= end_dt]
      else:
        df = self._naive_index(stock.history(period=period))

      if df.empty:
        return None

      # 표준 형식
      df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
      df.index.name = 'Date'
      return df

    except Exception as e:
      # yfinance 오류는 조용히 처리
      return None

  @staticmethod
  def _naive_index(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance 는 America/New_York tz 가 붙은 DatetimeIndex 를 돌려준다.
    호출자들은 naive 날짜와 비교하므로 tz 를 떼고 자정으로 정규화한다."""
    if df is not None and not df.empty and getattr(df.index, 'tz', None) is not None:
      df = df.copy()
      df.index = df.index.tz_localize(None).normalize()
    return df

  def _download_us_kis(
      self,
      ticker: str,
      start: Optional[str] = None,
      end: Optional[str] = None,
      period: str = '3mo'
  ) -> Optional[pd.DataFrame]:
    """KIS API로 미국 주식 다운로드"""
    if not self.us_client:
      return None

    # 날짜 설정
    if start is None and end is None:
      end_dt = datetime.now()
      delta = self._period_to_timedelta(period)
      start_dt = end_dt - delta
      start = start_dt.strftime("%Y%m%d")
      end = end_dt.strftime("%Y%m%d")
    else:
      if start: start = start.replace("-", "")
      if end: end = end.replace("-", "")

    headers = self.us_client.get_headers("HHDFS00000600")
    url = f"{self.us_client.base_url}/uapi/overseas-price/v1/quotations/inquire-daily-chartprice"
    params = {
      "AUTH": "",
      "EXCD": "NAS",
      "SYMB": ticker,
      "GUBN": "0",
      "BYMD": end,
      "FRMD": start,
      "MODP": "1"
    }

    try:
      res = requests.get(url, headers=headers, params=params, timeout=15)
      if res.status_code != 200:
        return None

      data = res.json()
      if data.get("rt_cd") != "0":
        return None

      df = pd.DataFrame(data["output2"])
      if df.empty:
        return None

      df = df.rename(columns={
        "xymd": "Date",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "last": "Close",
        "tvol": "Volume"
      })
      df["Date"] = pd.to_datetime(df["Date"])
      df.set_index("Date", inplace=True)
      df = df[["Open", "High", "Low", "Close", "Volume"]]
      df = df.astype(float)
      df = df.sort_index()

      return df

    except Exception as e:
      print(f"❌ KIS API 오류 ({ticker}): {e}")
      return None

  # ============================================================
  # 현재가 조회
  # ============================================================

  def get_current_price(self, ticker: str) -> Optional[dict]:
    """미국 주식 현재가 조회"""
    return self.get_us_stock_price(ticker)

  def get_us_stock_price(self, ticker: str) -> Optional[dict]:
    """미국 주식 현재가"""
    if not self.us_client:
      return None

    cache_key = f"us_price_{ticker}"
    cached = self._get_cached(cache_key)
    if cached:
      return cached

    headers = self.us_client.get_headers("HHDFS00000300")
    url = f"{self.us_client.base_url}/uapi/overseas-price/v1/quotations/price"
    params = {"AUTH": "", "EXCD": "NAS", "SYMB": ticker}

    try:
      res = requests.get(url, headers=headers, params=params, timeout=10)
      if res.status_code == 200:
        data = res.json()
        if data.get("rt_cd") == "0":
          output = data["output"]
          last_price = output.get("last", "")
          if last_price and last_price.strip():
            result = {"price": float(last_price)}
            self._set_cache(cache_key, result)
            return result
    except Exception as e:
      print(f"❌ 현재가 오류 ({ticker}): {e}")

    return None

  @staticmethod
  def _period_to_timedelta(period: str) -> timedelta:
    """period 문자열을 timedelta로 변환"""
    if period == "1mo":
      return timedelta(days=30)
    elif period == "3mo":
      return timedelta(days=90)
    elif period == "6mo":
      return timedelta(days=180)
    elif period == "1y":
      return timedelta(days=365)
    else:
      return timedelta(days=90)


# 테스트
if __name__ == "__main__":
  print("=" * 70)
  print("Data Provider 테스트")
  print("=" * 70)

  provider = KISDataProvider()

  # NVDA 테스트
  print("\n[NVDA 테스트]")
  df = provider.download("NVDA", period="1mo")
  if df is not None:
    print(f"✅ 성공: {len(df)}일")
    print(df.tail(3))
  else:
    print("❌ 실패")

  # 현재가
  price = provider.get_us_stock_price("NVDA")
  if price:
    print(f"✅ 현재가: ${price['price']:.2f}")
