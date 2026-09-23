# config.py - 멀티팩터 퀀트 전략 설정 (환경변수 지원)

import os
from pathlib import Path
from typing import Dict, List

# .env 파일 지원
try:
  from dotenv import load_dotenv

  # 프로젝트 루트의 .env 파일 로드 (여러 경로 시도)
  possible_paths = [
    Path(__file__).parent.parent.parent / '.env',
    # src/multifactor_bot/config.py -> 프로젝트 루트
    Path(__file__).parent.parent.parent.parent / '.env',  # 한 단계 더 상위
    Path.cwd() / '.env',  # 현재 작업 디렉토리
  ]
  for env_path in possible_paths:
    if env_path.exists():
      load_dotenv(dotenv_path=env_path)
      break
except ImportError:
  print("⚠️ python-dotenv 미설치: pip install python-dotenv")

# ============================================================
# KIS API 설정 (필수 - .env에서 로드)
# ============================================================
KIS_APP_KEY = os.getenv('KIS_APP_KEY', '')
KIS_APP_SECRET = os.getenv('KIS_APP_SECRET', '')
KIS_ACCOUNT = os.getenv('KIS_ACCOUNT', '')
KIS_VIRTUAL = os.getenv('KIS_VIRTUAL', 'True').lower() in ('true', '1', 'yes')

# ============================================================
# 텔레그램 설정 (선택 - 없으면 텔레그램 기능 비활성화)
# ============================================================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

# ============================================================
# 멀티팩터 전략 파라미터
# ============================================================

# 팩터 가중치 (합계 1.0)
FACTOR_WEIGHTS: Dict[str, float] = {
  'momentum': float(os.getenv('FACTOR_WEIGHT_MOMENTUM', '0.40')),
  'value': float(os.getenv('FACTOR_WEIGHT_VALUE', '0.20')),
  'quality': float(os.getenv('FACTOR_WEIGHT_QUALITY', '0.20')),
  'volatility': float(os.getenv('FACTOR_WEIGHT_VOLATILITY', '0.20'))
}

# 팩터 계산 파라미터
MOMENTUM_PERIOD = int(os.getenv('MOMENTUM_PERIOD', '126'))  # 6개월 (거래일 기준)
VOLATILITY_PERIOD = int(os.getenv('VOLATILITY_PERIOD', '252'))  # 1년 (거래일 기준)

# 종목 선정
TOP_N_STOCKS = int(os.getenv('TOP_N_STOCKS', '15'))  # 포트폴리오 종목 수

# 스코어링 임계값
MIN_COMPOSITE_SCORE = float(
  os.getenv('MIN_COMPOSITE_SCORE', '-0.5'))  # 최소 종합 점수 (Z-score)

# ============================================================
# 리밸런싱 설정
# ============================================================
REBALANCE_PERIOD_DAYS = int(
  os.getenv('REBALANCE_PERIOD_DAYS', '30'))  # 30일마다 리밸런싱
REBALANCE_HOUR = int(
  os.getenv('REBALANCE_HOUR', '10'))  # 리밸런싱 실행 시간 (미국 동부시간 기준)

# ============================================================
# 리스크 관리
# ============================================================
# 손절
STOP_LOSS_ENABLED = os.getenv('STOP_LOSS_ENABLED', 'True').lower() in ('true',
                                                                       '1',
                                                                       'yes')
STOP_LOSS_PERCENT = float(
  os.getenv('STOP_LOSS_PERCENT', '-15.0'))  # 진입가 대비 -15% 손절

# 트레일링 스탑
TRAILING_STOP_ENABLED = os.getenv('TRAILING_STOP_ENABLED', 'True').lower() in (
  'true', '1', 'yes')
TRAILING_STOP_PERCENT = float(
  os.getenv('TRAILING_STOP_PERCENT', '-15.0'))  # 최고가 대비 -15% 손절

# 최소 보유 기간
MIN_HOLD_DAYS = int(os.getenv('MIN_HOLD_DAYS', '7'))  # 최소 7일 보유

