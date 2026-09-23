# dynamic_screener.py - 동적 종목 스크리닝 (S&P500 + NASDAQ100)

import warnings
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import ssl
import urllib.request
import time

import certifi

from multifactor_bot.screening.sectors import SectorMap

warnings.filterwarnings('ignore')
logger = logging.getLogger(__name__)

# Wikipedia 호출용 TLS 컨텍스트.
# 예전에는 macOS 기본 CA 문제 때문에 ssl 검증을 프로세스 전체에서 껐지만,
# 그러면 KIS API 등 모든 HTTPS 가 MITM 에 노출된다. certifi 번들로 정상 검증한다.
def _https_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


class DynamicScreener:
    """
    동적 종목 스크리닝
    - S&P500 + NASDAQ100에서 실시간 필터링
    - 시총/가격/거래량 조건 적용
    - 캐싱으로 성능 최적화
    """

    def __init__(self,
                 min_market_cap: float = 500_000_000,    # $500M (사용 안 함)
                 min_price: float = 5.0,                 # $5
                 min_avg_volume: int = 50_000,           # 50K shares
                 max_workers: int = 5,                   # Rate limit 방지
                 check_market_cap: bool = False,         # 시총 체크 비활성화
                 sectors: Optional[List[str]] = None):   # 섹터 제한 (None/빈 리스트 = 전 섹터)
        """
        Args:
            min_market_cap: 최소 시가총액 ($) - check_market_cap=True일 때만 사용
            min_price: 최소 주가 ($)
            min_avg_volume: 최소 평균 거래량 (shares)
            max_workers: 병렬 처리 워커 수 (너무 높으면 Yahoo 401 에러)
            check_market_cap: 시가총액 체크 여부 (Yahoo API 401 에러 방지)
        """
        self.min_market_cap = min_market_cap
        self.min_price = min_price
        self.min_avg_volume = min_avg_volume
        self.max_workers = max_workers
        self.check_market_cap = check_market_cap
        self.sectors = list(sectors or [])
        self.sector_map = SectorMap()

        # 캐시
        self._sp500_cache = None
        self._nasdaq100_cache = None
        self._universe_cache = {}  # {date_str: [tickers]}
        self._cache_date = None

    def get_sp500_tickers(self, force_refresh: bool = False) -> List[str]:
        """S&P500 종목 리스트 가져오기 (Wikipedia)"""
        if self._sp500_cache and not force_refresh:
            return self._sp500_cache

        try:
            logger.info("S&P500 종목 리스트 조회 중...")
            url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'

            # User-Agent 헤더 추가 (브라우저처럼 보이게)
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }

            req = urllib.request.Request(url, headers=headers)
            context = _https_context()

            with urllib.request.urlopen(req, context=context) as response:
                # header=0을 명시적으로 지정하여 첫 행을 헤더로 사용
                tables = pd.read_html(response.read(), header=0)

            # S&P500 구성 종목 테이블 찾기 (보통 500개 정도의 행이 있음)
            df = None
            for idx, table in enumerate(tables):
                # S&P500 테이블은 'Symbol' 또는 유사한 티커 관련 컬럼이 있어야 함
                # 그리고 최소 400개 이상의 행이 있어야 함 (현재 S&P500은 500+)
                if len(table) > 400:
                    # 컬럼 이름 확인
                    col_names = [str(col).lower() for col in table.columns]
                    if any('symbol' in col or 'ticker' in col for col in col_names):
                        df = table
                        break

            # 찾지 못한 경우 첫 번째 큰 테이블 사용
            if df is None:
                for idx, table in enumerate(tables):
                    if len(table) > 100:
                        df = table
                        print(f"⚠️ S&P500 테이블 자동 감지 실패, 큰 테이블 사용")
                        break

            if df is None:
                df = tables[0]
                print(f"⚠️ 적절한 S&P500 테이블을 찾지 못함")

            # 'Symbol' 또는 'Ticker' 컬럼 찾기
            if 'Symbol' in df.columns:
                tickers = df['Symbol'].astype(str).str.replace('.', '-', regex=False).tolist()
            elif 'Ticker' in df.columns:
                tickers = df['Ticker'].astype(str).str.replace('.', '-', regex=False).tolist()
            else:
                # 첫 번째 컬럼이 티커일 가능성이 높음
                print(f"⚠️ 'Symbol' 또는 'Ticker' 컬럼을 찾을 수 없습니다. 첫 번째 컬럼 사용: {df.columns[0]}")
                tickers = df[df.columns[0]].astype(str).str.replace('.', '-', regex=False).tolist()

            sector_col = next((c for c in df.columns if 'gics sector' in str(c).lower()), None)
            if sector_col is not None:
                self.sector_map.update(dict(zip(tickers, df[sector_col].astype(str))), overwrite=True)

            self._sp500_cache = tickers
            logger.info(f"S&P500 종목 {len(tickers)}개 조회 완료")
            print(f"✅ S&P500 종목 {len(tickers)}개 조회 완료")
            return tickers
        except Exception as e:
            logger.error(f"S&P500 조회 실패: {e}")
            print(f"❌ S&P500 조회 실패: {e}")
            return []

    def get_nasdaq100_tickers(self, force_refresh: bool = False) -> List[str]:
        """NASDAQ100 종목 리스트 가져오기 (Wikipedia)"""
        if self._nasdaq100_cache and not force_refresh:
            return self._nasdaq100_cache

        try:
            logger.info("NASDAQ100 종목 리스트 조회 중...")
            # 2026년 기준 구성종목 표는 별도 페이지로 분리됨. 옛 페이지는 폴백.
            urls = [
                'https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies',
                'https://en.wikipedia.org/wiki/Nasdaq-100',
            ]

            # User-Agent 헤더 추가 (브라우저처럼 보이게)
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }

            df = None
            for url in urls:
                req = urllib.request.Request(url, headers=headers)
                context = _https_context()
                with urllib.request.urlopen(req, context=context) as response:
                    tables = pd.read_html(response.read())

                # 'Ticker'/'Symbol' 컬럼이 있고 100개 안팎인 표만 인정
                for table in tables:
                    if ('Ticker' in table.columns or 'Symbol' in table.columns) and len(table) > 50:
                        df = table
                        break
                if df is not None:
                    break

            if df is None:
                # 엉뚱한 표(연혁 등)를 티커로 쓰면 yfinance 조회만 낭비되므로 빈 목록 반환
                print("⚠️ NASDAQ100 구성종목 표를 찾지 못했습니다 (Wikipedia 구조 변경?). S&P500 만 사용")
                logger.warning("NASDAQ100 구성종목 표 감지 실패")
                self._nasdaq100_cache = []
                return []

            col = 'Ticker' if 'Ticker' in df.columns else 'Symbol'
            tickers = df[col].astype(str).str.replace('.', '-', regex=False).tolist()

            industry_col = next((c for c in df.columns if 'icb industry' in str(c).lower()), None)
            if industry_col is not None:
                self.sector_map.update(dict(zip(tickers, df[industry_col].astype(str))), overwrite=False)

            self._nasdaq100_cache = tickers
            logger.info(f"NASDAQ100 종목 {len(tickers)}개 조회 완료")
            print(f"✅ NASDAQ100 종목 {len(tickers)}개 조회 완료")
            return tickers
        except Exception as e:
            logger.error(f"NASDAQ100 조회 실패: {e}")
            print(f"❌ NASDAQ100 조회 실패: {e}")
            return []

    def check_ticker(self, ticker: str, reference_date: Optional[datetime] = None) -> Optional[Dict]:
        """
        개별 종목 체크

        Args:
            ticker: 종목 코드
            reference_date: 기준 날짜 (백테스트용, None이면 현재)

        Returns:
            조건 충족시 종목 정보 dict, 아니면 None
        """
        try:
            if reference_date is None:
                reference_date = datetime.now()

            # Rate limit 방지: 요청 간 지연
            time.sleep(0.15)

            # 최근 60일 데이터 다운로드
            start_date = reference_date - timedelta(days=90)
            end_date = reference_date + timedelta(days=1)

            # 재시도 로직 (최대 2회)
            df = None
            for attempt in range(2):
                try:
                    df = yf.download(ticker, start=start_date, end=end_date,
                                   progress=False, threads=False)
                    break
                except Exception:
                    if attempt == 0:
                        time.sleep(1)  # 1초 대기 후 재시도
                    else:
                        raise

            if df is None or len(df) < 30:
                return None

            # yfinance MultiIndex 컬럼 처리 (단일 티커일 때도 MultiIndex 반환)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # 현재가
            current_price = float(df['Close'].iloc[-1])
            if current_price < self.min_price:
                return None

            # 평균 거래량 (최근 30일)
            avg_volume = float(df['Volume'].iloc[-30:].mean())
            if avg_volume < self.min_avg_volume:
                return None

            # 시가총액 체크 (선택적, 401 에러 방지)
            market_cap = 0
            name = ticker
            if self.check_market_cap:
                try:
                    stock = yf.Ticker(ticker)
                    info = stock.info
                    market_cap = info.get('marketCap', 0) or 0
                    name = info.get('longName', ticker)

                    if market_cap < self.min_market_cap:
                        return None
                except:
                    # .info 실패 시 시총 체크 건너뛰기
                    pass

            # 조건 통과
            return {
                'ticker': ticker,
                'price': float(current_price),
                'market_cap': market_cap,
                'avg_volume': float(avg_volume),
                'name': name
            }

        except Exception as e:
            logger.debug(f"{ticker} 체크 실패: {e}")
            return None

    def screen_universe(self,
                       reference_date: Optional[datetime] = None,
                       force_refresh: bool = False,
                       max_tickers: int = 200) -> List[str]:
        """
        동적 유니버스 스크리닝

        Args:
            reference_date: 기준 날짜 (None이면 현재)
            force_refresh: 캐시 무시하고 재조회
            max_tickers: 최대 반환 종목 수

        Returns:
            조건 충족하는 종목 코드 리스트
        """
        if reference_date is None:
            reference_date = datetime.now()

        date_str = reference_date.strftime('%Y-%m-%d')

        # 캐시 체크 (같은 날짜면 재사용)
        if not force_refresh and date_str in self._universe_cache:
            logger.info(f"캐시된 유니버스 사용: {date_str} ({len(self._universe_cache[date_str])}개)")
            return self._universe_cache[date_str]

        logger.info(f"\n{'='*60}")
        logger.info(f"동적 스크리닝 시작: {date_str}")
        logger.info(f"{'='*60}")

        # 1. 후보 종목 수집 (S&P500 + NASDAQ100)
        sp500 = self.get_sp500_tickers(force_refresh)
        nasdaq100 = self.get_nasdaq100_tickers(force_refresh)

        candidates = list(set(sp500 + nasdaq100))
        logger.info(f"후보 종목: {len(candidates)}개 (S&P500: {len(sp500)}, NASDAQ100: {len(nasdaq100)})")
        candidates = self.apply_sector_filter(candidates)

        # 2. 병렬로 각 종목 체크
        passed_tickers = []

        logger.info(f"조건 필터링 시작 (병렬 {self.max_workers}개)...")
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.check_ticker, ticker, reference_date): ticker
                for ticker in candidates
            }

            for i, future in enumerate(as_completed(futures)):
                if i % 50 == 0:
                    logger.info(f"  진행: {i}/{len(candidates)}...")

                result = future.result()
                if result:
                    passed_tickers.append(result['ticker'])

        # 3. 최대 개수 제한
        passed_tickers = passed_tickers[:max_tickers]

        logger.info(f"\n✅ 스크리닝 완료: {len(passed_tickers)}개 종목 선정")
        logger.info(f"조건: 시총 ${self.min_market_cap/1e6:.0f}M+, 주가 ${self.min_price}+, 거래량 {self.min_avg_volume:,}+")
        logger.info(f"{'='*60}\n")

        # 캐시 저장
        self._universe_cache[date_str] = passed_tickers
        self._cache_date = date_str

        return passed_tickers

    def apply_sector_filter(self, tickers: List[str]) -> List[str]:
        """self.sectors 가 설정돼 있으면 해당 섹터 종목만 남긴다 (표에 있는 정보만 사용, 네트워크 조회 없음)"""
        if not self.sectors:
            return tickers
        kept = self.sector_map.filter(tickers, self.sectors, lookup_missing=False)
        msg = f"🏷️ 섹터 필터 {self.sectors}: {len(tickers)}개 → {len(kept)}개"
        logger.info(msg)
        print(msg)
        return kept

    def get_detailed_universe(self,
                            reference_date: Optional[datetime] = None,
                            force_refresh: bool = False,
                            max_tickers: int = 200) -> List[Dict]:
        """
        상세 정보 포함한 유니버스 스크리닝

        Returns:
            각 종목의 상세 정보 dict 리스트
        """
        if reference_date is None:
            reference_date = datetime.now()

        logger.info(f"상세 유니버스 스크리닝 시작: {reference_date.strftime('%Y-%m-%d')}")

        # 1. 후보 종목 수집
        sp500 = self.get_sp500_tickers(force_refresh)
        nasdaq100 = self.get_nasdaq100_tickers(force_refresh)
        candidates = list(set(sp500 + nasdaq100))

        # 2. 병렬로 각 종목 체크
        results = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.check_ticker, ticker, reference_date): ticker
                for ticker in candidates
            }

            for future in as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)

        # 3. 시가총액 순으로 정렬하고 최대 개수 제한
        results.sort(key=lambda x: x['market_cap'], reverse=True)
        results = results[:max_tickers]

        logger.info(f"✅ 상세 스크리닝 완료: {len(results)}개 종목")
        return results

    def clear_cache(self):
        """캐시 초기화"""
        self._universe_cache.clear()
        self._cache_date = None
        logger.info("스크리너 캐시 초기화 완료")