# 최대 보유 기간
MAX_HOLD_DAYS = int(os.getenv('MAX_HOLD_DAYS', '90'))  # 최대 90일 보유

# ============================================================
# 포지션 관리
# ============================================================
MAX_POSITIONS = int(os.getenv('MAX_POSITIONS', '20'))  # 최대 보유 종목 수
MIN_ORDER_AMOUNT_USD = int(
  os.getenv('MIN_ORDER_AMOUNT_USD', '2000'))  # 최소 주문 금액
MAX_ORDER_AMOUNT_USD = int(
  os.getenv('MAX_ORDER_AMOUNT_USD', '10000'))  # 최대 주문 금액
POSITION_SIZE_METHOD = os.getenv('POSITION_SIZE_METHOD',
                                 'equal')  # 'equal': 동일 비중, 'score': 점수 비례

# ============================================================
# 봇 운영 설정
# ============================================================
SCAN_INTERVAL = int(os.getenv('SCAN_INTERVAL', '3600'))  # 1시간마다 스캔 (초 단위)
DRY_RUN = os.getenv('DRY_RUN', 'True').lower() in ('true', '1',
                                                   'yes')  # True: 모의 실행, False: 실제 거래

# 스캔 스케줄 설정 (매시간 정각+1분, 31분에 실행)
SCAN_MINUTES = [int(x) for x in os.getenv('SCAN_MINUTES', '1,31').split(',')]

# ============================================================
# 마켓 시간 설정 (미국 동부시간 ET 기준, 서머타임 자동 반영)
# ============================================================
# 세션 판정은 MARKET_TIMEZONE(뉴욕)으로, 로그·텔레그램 표시는 DISPLAY_TIMEZONE(서울)으로 한다.
# 한국시간으로 고정하면 서머타임에 따라 실제 미국 장과 1시간 어긋나고,
# 요일을 KST 로 판정하면 금요일 정규장 후반(토요일 새벽 KST)을 휴장으로 오인한다.
#
#   세션          ET              KST(겨울, EST)     KST(여름, EDT)
#   데이마켓      20:00~03:50     10:00~17:50        09:00~16:50   (KIS 주간거래, 일~목 밤 ET)
#   프리마켓      04:00~09:30     18:00~23:30        17:00~22:30
#   정규장        09:30~16:00     23:30~06:00        22:30~05:00
#   애프터마켓    16:00~19:50     06:00~09:50        05:00~08:50
MARKET_TIMEZONE = os.getenv('MARKET_TIMEZONE', 'America/New_York')  # 세션 판정 기준
DISPLAY_TIMEZONE = os.getenv('DISPLAY_TIMEZONE', 'Asia/Seoul')      # 로그/알림 표시 기준

# 마켓 세션 시간 (시작, 종료) - ET 24시간제. 자정을 넘기는 세션은 start > end 로 표현
MARKET_SESSIONS = {
    'day_market': {
        'start': os.getenv('DAY_MARKET_START', '20:00'),
        'end': os.getenv('DAY_MARKET_END', '03:50'),
    },
    'pre_market': {
        'start': os.getenv('PRE_MARKET_START', '04:00'),
        'end': os.getenv('PRE_MARKET_END', '09:30'),
    },
    'regular': {
        'start': os.getenv('REGULAR_MARKET_START', '09:30'),
        'end': os.getenv('REGULAR_MARKET_END', '16:00'),
    },
    'after_market': {
        'start': os.getenv('AFTER_MARKET_START', '16:00'),
        'end': os.getenv('AFTER_MARKET_END', '19:50'),
    },
}

# 주말에도 작동 여부 (True 면 요일 판정을 건너뛰고 세션 시간만 본다)
WEEKEND_TRADING_ENABLED = os.getenv('WEEKEND_TRADING_ENABLED', 'False').lower() in ('true', '1', 'yes')

# ============================================================
# 고정 유니버스 (폴백용) - GICS Information Technology 30 종목
# ============================================================
# 용도: 동적 스크리닝/시점별 구성종목 조회가 실패했을 때의 폴백, 그리고 정적 백테스트(backtest.py).
# UNIVERSE_SECTORS 기본값(IT)과 일관되도록 2026-09-23 Wikipedia S&P 500 표의 GICS 섹터가
# Information Technology 인 종목만 골랐다 (시가총액 상위 순). 다른 섹터를 유니버스에 추가하면
# 이 폴백 목록도 같이 손보는 것이 맞다.
# 주의: 현재 시점 대형주 목록이라 과거 백테스트에 쓰면 생존 편향이 크다. 전략 근거로 쓰려면
#       scripts/run_dynamic_backtest.py 의 시점별 구성종목 모드를 사용할 것.
WATCHLIST: List[str] = [
  "AAPL", "MSFT", "NVDA", "AVGO", "ORCL",
  "CRM", "AMD", "ADBE", "CSCO", "ACN",
  "IBM", "INTC", "QCOM", "TXN", "AMAT",
  "MU", "LRCX", "KLAC", "ADI", "NOW",
  "INTU", "PANW", "ANET", "CDNS", "SNPS",
  "PLTR", "CRWD", "MRVL", "APH", "FTNT",
]

# ============================================================
# 동적 스크리닝 설정
# ============================================================
DYNAMIC_SCREENING_ENABLED = os.getenv('DYNAMIC_SCREENING_ENABLED',
                                      'True').lower() in ('true', '1',
                                                          'yes')  # 동적 스크리닝 사용 여부
DYNAMIC_SCREENING_MIN_MARKET_CAP = int(
  os.getenv('DYNAMIC_SCREENING_MIN_MARKET_CAP', '500000000'))  # 최소 시가총액 ($500M)
DYNAMIC_SCREENING_MIN_PRICE = float(
  os.getenv('DYNAMIC_SCREENING_MIN_PRICE', '5.0'))  # 최소 주가 ($5)
DYNAMIC_SCREENING_MIN_VOLUME = int(
  os.getenv('DYNAMIC_SCREENING_MIN_VOLUME', '50000'))  # 최소 평균 거래량 (50K shares)
DYNAMIC_SCREENING_MAX_TICKERS = int(
  os.getenv('DYNAMIC_SCREENING_MAX_TICKERS', '200'))  # 최대 스크리닝 종목 수
DYNAMIC_SCREENING_WORKERS = int(
  os.getenv('DYNAMIC_SCREENING_WORKERS', '10'))  # 병렬 처리 워커 수

# 유니버스 섹터 제한 (실봇 스크리너 + 백테스트 공통). 빈 값이면 전 섹터.
# GICS 섹터명을 쉼표로 나열. 예) "Information Technology, Communication Services"
# 기본은 IT 기술주만. GOOGL/META/NFLX 는 Communication Services, AMZN/TSLA 는 Consumer Discretionary 라
# 포함하려면 해당 섹터를 추가해야 한다.
from multifactor_bot.screening.sectors import parse_sectors as _parse_sectors  # noqa: E402
UNIVERSE_SECTORS: List[str] = _parse_sectors(os.getenv('UNIVERSE_SECTORS', 'Information Technology'))

# 팩터 계산 누락 재시도: 유니버스 대비 누락 비율이 이 값 이상이면 대기 후 1회 재시도
FACTOR_RETRY_MIN_SKIP_RATIO = float(os.getenv('FACTOR_RETRY_MIN_SKIP_RATIO', '0.3'))
FACTOR_RETRY_WAIT_SECONDS = int(os.getenv('FACTOR_RETRY_WAIT_SECONDS', '60'))

# ============================================================
# 백테스팅 설정
# ============================================================
BACKTEST_START_DATE = os.getenv('BACKTEST_START_DATE', '2020-01-01')  # 백테스팅 시작일
BACKTEST_END_DATE = os.getenv('BACKTEST_END_DATE', '2025-11-20')  # 백테스팅 종료일
BACKTEST_INITIAL_CAPITAL = int(
  os.getenv('BACKTEST_INITIAL_CAPITAL', '100000'))  # 초기 자본 ($)
BACKTEST_COMMISSION = float(
  os.getenv('BACKTEST_COMMISSION', '0.001'))  # 거래 수수료 (0.1%)

# ============================================================
# Database 설정 (PostgreSQL)
# ============================================================
DB_ENABLED = os.getenv('DB_ENABLED', 'False').lower() in ('true', '1',
                                                          'yes')  # DB 사용 여부
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = int(os.getenv('DB_PORT', '5432'))
DB_NAME = os.getenv('DB_NAME', 'trading_bot')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
DB_POOL_SIZE = int(os.getenv('DB_POOL_SIZE', '5'))  # 커넥션 풀 크기
DB_MAX_OVERFLOW = int(os.getenv('DB_MAX_OVERFLOW', '10'))  # 최대 초과 커넥션

# SSH Tunnel 설정
SSH_TUNNEL_ENABLED = os.getenv('SSH_TUNNEL_ENABLED', 'False').lower() in ('true', '1', 'yes')
SSH_HOST = os.getenv('SSH_HOST', '')
SSH_PORT = int(os.getenv('SSH_PORT', '22'))
SSH_USER = os.getenv('SSH_USER', '')
SSH_KEY_PATH = os.getenv('SSH_KEY_PATH', '')
SSH_REMOTE_HOST = os.getenv('SSH_REMOTE_HOST', 'localhost')
SSH_REMOTE_PORT = int(os.getenv('SSH_REMOTE_PORT', '5432'))

# Database 연결 URL
DATABASE_URL = os.getenv(
    'DATABASE_URL',
    f'postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}'
)

# ============================================================
# Paper Trading 설정
# ============================================================
PAPER_TRADING_ENABLED = os.getenv('PAPER_TRADING_ENABLED', 'False').lower() in (
  'true', '1', 'yes')
PAPER_TRADING_INITIAL_CAPITAL = int(
  os.getenv('PAPER_TRADING_INITIAL_CAPITAL', '100000'))  # 가상 거래 초기 자본
PAPER_ACCOUNT_ID = int(
  os.getenv('PAPER_ACCOUNT_ID', '1'))  # Paper Trading 계좌 ID

# ============================================================
# Live Trading 설정
# ============================================================
LIVE_TRADING_ENABLED = os.getenv('LIVE_TRADING_ENABLED', 'False').lower() in (
  'true', '1', 'yes')
LIVE_ACCOUNT_ID = int(os.getenv('LIVE_ACCOUNT_ID', '2'))  # Live Trading 계좌 ID
LIVE_CASH_FLOW_THRESHOLD_USD = float(
  os.getenv('LIVE_CASH_FLOW_THRESHOLD_USD', '100'))  # KIS-DB 현금 차이가 이 값을 넘으면 입출금으로 간주
LIVE_SYNC_INTERVAL = int(
  os.getenv('LIVE_SYNC_INTERVAL', '300'))  # KIS API 동기화 간격 (초)

# ============================================================
# 전략 설명
# ============================================================
"""
멀티팩터 퀀트 전략:

1. 팩터 계산:
   - 모멘텀: 6개월 수익률
   - 가치: P/E, P/B 역수의 평균
   - 퀄리티: ROE - (부채비율/100)
   - 저변동성: 변동성의 역수

2. 종합 점수:
   - 각 팩터를 Z-score로 정규화
   - 가중치 적용하여 종합 점수 계산
   - 상위 N개 종목 선택

3. 포트폴리오 구성:
   - 동일 비중 또는 점수 비례 배분
   - 월간 리밸런싱

4. 리스크 관리:
   - 진입가 대비 -15% 손절
   - 최고가 대비 -15% 트레일링 스탑
   - 최소 7일, 최대 90일 보유

목표:
   - 연평균 수익률 30% 목표
   - 샤프 비율 1.5 이상
   - 최대 낙폭(MDD) 20% 이내
"""
