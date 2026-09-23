# bot_multifactor.py - 멀티팩터 퀀트 트레이딩 봇 (텔레그램 + 시장시간 체크 + 모드전환)

import asyncio
import sys
import threading
import time
import traceback
from datetime import datetime, time as dtime
from typing import Optional

import numpy as np
import pandas as pd
import pytz
import requests

# 텔레그램 라이브러리
try:
  from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
  from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

  TELEGRAM_LIB_AVAILABLE = True
except ImportError:
  TELEGRAM_LIB_AVAILABLE = False
  print(
    "⚠️ python-telegram-bot 미설치: 텔레그램 제어 기능 제한됨 (pip install python-telegram-bot)")

try:
  import yfinance as yf

  YFINANCE_AVAILABLE = True
except ImportError:
  YFINANCE_AVAILABLE = False
  print("⚠️ yfinance 미설치: pip install yfinance")

# 로컬 모듈 임포트
try:
  from multifactor_bot import config
  from multifactor_bot.brokers.kis_data_provider import KISDataProvider
  from multifactor_bot.brokers.kis_trader import KISTrader
  from multifactor_bot.database.paper_trading import PaperTradingManager
  from multifactor_bot.database.live_trading import LiveTradingManager
  from multifactor_bot.screening.dynamic_screener import DynamicScreener
except ImportError as e:
  print(f"❌ 필수 모듈 로드 실패: {e}")
  sys.exit(1)


class MultiFactorBot:
  """멀티팩터 퀀트 트레이딩 봇 (텔레그램 연동 Full Version)"""

  def __init__(self):
    self.config = config

    # 상태 변수 (Config에서 초기값 로드하지만 변경 가능)
    self.dry_run = getattr(config, 'DRY_RUN', True)

    # KIS API 클라이언트
    self.data_provider = KISDataProvider(use_yfinance=True, config=config)
    self.trader = KISTrader(config=config)

    # 포트폴리오 상태
    self.holdings = {}  # {ticker: {'shares': N, 'avg_price': P, ...}}
    self.last_rebalance = None

    # 통계
    self.total_trades = 0
    self.successful_trades = 0

    # 텔레그램 설정
    self.tg_token = getattr(config, 'TELEGRAM_TOKEN', None)
    self.tg_chat_id = getattr(config, 'TELEGRAM_CHAT_ID', None)

    # DB 매니저 초기화 (Paper Trading 또는 Live Trading)
    self.db_enabled = getattr(config, 'DB_ENABLED', False)
    self.paper_trading_enabled = getattr(config, 'PAPER_TRADING_ENABLED', False)
    self.db_manager = None

    if self.db_enabled:
      try:
        if self.paper_trading_enabled:
          self.db_manager = PaperTradingManager(account_id=config.PAPER_ACCOUNT_ID)
          print("📊 Paper Trading DB 연결 완료")
        else:
          self.db_manager = LiveTradingManager(account_id=config.LIVE_ACCOUNT_ID)
          print("📊 Live Trading DB 연결 완료")
      except Exception as e:
        print(f"⚠️ DB 연결 실패 (계속 진행): {e}")
        self.db_enabled = False

    # Live 모드: KIS 실계좌(현금+보유종목)를 DB에 먼저 반영 (첫 동기화 시 initial_capital 확정)
    self._sync_live_account_from_kis(reason="봇 시작")

    # 재시작 시 DB에 저장된 보유 종목/마지막 리밸런싱 시점 복원
    self._restore_holdings_from_db()

    # 동적 스크리너 초기화
    self.dynamic_screening_enabled = getattr(config, 'DYNAMIC_SCREENING_ENABLED', True)
    self.screener = None

    if self.dynamic_screening_enabled:
      try:
        self.screener = DynamicScreener(
          min_market_cap=config.DYNAMIC_SCREENING_MIN_MARKET_CAP,
          min_price=config.DYNAMIC_SCREENING_MIN_PRICE,
          min_avg_volume=config.DYNAMIC_SCREENING_MIN_VOLUME,
          max_workers=config.DYNAMIC_SCREENING_WORKERS,
          sectors=getattr(config, 'UNIVERSE_SECTORS', [])
        )
        print("🔍 동적 스크리너 초기화 완료")
      except Exception as e:
        print(f"⚠️ 동적 스크리너 초기화 실패 (고정 유니버스 사용): {e}")
        self.dynamic_screening_enabled = False

    # 초기화 로그
    print("=" * 80)
    print("🤖 멀티팩터 퀀트 트레이딩 봇 (텔레그램 Ver)")
    print("=" * 80)
    print(f"초기 모드: {'🧪 모의 거래 (Dry Run)' if self.dry_run else '💸 실전 거래 (Live)'}")
    print(f"DB 기록: {'✅ 활성' if self.db_enabled else '❌ 비활성'}")
    if self.db_enabled:
      print(f"DB 모드: {'📝 Paper Trading' if self.paper_trading_enabled else '💰 Live Trading'}")
    print(f"스크리닝: {'🔍 동적 (S&P500+NASDAQ100)' if self.dynamic_screening_enabled else f'📋 고정 ({len(config.WATCHLIST)}개)'}")
    print(f"리밸런싱: {config.REBALANCE_PERIOD_DAYS}일 주기")
    if self.holdings:
      last = self.last_rebalance.strftime('%Y-%m-%d') if self.last_rebalance else '없음'
      print(f"보유 종목 복원: {len(self.holdings)}개 (마지막 매수일: {last})")
    print("=" * 80)

    screening_label = ('🔍 동적 (S&P500+NASDAQ100)' if self.dynamic_screening_enabled
                       else f"📋 고정 ({len(config.WATCHLIST)}개)")
    sectors = getattr(config, 'UNIVERSE_SECTORS', [])
    if sectors and self.dynamic_screening_enabled:
      screening_label += f"\n섹터: {', '.join(sectors)}"
    db_label = '❌' if not self.db_enabled else ('📝 Paper' if self.paper_trading_enabled else '💰 Live')
    self.send_telegram(
      f"🤖 봇 가동 시작\n"
      f"모드: {'🧪 모의' if self.dry_run else '💸 실전'}\n"
      f"DB: {db_label}\n"
      f"스크리닝: {screening_label}\n"
      f"리밸런싱: {config.REBALANCE_PERIOD_DAYS}일 주기"
      + (f"\n보유 복원: {len(self.holdings)}개" if self.holdings else "")
    )

  # -----------------------------------------------------------
  # Live 계좌 동기화: KIS 실계좌 -> DB (현금, 포지션, 입출금 감지)
  # -----------------------------------------------------------
  def _sync_live_account_from_kis(self, reason: str = "") -> Optional[dict]:
    """Live 모드에서 KIS 실계좌 상태를 DB 에 반영한다.

    Paper 모드이거나 DB 가 꺼져 있으면 아무것도 하지 않는다.
    KIS 조회가 실패하면 DB 를 건드리지 않고 None 을 반환한다
    (잔고 0 으로 덮어쓰는 사고 방지).
    """
    if not (self.db_enabled and self.db_manager) or self.paper_trading_enabled:
      return None

    balance = self.trader.get_balance()
    if balance is None:
      print(f"⚠️ KIS 잔고 조회 실패 → DB 동기화 건너뜀 ({reason})")
      return None

    positions = self.trader.get_positions()
    if positions is None:
      print(f"⚠️ KIS 보유종목 조회 실패 → 현금만 동기화 ({reason})")

    try:
      result = self.db_manager.sync_from_kis(
        kis_cash=balance['available_cash'],
        kis_positions=positions,
        cash_flow_threshold=getattr(config, 'LIVE_CASH_FLOW_THRESHOLD_USD', 100.0),
      )
    except Exception as e:
      print(f"⚠️ KIS → DB 동기화 실패 ({reason}): {e}")
      return None

    tag = "첫 동기화" if result['first_sync'] else "재동기화"
    msg = (f"🔄 Live 계좌 {tag} ({reason})\n"
           f"현금: ${result['kis_cash']:,.2f}\n"
           f"initial_capital: ${result['initial_capital']:,.2f}")
    if result['positions']:
      pr = result['positions']
      msg += f"\n포지션: +{len(pr['added'])} / ={len(pr['updated'])} / -{len(pr['removed'])}"
    if result['cash_flow']:
      msg += f"\n💸 입출금 감지: {result['cash_flow']:+,.2f}"
    print(msg)
    if result['first_sync'] or result['cash_flow']:
      self.send_telegram(msg)
    return result

  # -----------------------------------------------------------
  # 상태 복원: DB 포지션 -> 메모리 holdings
  # -----------------------------------------------------------
  def _restore_holdings_from_db(self):
    """봇 재시작 시 DB의 보유 포지션을 self.holdings로 복원한다.

    복원하지 않으면 리밸런싱이 기존 종목을 '매도 대상'으로 인식하지 못하고,
    손절/트레일링 스탑/보유기간 만료 체크도 빈 holdings를 순회해 아무 일도 하지 않는다.
    last_rebalance는 가장 최근 매수일로 설정해, 재시작 직후 불필요한 리밸런싱을 막고
    원래 주기(REBALANCE_PERIOD_DAYS)를 이어가도록 한다.
    """
    if not (self.db_enabled and self.db_manager):
      return

    try:
      positions = self.db_manager.get_positions()
    except Exception as e:
      print(f"⚠️ DB 포지션 복원 실패 (빈 상태로 시작): {e}")
      return

    restored = {}
    latest_buy = None
    for pos in positions:
      ticker = pos.get('ticker')
      shares = int(pos.get('shares') or 0)
      avg_price = float(pos.get('avg_price') or 0)
      if not ticker or shares <= 0 or avg_price <= 0:
        continue

      buy_date = pos.get('buy_date') or datetime.now()
      if hasattr(buy_date, 'tzinfo') and buy_date.tzinfo is not None:
        buy_date = buy_date.replace(tzinfo=None)

      highest = pos.get('highest_price')
      high_price = max(float(highest), avg_price) if highest else avg_price

      restored[ticker] = {
        'shares': shares,
        'avg_price': avg_price,
        'buy_date': buy_date,
        'high_price': high_price,
      }
      if latest_buy is None or buy_date > latest_buy:
        latest_buy = buy_date

    self.holdings = restored
    if latest_buy is not None:
      self.last_rebalance = latest_buy

    if restored:
      print(f"♻️ DB에서 보유 종목 {len(restored)}개 복원: {sorted(restored.keys())}")
    else:
      print("♻️ DB에 복원할 보유 종목 없음")

  # -----------------------------------------------------------
  # 유틸리티: 텔레그램 메시지 발송 (Requests 사용 - 동기식)
  # -----------------------------------------------------------
  def send_telegram(self, message):
    if not self.tg_token or not self.tg_chat_id:
      return

    max_retries = 3
    for attempt in range(max_retries):
      try:
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        data = {"chat_id": self.tg_chat_id, "text": message}
        response = requests.post(url, data=data, timeout=15)

        if response.status_code == 200:
          return  # 성공
        elif attempt < max_retries - 1:
          time.sleep(1)  # 재시도 전 대기

      except requests.exceptions.Timeout:
        if attempt < max_retries - 1:
          time.sleep(2)
        else:
          pass  # 마지막 시도 실패는 조용히 무시
      except Exception:
        break  # 기타 에러는 재시도 안함

  # -----------------------------------------------------------
  # 유틸리티: 마켓 운영 시간 확인 (한국시간 기준)
  # -----------------------------------------------------------
  @staticmethod
  def _market_tz():
    return pytz.timezone(getattr(config, 'MARKET_TIMEZONE', 'America/New_York'))

  def is_market_open(self, now=None):
    """마켓 운영 여부 판정 (미국 동부시간 ET 기준, 서머타임 자동 반영)

    세션 시간표는 config.MARKET_SESSIONS (ET). 요일 규칙:
      토요일           : 휴장
      일요일           : 데이마켓(20:00 ET~)만 열림  (KIS 주간거래는 일~목 밤 ET)
      금요일 20:00 ET~ : 휴장 (금요일 밤 주간거래 없음)
      그 외            : 세션 시간표대로
    WEEKEND_TRADING_ENABLED=True 면 요일 규칙을 건너뛴다.
    미국 증시 휴장일은 반영하지 않는다.

    Returns:
      (is_open: bool, session_name: str | None)
    """
    now_et = (now or datetime.now(pytz.utc)).astimezone(self._market_tz())
    current_time = now_et.time()
    weekday = now_et.weekday()  # 0=월 ... 5=토 6=일

    sessions = getattr(config, 'MARKET_SESSIONS', {})
    day_market = sessions.get('day_market', {})
    day_start = self._parse_hhmm(day_market.get('start', '20:00'))

    weekend_enabled = getattr(config, 'WEEKEND_TRADING_ENABLED', False)
    if not weekend_enabled:
      if weekday == 5:
        return False, None
      if weekday == 6 and current_time < day_start:
        return False, None
      if weekday == 4 and current_time >= day_start:
        return False, None

    for session_name, times in sessions.items():
      start_time = self._parse_hhmm(times.get('start', '00:00'))
      end_time = self._parse_hhmm(times.get('end', '00:00'))
      # 구간은 [start, end) : 종료 시각은 다음 세션의 시작이므로 미포함 (09:30 은 정규장)
      if start_time > end_time:  # 자정을 걸치는 세션 (예: 20:00 ~ 03:50)
        if current_time >= start_time or current_time < end_time:
          return True, session_name
      elif start_time <= current_time < end_time:
        return True, session_name

    return False, None

  @staticmethod
  def _parse_hhmm(value: str) -> dtime:
    h, m = map(int, value.split(':'))
    return dtime(h, m)

  def get_current_session(self):
    """현재 마켓 세션 이름 반환"""
    is_open, session = self.is_market_open()
    if is_open and session:
      session_names = {
        'day_market': '🌅 데이마켓',
        'pre_market': '🌆 프리마켓',
        'regular': '🌙 정규장',
        'after_market': '🌄 애프터마켓'
      }
      return session_names.get(session, session)
    return '휴장'

  # -----------------------------------------------------------
  # 메인 루프
  # -----------------------------------------------------------
  def _get_next_scan_time(self):
    """다음 스캔 시간 계산 (매시간 정각+1분, 31분)"""
    kst_tz = pytz.timezone(getattr(config, 'DISPLAY_TIMEZONE', 'Asia/Seoul'))
    now = datetime.now(kst_tz)
    scan_minutes = getattr(config, 'SCAN_MINUTES', [1, 31])

    current_minute = now.minute
    current_hour = now.hour

    # 다음 스캔 시점 찾기
    for scan_min in sorted(scan_minutes):
      if current_minute < scan_min:
        # 같은 시간 내 다음 스캔
        next_scan = now.replace(minute=scan_min, second=0, microsecond=0)
        return next_scan

    # 다음 시간의 첫 번째 스캔
    next_hour = (current_hour + 1) % 24
    next_scan = now.replace(hour=next_hour, minute=sorted(scan_minutes)[0], second=0, microsecond=0)
    if next_hour < current_hour:  # 자정 넘김
      next_scan = next_scan + pd.Timedelta(days=1)
    return next_scan

  def _wait_until_next_scan(self):
    """다음 스캔 시간까지 대기"""
    kst_tz = pytz.timezone(getattr(config, 'DISPLAY_TIMEZONE', 'Asia/Seoul'))
    now = datetime.now(kst_tz)
    next_scan = self._get_next_scan_time()

    wait_seconds = (next_scan - now).total_seconds()
    if wait_seconds > 0:
      print(f"⏰ 다음 스캔: {next_scan.strftime('%H:%M %Z')} ({int(wait_seconds)}초 후)")
      time.sleep(wait_seconds)

  def run(self):
    """봇 메인 실행 루프 (매시간 01분, 31분 스캔)"""
    print("\n🚀 트레이딩 로직 루프 시작")
    kst_tz = pytz.timezone(getattr(config, 'DISPLAY_TIMEZONE', 'Asia/Seoul'))
    scan_minutes = getattr(config, 'SCAN_MINUTES', [1, 31])
    print(f"📅 스캔 스케줄: 매시간 {scan_minutes}분")
    print(f"🕐 세션 판정: {getattr(config, 'MARKET_TIMEZONE', 'America/New_York')} / 표시: {getattr(config, 'DISPLAY_TIMEZONE', 'Asia/Seoul')}\n")

    last_closed_msg_time = 0

    while True:
      try:
        now_kst = datetime.now(kst_tz)

        # 1. 장 운영 시간 확인
        is_open, session = self.is_market_open()

        if not is_open:
          # 30분에 한 번만 로그 출력
          if time.time() - last_closed_msg_time > 1800:
            print(f"\r💤 휴장 중 ({now_kst.strftime('%H:%M %Z')}, ET {datetime.now(self._market_tz()).strftime('%a %H:%M')}) - 대기 모드...", end='')
            last_closed_msg_time = time.time()

          time.sleep(60)  # 1분 대기
          continue

        # 2. 스캔 시간 체크 (정각+1분 또는 31분)
        if now_kst.minute not in scan_minutes:
          # 다음 스캔 시간까지 대기
          self._wait_until_next_scan()
          continue

        # 3. 스캔 실행
        session_name = self.get_current_session()
        print(f"\n{'=' * 50}")
        print(f"🔍 스캔 시작 | {now_kst.strftime('%Y-%m-%d %H:%M %Z')} | {session_name}")
        print(f"{'=' * 50}")

        # 4. 리밸런싱 체크
        if self.should_rebalance():
          msg = f"🔄 리밸런싱 시작\n{now_kst.strftime('%Y-%m-%d %H:%M:%S %Z')}\n세션: {session_name}"
          print(msg)
          self.send_telegram(msg)

          self.rebalance()
          self.last_rebalance = datetime.now()

        # 5. 리스크 관리 (손절/익절/트레일링스탑)
        self.check_risk_management()

        # 6. 포트폴리오 상태 출력 (콘솔)
        self.print_portfolio_status()

        # 7. 다음 스캔까지 대기
        self._wait_until_next_scan()

      except KeyboardInterrupt:
        print("\n⚠️ 사용자 중단")
        break
      except Exception as e:
        err_msg = f"❌ 봇 루프 오류: {e}"
        print(err_msg)
        traceback.print_exc()
        self.send_telegram(err_msg)
        time.sleep(60)

  # -----------------------------------------------------------
  # 핵심 로직: 리밸런싱
  # -----------------------------------------------------------
  def should_rebalance(self) -> bool:
    if self.last_rebalance is None:
      return True
    days_since = (datetime.now() - self.last_rebalance).days
    return days_since >= config.REBALANCE_PERIOD_DAYS

  def rebalance(self):
    """포트폴리오 리밸런싱 실행"""
    print("\n📊 팩터 분석 및 종목 선정 중...")

    # 1. 팩터 계산
    factors_df = self.calculate_all_factors()
    if factors_df.empty:
      self.send_telegram("❌ 팩터 계산 실패: 데이터 부족")
      return

    # 2. 스코어링 및 선정
    factors_df = self.normalize_and_score(factors_df)
    selected = factors_df.head(config.TOP_N_STOCKS)
    selected_tickers = set(selected['ticker'].tolist())

    # 3. 매매 계획
    current_tickers = set(self.holdings.keys())
    to_sell = current_tickers - selected_tickers
    to_buy = selected_tickers - current_tickers

    plan_msg = (
      f"📋 리밸런싱 계획\n"
      f"매도: {len(to_sell)}개 {list(to_sell)}\n"
      f"매수: {len(to_buy)}개 {list(to_buy)}\n"
      f"유지: {len(current_tickers & selected_tickers)}개"
    )
    print(plan_msg)
    self.send_telegram(plan_msg)

    # 4. 매도 실행 (개별 보호)
    for ticker in to_sell:
      try:
        self.sell_position(ticker, "리밸런싱 제외")
      except Exception as e:
        print(f"  ❌ {ticker} 매도 처리 실패 (스킵): {e}")

    # 5. 자금 확인 및 매수 실행
    # Paper Trading 모드: DB 잔고 사용 / Live Trading 모드: KIS API 잔고 사용
    if self.db_enabled and self.db_manager and self.paper_trading_enabled:
      # Paper Trading: DB에서 현금 잔고 조회
      available_cash = self.db_manager.get_cash_balance()
      print(f"💰 가용 자금 (DB Paper): ${available_cash:,.2f}")
    else:
      # Live Trading: KIS 실계좌를 DB 에 재동기화(입출금 반영)한 뒤 실제 잔고 사용
      self._sync_live_account_from_kis(reason="리밸런싱")
      balance = self.trader.get_balance()
      if balance is None:
        print("⚠️ KIS 잔고 조회 실패 → 이번 리밸런싱 매수 건너뜀")
        available_cash = 0
      else:
        available_cash = balance.get('available_cash', 0)
        print(f"💰 가용 자금 (KIS API): ${available_cash:,.2f}")

    if len(to_buy) > 0 and available_cash > 0:
      # 금액 균등 배분
      position_size = available_cash / len(to_buy)

      for ticker in to_buy:
        # 종목 하나의 실패가 나머지 매수와 last_rebalance 갱신을 막지 않도록 개별 보호
        try:
          row = selected[selected['ticker'] == ticker].iloc[0]
          price = row['price']

          if position_size >= getattr(config, 'MIN_ORDER_AMOUNT_USD', 50):
            self.buy_position(ticker, price, position_size)
          else:
            print(f"  ⚠️ 주문 금액 부족으로 스킵: {ticker} (${position_size:.2f})")
        except Exception as e:
          print(f"  ❌ {ticker} 매수 처리 실패 (스킵): {e}")

    self.send_telegram("✅ 리밸런싱 작업 완료")

  # -----------------------------------------------------------
  # 팩터 계산 로직
  # -----------------------------------------------------------
  def calculate_all_factors(self) -> pd.DataFrame:
    # 1. 유니버스 선정 (동적 or 고정)
    if self.dynamic_screening_enabled and self.screener:
      print("🔍 동적 스크리닝 시작...")
      universe = self.screener.screen_universe(
        max_tickers=config.DYNAMIC_SCREENING_MAX_TICKERS
      )
      print(f"✅ {len(universe)}개 종목 선정 완료")
    else:
      universe = config.WATCHLIST
      print(f"📋 고정 유니버스 사용: {len(universe)}개 종목")

    # 2. 각 종목 팩터 계산 (누락 사유를 집계하고, 대량 누락이면 잠시 후 1회 재시도)
    factors, skipped = self._calculate_factors_for(universe)

    retry_ratio = getattr(config, 'FACTOR_RETRY_MIN_SKIP_RATIO', 0.3)
    retry_wait = getattr(config, 'FACTOR_RETRY_WAIT_SECONDS', 60)
    retry_targets = skipped['no_data'] + skipped['error']
    if universe and len(retry_targets) / len(universe) >= retry_ratio:
      print(f"⚠️ 팩터 누락 {len(retry_targets)}/{len(universe)}개 (데이터 없음 {len(skipped['no_data'])}, "
            f"오류 {len(skipped['error'])}) → {retry_wait}초 후 재시도 (yfinance/KIS 일시 제한 가능성)")
      time.sleep(retry_wait)
      more, skipped2 = self._calculate_factors_for(retry_targets)
      factors.extend(more)
      for k in skipped:
        skipped[k] = (skipped[k] if k == 'short' else []) + skipped2[k]

    total_skipped = sum(len(v) for v in skipped.values())
    print(f"📊 팩터 계산 완료: {len(factors)}개 종목"
          + (f" (누락 {total_skipped}: 데이터 없음 {len(skipped['no_data'])}, "
             f"126일 미만 {len(skipped['short'])}, 오류 {len(skipped['error'])})" if total_skipped else ""))
    if skipped['no_data']:
      print(f"   데이터 없음: {skipped['no_data'][:20]}{' ...' if len(skipped['no_data']) > 20 else ''}")
    return pd.DataFrame(factors)

  def _calculate_factors_for(self, tickers):
    """종목별 팩터 계산. (factors, {'no_data': [...], 'short': [...], 'error': [...]}) 반환"""
    factors = []
    skipped = {'no_data': [], 'short': [], 'error': []}
    for ticker in tickers:
      try:
        df = self.data_provider.download(ticker, 'US', period='1y')
        if df is None or df.empty:
          skipped['no_data'].append(ticker)
          continue
        if len(df) < 126:
          skipped['short'].append(ticker)
          continue

        # 가격 추출: 마지막 유효 종가 (yfinance 가 당일 미완성 행을 NaN 으로 줄 때가 있다)
        close = df['Close'].dropna()
        if close.empty:
          skipped['no_data'].append(ticker)
          continue
        price = float(close.iloc[-1])
        if not np.isfinite(price) or price <= 0:
          skipped['no_data'].append(ticker)
          continue

        # 재무 정보는 선택적 (실패 시 value/quality 만 NaN)
        info = {}
        try:
          info = yf.Ticker(ticker).info or {}
        except Exception:
          pass

        factors.append({
          'ticker': ticker,
          'price': price,
          'momentum': self._calculate_momentum(df),
          'value': self._calculate_value(info),
          'quality': self._calculate_quality(info),
          'volatility': self._calculate_volatility(df)
        })
      except Exception as e:
        skipped['error'].append(ticker)
        if "delisted" not in str(e).lower():
          print(f"  ⚠️ {ticker} 팩터 계산 실패: {e}")
        continue
    return factors, skipped

  def _calculate_momentum(self, df):
    try:
      period = min(config.MOMENTUM_PERIOD, len(df) - 1)
      return (df['Close'].iloc[-1] / df['Close'].iloc[-period] - 1) * 100
    except:
      return np.nan

  def _calculate_value(self, info):
    try:
      pe = info.get('forwardPE', info.get('trailingPE', np.nan))
      pb = info.get('priceToBook', np.nan)
      if pd.isna(pe) or pd.isna(pb) or pe <= 0 or pb <= 0: return np.nan
      return (1 / pe + 1 / pb) / 2
    except:
      return np.nan

  def _calculate_quality(self, info):
    try:
      roe = info.get('returnOnEquity', np.nan)
      debt = info.get('debtToEquity', np.nan)
      if pd.isna(roe): return np.nan
      if pd.isna(debt): return roe * 100
      return roe * 100 - (debt / 100)
    except:
      return np.nan

  def _calculate_volatility(self, df):
    try:
      period = min(config.VOLATILITY_PERIOD, len(df))
      returns = df['Close'].pct_change().dropna().tail(period)
      vol = returns.std() * np.sqrt(252) * 100
      return 100 / vol if vol > 0 else np.nan
    except:
      return np.nan

  def normalize_and_score(self, factors_df):
    factor_cols = ['momentum', 'value', 'quality', 'volatility']
    for col in factor_cols:
      mean = factors_df[col].mean()
      std = factors_df[col].std()
      # 값이 모두 같아도 부동소수 오차로 std 가 1e-17 같은 값이 나올 수 있어 허용 오차로 판정
      factors_df[f'{col}_z'] = (factors_df[col] - mean) / std if std > 1e-9 else 0

    factors_df['composite_score'] = (
            factors_df['momentum_z'] * config.FACTOR_WEIGHTS['momentum'] +
            factors_df['value_z'] * config.FACTOR_WEIGHTS['value'] +
            factors_df['quality_z'] * config.FACTOR_WEIGHTS['quality'] +
            factors_df['volatility_z'] * config.FACTOR_WEIGHTS['volatility']
    )
    return factors_df.dropna(subset=['composite_score']).sort_values(
      'composite_score', ascending=False)

  # -----------------------------------------------------------
  # 매매 실행 (트레이더 호출)
  # -----------------------------------------------------------
  def buy_position(self, ticker: str, price: float, amount: float):
    # NaN/0/음수 가격은 주문 불가 (int(NaN) 은 ValueError 로 리밸런싱 전체를 중단시킨다)
    try:
      price = float(price)
    except (TypeError, ValueError):
      price = float('nan')
    if not np.isfinite(price) or price <= 0:
      print(f"  ⚠️ 가격 불명으로 매수 스킵: {ticker} (price={price})")
      return
    shares = int(amount / price)
    if shares <= 0: return

    msg = f"🛒 매수 시도: {ticker} {shares}주 @ ${price:.2f}"
    print(f"\n{msg}")

    # [중요] self.dry_run 변수 사용
    result = self.trader.buy_stock(ticker, shares, dry_run=self.dry_run)

    if result['success']:
      buy_date = datetime.now()
      self.holdings[ticker] = {
        'shares': shares,
        'avg_price': price,
        'buy_date': buy_date,
        'high_price': price
      }
      self.total_trades += 1
      self.successful_trades += 1

      # DB에 매수 기록
      if self.db_enabled and self.db_manager:
        try:
          commission = amount * getattr(config, 'BACKTEST_COMMISSION', 0.001)
          self.db_manager.record_buy(
            ticker=ticker,
            shares=shares,
            price=price,
            commission=commission,
            notes=f"자동 매수 (봇)"
          )
          self.db_manager.add_position(
            ticker=ticker,
            shares=shares,
            avg_price=price,
            buy_date=buy_date
          )
        except Exception as e:
          print(f"  ⚠️ DB 기록 실패: {e}")

      noti = f"✅ 매수 체결: {ticker}\n수량: {shares}주\n가격: ${price:.2f}\n모드: {'🧪 모의' if self.dry_run else '💸 실전'}"
      print(f"  성공!")
      self.send_telegram(noti)
    else:
      err = f"❌ 매수 실패: {ticker}\n사유: {result.get('message', '')}"
      print(f"  실패: {result.get('message', '')}")
      self.send_telegram(err)

  def sell_position(self, ticker: str, reason: str):
    if ticker not in self.holdings: return
    position = self.holdings[ticker]
    shares = position['shares']
    buy_price = position['avg_price']
    buy_date = position['buy_date']

    # 현재가 조회 시도
    price_data = self.data_provider.get_us_stock_price(ticker)
    current_price = price_data['price'] if price_data else position['avg_price']

    profit_pct = (current_price / buy_price - 1) * 100

    print(f"\n💰 매도 시도: {ticker} ({reason})")

    # [중요] self.dry_run 변수 사용
    result = self.trader.sell_stock(ticker, shares, dry_run=self.dry_run)

    if result['success']:
      # DB에 매도 기록
      if self.db_enabled and self.db_manager:
        try:
          total_value = shares * current_price
          commission = total_value * getattr(config, 'BACKTEST_COMMISSION', 0.001)
          self.db_manager.record_sell(
            ticker=ticker,
            shares=shares,
            price=current_price,
            buy_price=buy_price,
            buy_date=buy_date,
            commission=commission,
            exit_reason=reason,
            notes=f"자동 매도 (봇)"
          )
          self.db_manager.remove_position(ticker)
        except Exception as e:
          print(f"  ⚠️ DB 기록 실패: {e}")

      del self.holdings[ticker]
      self.total_trades += 1
      if profit_pct > 0: self.successful_trades += 1

      noti = (f"💰 매도 체결: {ticker}\n"
              f"수익률: {profit_pct:+.2f}%\n"
              f"사유: {reason}\n"
              f"모드: {'🧪 모의' if self.dry_run else '💸 실전'}")
      print(f"  성공! ({profit_pct:+.2f}%)")
      self.send_telegram(noti)
    else:
      print(f"  실패: {result.get('message', '')}")
      self.send_telegram(f"❌ 매도 실패 ({ticker}): {result.get('message', '')}")

  # -----------------------------------------------------------
  # 리스크 관리
  # -----------------------------------------------------------
  def check_risk_management(self):
    """손절 / 트레일링 스탑 / 보유기간 만료 검사.

    규칙 (우선순위 순):
      1. 손절        : 진입가 대비 STOP_LOSS_PERCENT 이하
      2. 트레일링 스탑: 고점 대비 TRAILING_STOP_PERCENT 이하
      3. 보유기간 만료: MAX_HOLD_DAYS 이상 보유
    1·2 는 MIN_HOLD_DAYS 이전에는 발동하지 않는다 (README "최소 7일 보유").
    3 은 최소 보유일과 무관하다.
    예전 elif 구조에서는 손절 조건이 맞지만 최소 보유일 미달이면 나머지 규칙이
    평가조차 되지 않았고, 트레일링에는 최소 보유일이 적용되지 않는 비일관이 있었다.
    """
    stop_loss_enabled = getattr(config, 'STOP_LOSS_ENABLED', False)
    stop_loss_pct = getattr(config, 'STOP_LOSS_PERCENT', -10)
    trailing_enabled = getattr(config, 'TRAILING_STOP_ENABLED', False)
    trailing_pct = getattr(config, 'TRAILING_STOP_PERCENT', -5)
    min_hold_days = getattr(config, 'MIN_HOLD_DAYS', 0)
    max_hold_days = getattr(config, 'MAX_HOLD_DAYS', 9999)

    for ticker in list(self.holdings.keys()):
      position = self.holdings[ticker]
      price_data = self.data_provider.get_us_stock_price(ticker)
      if not price_data: continue

      current_price = price_data['price']
      if current_price > position.get('high_price', 0):
        position['high_price'] = current_price

      profit_pct = (current_price / position['avg_price'] - 1) * 100
      high_price = position.get('high_price', current_price)
      from_high_pct = (current_price / high_price - 1) * 100
      hold_days = (datetime.now() - position['buy_date']).days
      min_hold_met = hold_days >= min_hold_days

      reason = None
      if min_hold_met and stop_loss_enabled and profit_pct <= stop_loss_pct:
        reason = f"손절 ({profit_pct:.1f}%)"
      elif min_hold_met and trailing_enabled and from_high_pct <= trailing_pct:
        reason = f"트레일링 스탑 ({from_high_pct:.1f}%)"
      elif hold_days >= max_hold_days:
        reason = f"보유기간 만료 ({hold_days}일)"

      if reason:
        self.sell_position(ticker, reason)

  def print_portfolio_status(self):
    if not self.holdings: return
    # 콘솔 로그용 - 너무 자주 출력되지 않게 조정하거나 내용은 간략히
    pass  # 루프마다 출력하면 너무 많으므로 생략, 필요시 구현


# ============================================================
# 텔레그램 명령어 처리 핸들러 (Async)
# ============================================================

# Reply Keyboard 버튼 레이아웃
def get_main_keyboard():
  """메인 메뉴 키보드 버튼 생성"""
  keyboard = [
    [KeyboardButton("/status"), KeyboardButton("/positions")],
    [KeyboardButton("/trades"), KeyboardButton("/stats")],
    [KeyboardButton("/performance"), KeyboardButton("/mode")],
    [KeyboardButton("/force"), KeyboardButton("/help")],
  ]
  return ReplyKeyboardMarkup(
    keyboard,
    resize_keyboard=True,  # 버튼 크기 자동 조절
    one_time_keyboard=False  # 버튼 계속 표시
  )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  keyboard = get_main_keyboard()
  await update.message.reply_text(
    "🤖 멀티팩터 퀀트 트레이딩 봇\n\n"
    "4-Factor 모델 기반 미국 주식 자동매매 봇입니다.\n"
    "(모멘텀 + 가치 + 퀄리티 + 저변동성)\n\n"
    "아래 버튼을 눌러 명령을 실행하세요!",
    reply_markup=keyboard
  )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
  await update.message.reply_text(
    "📖 **명령어 안내**\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "📊 **포지션 관리**\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "/positions\n"
    "  보유 포지션 상세 조회\n"
    "  (수량, 평단가, 수익률, 보유일)\n\n"
    "/close [TICKER]\n"
    "  특정 종목 수동 청산\n"
    "  예: /close AAPL\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "📜 **거래 내역**\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "/trades\n"
    "  최근 거래 내역 조회 (최대 15건)\n"
    "  (매수/매도 일시, 가격, 수익률)\n\n"
    "/stats\n"
    "  거래 통계 조회\n"
    "  (승률, 평균수익률, 최대손익 등)\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "📊 **정보 조회**\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "/status\n"
    "  현재 봇 상태 및 포트폴리오 조회\n"
    "  (모드, 시장상태, 보유종목, 수익률)\n\n"
    "/performance\n"
    "  성과 통계 조회\n"
    "  (승률, 평균수익률, 최대손익 등)\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "⚙️ **제어 명령**\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "/mode\n"
    "  🧪 모의투자 ↔ 💸 실전투자 전환\n"
    "  (실전 전환시 실제 주문 발생!)\n\n"
    "/screening\n"
    "  🔍 동적 ↔ 📋 고정 스크리닝 전환\n"
    "  동적: S&P500 + NASDAQ100 실시간 필터링\n"
    "  고정: config에 설정된 종목 리스트\n\n"
    "/force\n"
    "  ⚠️ 즉시 리밸런싱 강제 실행\n"
    "  (장 운영시간에만 가능)\n\n"
    "/reset\n"
    "  🔄 Paper Trading 데이터 초기화\n"
    "  (포지션, 거래내역, 잔고 리셋)\n"
    "  확정: /reset confirm\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "ℹ️ /start - 봇 소개\n"
    "ℹ️ /help - 이 도움말",
    reply_markup=get_main_keyboard()
  )


async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE,
                            bot_instance: MultiFactorBot):
  """/positions: 보유 포지션 상세 조회"""
  text = f"📊 **[보유 포지션]** {datetime.now().strftime('%H:%M')}\n\n"

  # DB에서 포지션 조회
  if bot_instance.db_enabled and bot_instance.db_manager:
    try:
      positions = bot_instance.db_manager.get_positions()
      if positions:
        text += f"📦 DB 포지션: {len(positions)}개\n"
        text += "-" * 30 + "\n"

        total_value = 0
        total_pnl = 0

        for pos in positions:
          ticker = pos['ticker']
          shares = int(pos['shares'])
          avg_price = float(pos['avg_price'])
          buy_date = pos['buy_date']

          # 현재가 조회
          price_data = bot_instance.data_provider.get_us_stock_price(ticker)
          current_price = price_data['price'] if price_data else avg_price

          position_value = shares * current_price
          pnl_pct = (current_price / avg_price - 1) * 100
          pnl_amt = (current_price - avg_price) * shares

          days_held = (datetime.now() - buy_date).days if buy_date else 0
          pnl_emoji = "📈" if pnl_pct > 0 else "📉" if pnl_pct < 0 else "➖"

          text += f"{pnl_emoji} {ticker}\n"
          text += f"   수량: {shares}주\n"
          text += f"   평단: ${avg_price:.2f} → ${current_price:.2f}\n"
          text += f"   수익: {pnl_pct:+.2f}% (${pnl_amt:+,.2f})\n"
          text += f"   보유: {days_held}일\n\n"

          total_value += position_value
          total_pnl += pnl_amt

        text += "-" * 30 + "\n"
        text += f"💰 총 포지션 가치: ${total_value:,.2f}\n"
        text += f"📊 총 미실현 손익: ${total_pnl:+,.2f}\n"
      else:
        text += "📭 보유 중인 포지션이 없습니다.\n"
    except Exception as e:
      text += f"⚠️ DB 조회 실패: {e}\n"

  # 메모리 포지션 (DB 미연결시 또는 백업)
  elif bot_instance.holdings:
    text += f"📦 메모리 포지션: {len(bot_instance.holdings)}개\n"
    text += "-" * 30 + "\n"

    for ticker, pos in bot_instance.holdings.items():
      shares = pos['shares']
      avg_price = pos['avg_price']
      buy_date = pos.get('buy_date', datetime.now())

      price_data = bot_instance.data_provider.get_us_stock_price(ticker)
      current_price = price_data['price'] if price_data else avg_price

      pnl_pct = (current_price / avg_price - 1) * 100
      days_held = (datetime.now() - buy_date).days

      pnl_emoji = "📈" if pnl_pct > 0 else "📉" if pnl_pct < 0 else "➖"
      text += f"{pnl_emoji} {ticker}: {shares}주 {pnl_pct:+.2f}% ({days_held}일)\n"
  else:
    text += "📭 보유 중인 포지션이 없습니다.\n"

  await update.message.reply_text(text, reply_markup=get_main_keyboard())


async def close_command(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        bot_instance: MultiFactorBot):
  """/close [TICKER]: 수동 포지션 청산"""
  # 인자 파싱
  args = context.args
  if not args:
    await update.message.reply_text(
      "⚠️ 사용법: /close [TICKER]\n"
      "예: /close AAPL"
    )
    return

  ticker = args[0].upper()

  # 포지션 확인
  if ticker not in bot_instance.holdings:
    # DB에서도 확인
    if bot_instance.db_enabled and bot_instance.db_manager:
      db_pos = bot_instance.db_manager.get_position(ticker)
      if not db_pos:
        await update.message.reply_text(f"❌ {ticker} 포지션을 찾을 수 없습니다.")
        return
    else:
      await update.message.reply_text(f"❌ {ticker} 포지션을 찾을 수 없습니다.")
      return

  # 시장 개장 확인
  is_open, session = bot_instance.is_market_open()
  if not is_open:
    kst_tz = pytz.timezone(getattr(config, 'DISPLAY_TIMEZONE', 'Asia/Seoul'))
    kst_time = datetime.now(pytz.utc).astimezone(kst_tz)
    await update.message.reply_text(
      f"⚠️ 시장 휴장 중입니다 ({kst_time.strftime('%H:%M %Z')})\n"
      f"포지션 청산은 마켓 운영 시간에만 가능합니다."
    )
    return

  # 청산 실행
  await update.message.reply_text(f"🔄 {ticker} 포지션 청산 중...")

  try:
    bot_instance.sell_position(ticker, "수동 청산 (텔레그램)")
    await update.message.reply_text(f"✅ {ticker} 포지션 청산 완료")
  except Exception as e:
    await update.message.reply_text(f"❌ 청산 실패: {e}")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        bot_instance: MultiFactorBot):
  """/stats: 거래 통계 조회"""
  if not bot_instance.db_enabled or not bot_instance.db_manager:
    await update.message.reply_text("⚠️ DB가 연결되지 않았습니다.")
    return

  try:
    stats = bot_instance.db_manager.get_trade_statistics()

    if not stats:
      await update.message.reply_text("📭 거래 통계가 없습니다.")
      return

    total_buys = int(stats.get('total_buys', 0))
    total_sells = int(stats.get('total_sells', 0))
    winning = int(stats.get('winning_trades', 0))
    losing = int(stats.get('losing_trades', 0))

    win_rate = (winning / total_sells * 100) if total_sells > 0 else 0
    avg_pnl = float(stats.get('avg_pnl_percent', 0))
    max_gain = float(stats.get('max_gain_percent', 0))
    max_loss = float(stats.get('max_loss_percent', 0))
    avg_days = float(stats.get('avg_holding_days', 0) or 0)

    text = f"📊 **[거래 통계]**\n\n"
    text += f"📈 총 거래\n"
    text += f"• 매수: {total_buys}건\n"
    text += f"• 매도: {total_sells}건\n\n"
    text += f"🎯 성과\n"
    text += f"• 승률: {win_rate:.1f}% ({winning}승 {losing}패)\n"
    text += f"• 평균 수익률: {avg_pnl:+.2f}%\n"
    text += f"• 최대 수익: {max_gain:+.2f}%\n"
    text += f"• 최대 손실: {max_loss:+.2f}%\n"
    text += f"• 평균 보유일: {avg_days:.1f}일\n"

    await update.message.reply_text(text, reply_markup=get_main_keyboard())

  except Exception as e:
    await update.message.reply_text(f"❌ 통계 조회 실패: {e}")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         bot_instance: MultiFactorBot):
  """/status: 상태 보고 (DB 기반)"""
  is_open, session = bot_instance.is_market_open()
  session_name = bot_instance.get_current_session()

  text = f"📊 **[상태 보고]** {datetime.now().strftime('%H:%M')}\n"
  text += f"• 모드: {'🧪 모의 (Dry)' if bot_instance.dry_run else '💸 실전 (Live)'}\n"
  text += f"• 시장: {'🟢 ' + session_name if is_open else '🔴 휴장'}\n"
  text += f"• DB: {'✅ 연결됨' if bot_instance.db_enabled else '❌ 미연결'}\n"

  # DB에서 포트폴리오 상태 조회
  if bot_instance.db_enabled and bot_instance.db_manager:
    try:
      # DB 포지션 가격 업데이트 (현재가 반영)
      for pos in bot_instance.db_manager.get_positions():
        ticker = pos['ticker']
        curr = bot_instance.data_provider.get_us_stock_price(ticker)
        if curr and curr.get('price'):
          bot_instance.db_manager.update_position_price(ticker, float(curr['price']))

      # 갱신된 현재가/수익률을 다시 읽는다 (갱신 전 목록을 쓰면 종목별 수익률이 0% 로 남는다)
      positions = bot_instance.db_manager.get_positions()
      status = bot_instance.db_manager.get_portfolio_status()
      if status:
        text += f"\n💰 [DB 포트폴리오 상태]\n"
        text += f"• 총 자산: ${float(status['total_value']):,.2f}\n"
        text += f"• 현금: ${float(status['current_cash']):,.2f}\n"
        text += f"• 포지션 가치: ${float(status['positions_value']):,.2f}\n"
        text += f"• 수익률: {float(status['total_return_percent']):.2f}%\n"
        text += f"• 보유 종목: {status['num_positions']}개\n"

      # DB 포지션 상세 표시
      if positions:
        text += f"\n📦 [보유 상세]\n"
        total_pl_sum = 0
        for pos in positions:
          ticker = pos['ticker']
          avg_price = float(pos['avg_price'])
          current_price = float(pos.get('current_price') or avg_price)
          pnl_percent = float(pos.get('unrealized_pnl_percent') or 0)
          if pnl_percent == 0 and avg_price > 0:
            pnl_percent = (current_price / avg_price - 1) * 100
          text += f"▫️ {ticker}: {pnl_percent:+.2f}%\n"
          total_pl_sum += pnl_percent

        if len(positions) > 0:
          text += f"\n평균 수익률: {total_pl_sum / len(positions):+.2f}%"
    except Exception as e:
      text += f"\n⚠️ DB 조회 실패: {e}\n"
  else:
    # DB 미연결 시 메모리 상 포트폴리오 표시
    text += f"\n📦 메모리 보유: {len(bot_instance.holdings)} 종목\n"

    if bot_instance.holdings:
      text += "\n[보유 상세]\n"
      total_pl_sum = 0
      count = 0
      for ticker, pos in bot_instance.holdings.items():
        curr = bot_instance.data_provider.get_us_stock_price(ticker)
        price = curr['price'] if curr else pos['avg_price']
        pnl = (price / pos['avg_price'] - 1) * 100
        text += f"▫️ {ticker}: {pnl:+.2f}%\n"
        total_pl_sum += pnl
        count += 1

      if count > 0:
        text += f"\n평균 수익률: {total_pl_sum / count:+.2f}%"

  await update.message.reply_text(text, reply_markup=get_main_keyboard())


async def trades_command(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         bot_instance: MultiFactorBot):
  """/trades: 최근 거래 내역 조회 (DB)"""
  if not bot_instance.db_enabled or not bot_instance.db_manager:
    await update.message.reply_text("⚠️ DB가 연결되지 않았습니다.")
    return

  try:
    trades = bot_instance.db_manager.get_recent_trades(limit=15)

    if not trades:
      await update.message.reply_text("📭 거래 내역이 없습니다.")
      return

    text = f"📜 **[최근 거래 내역]** (최근 {len(trades)}건)\n\n"

    for trade in trades:
      action = trade['action']
      ticker = trade['ticker']
      shares = int(trade['shares'])
      price = float(trade['price'])
      date_str = trade['date'].strftime('%m/%d %H:%M') if hasattr(trade['date'], 'strftime') else str(trade['date'])[:16]

      # 매수는 간단히 표시
      if action == 'BUY':
        text += f"🛒 {date_str} | {ticker}\n"
        text += f"   매수 {shares}주 @ ${price:.2f}\n\n"

      # 매도는 수익률 포함
      elif action == 'SELL':
        pnl_percent = float(trade.get('pnl_percent', 0))
        pnl_emoji = "📈" if pnl_percent > 0 else "📉"
        exit_reason = trade.get('exit_reason', 'SIGNAL')

        text += f"{pnl_emoji} {date_str} | {ticker}\n"
        text += f"   매도 {shares}주 @ ${price:.2f}\n"
        text += f"   수익: {pnl_percent:+.2f}% ({exit_reason})\n\n"

    await update.message.reply_text(text, reply_markup=get_main_keyboard())

  except Exception as e:
    await update.message.reply_text(f"❌ 거래 내역 조회 실패: {e}")


async def performance_command(update: Update, context: ContextTypes.DEFAULT_TYPE,
                               bot_instance: MultiFactorBot):
  """/performance: 성과 통계 조회 (DB)"""
  if not bot_instance.db_enabled or not bot_instance.db_manager:
    await update.message.reply_text("⚠️ DB가 연결되지 않았습니다.")
    return

  try:
    stats = bot_instance.db_manager.get_trade_statistics()
    summary = bot_instance.db_manager.get_performance_summary()

    if not stats and not summary:
      await update.message.reply_text("📭 거래 통계가 없습니다.")
      return

    text = f"📊 **[성과 통계]**\n\n"

    # 포트폴리오 현황
    if summary:
      text += f"💰 [포트폴리오]\n"
      text += f"• 총 자산: ${summary.get('total_value', 0):,.2f}\n"
      text += f"• 현금: ${summary.get('cash', 0):,.2f}\n"
      text += f"• 포지션: ${summary.get('positions_value', 0):,.2f}\n"
      text += f"• 수익률: {summary.get('total_return', 0):.2f}%\n"
      text += f"• 보유종목: {summary.get('num_positions', 0)}개\n\n"

    # 거래 통계
    if stats:
      total_buys = int(stats.get('total_buys', 0))
      total_sells = int(stats.get('total_sells', 0))
      winning = int(stats.get('winning_trades', 0))
      losing = int(stats.get('losing_trades', 0))

      win_rate = (winning / total_sells * 100) if total_sells > 0 else 0
      avg_pnl = float(stats.get('avg_pnl_percent', 0))
      max_gain = float(stats.get('max_gain_percent', 0))
      max_loss = float(stats.get('max_loss_percent', 0))
      avg_days = float(stats.get('avg_holding_days', 0) or 0)

      text += f"📈 [거래 통계]\n"
      text += f"• 총 매수: {total_buys}건\n"
      text += f"• 총 매도: {total_sells}건\n"
      text += f"• 승률: {win_rate:.1f}% ({winning}승 {losing}패)\n"
      text += f"• 평균 수익률: {avg_pnl:+.2f}%\n"
      text += f"• 최대 수익: {max_gain:+.2f}%\n"
      text += f"• 최대 손실: {max_loss:+.2f}%\n"
      text += f"• 평균 보유일: {avg_days:.1f}일\n"

    await update.message.reply_text(text, reply_markup=get_main_keyboard())

  except Exception as e:
    await update.message.reply_text(f"❌ 성과 조회 실패: {e}")


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       bot_instance: MultiFactorBot):
  """/mode: 실전/모의 전환"""
  # 상태 토글
  bot_instance.dry_run = not bot_instance.dry_run

  mode_str = "🧪 모의투자 (Dry Run)" if bot_instance.dry_run else "💸 실전투자 (Live Trading)"
  warning = "\n⚠️ 주의: 이제부터 실제 계좌에서 주문이 집행됩니다!" if not bot_instance.dry_run else ""

  log_msg = f"🔄 모드 변경 완료\n현재 상태: {mode_str}{warning}"
  print(f"\n[Telegram] 모드 변경 요청: {mode_str}")
  await update.message.reply_text(log_msg)


async def screening_command(update: Update, context: ContextTypes.DEFAULT_TYPE,
                            bot_instance: MultiFactorBot):
  """/screening: 동적/고정 스크리닝 전환"""
  # 동적 스크리닝 토글
  if bot_instance.dynamic_screening_enabled:
    # 동적 -> 고정
    bot_instance.dynamic_screening_enabled = False
    mode_str = f"📋 고정 스크리닝 ({len(config.WATCHLIST)}개 종목)"
  else:
    # 고정 -> 동적 (스크리너가 초기화되어 있어야 함)
    if bot_instance.screener is None:
      try:
        bot_instance.screener = DynamicScreener(
          min_market_cap=config.DYNAMIC_SCREENING_MIN_MARKET_CAP,
          min_price=config.DYNAMIC_SCREENING_MIN_PRICE,
          min_avg_volume=config.DYNAMIC_SCREENING_MIN_VOLUME,
          max_workers=config.DYNAMIC_SCREENING_WORKERS,
          sectors=getattr(config, 'UNIVERSE_SECTORS', [])
        )
      except Exception as e:
        await update.message.reply_text(f"❌ 동적 스크리너 초기화 실패: {e}")
        return
    bot_instance.dynamic_screening_enabled = True
    mode_str = "🔍 동적 스크리닝 (S&P500 + NASDAQ100)"

  log_msg = f"🔄 스크리닝 모드 변경\n현재: {mode_str}"
  print(f"\n[Telegram] 스크리닝 모드 변경: {mode_str}")
  await update.message.reply_text(log_msg)


async def force_command(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        bot_instance: MultiFactorBot):
  """/force: 강제 리밸런싱 (디버깅용)"""
  # 시장 개장 체크 (안전장치)
  is_open, session = bot_instance.is_market_open()
  if not is_open:
    kst_tz = pytz.timezone(getattr(config, 'DISPLAY_TIMEZONE', 'Asia/Seoul'))
    kst_time = datetime.now(pytz.utc).astimezone(kst_tz)
    await update.message.reply_text(
      f"⚠️ 시장 휴장 중입니다 ({kst_time.strftime('%H:%M %Z')})\n"
      f"강제 리밸런싱은 마켓 운영 시간에만 가능합니다."
    )
    return

  await update.message.reply_text("⚠️ 강제 리밸런싱을 요청했습니다. 잠시 후 실행됩니다...")
  # 강제로 last_rebalance를 초기화하여 다음 루프에서 실행되게 함
  bot_instance.last_rebalance = None
  print("\n[Telegram] 강제 리밸런싱 요청됨")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        bot_instance: MultiFactorBot):
  """/reset: Paper Trading 데이터 초기화"""
  # Paper Trading 모드 체크
  if not bot_instance.paper_trading_enabled:
    await update.message.reply_text(
      "⚠️ Paper Trading 모드가 아닙니다.\n"
      "Live Trading 데이터는 초기화할 수 없습니다."
    )
    return

  if not bot_instance.db_enabled or not bot_instance.db_manager:
    await update.message.reply_text("⚠️ DB가 연결되지 않았습니다.")
    return

  # 확인 메시지
  args = context.args
  if not args or args[0].lower() != 'confirm':
    await update.message.reply_text(
      "⚠️ **Paper Trading 초기화**\n\n"
      "다음 데이터가 삭제됩니다:\n"
      "• 모든 보유 포지션\n"
      "• 모든 거래 내역\n"
      "• 모든 포트폴리오 스냅샷\n"
      "• 현금 잔고 → 초기 자본금으로 리셋\n\n"
      "정말 초기화하려면 다음 명령어를 입력하세요:\n"
      "`/reset confirm`"
    )
    return

  try:
    result = bot_instance.db_manager.reset_trading_data()

    # 봇 내부 상태도 초기화
    bot_instance.holdings = {}
    bot_instance.last_rebalance = None

    await update.message.reply_text(
      "✅ **Paper Trading 초기화 완료**\n\n"
      f"• 삭제된 포지션: {result['positions_deleted']}개\n"
      f"• 삭제된 거래: {result['trades_deleted']}건\n"
      f"• 삭제된 스냅샷: {result['snapshots_deleted']}개\n"
      f"• 새 현금 잔고: ${result['new_cash_balance']:,.2f}"
    )
    print(f"\n[Telegram] Paper Trading 초기화 완료: {result}")

  except Exception as e:
    await update.message.reply_text(f"❌ 초기화 실패: {e}")


# ============================================================
# 텔레그램 리스너 쓰레드 (macOS 호환 버전)
# ============================================================
def run_telegram_listener(bot_instance):
  """텔레그램 봇 폴링 실행 (macOS/Unix 호환)"""
  if not bot_instance.tg_token:
    print("⚠️ 텔레그램 토큰 없음 - 리스너 시작 안함")
    return

  try:
    # 봇 애플리케이션 빌드
    app = ApplicationBuilder().token(bot_instance.tg_token).build()

    # 핸들러 등록 (lambda를 통해 bot 인스턴스 주입)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(
      CommandHandler("positions",
                     lambda u, c: positions_command(u, c, bot_instance)))
    app.add_handler(
      CommandHandler("close",
                     lambda u, c: close_command(u, c, bot_instance)))
    app.add_handler(
      CommandHandler("status",
                     lambda u, c: status_command(u, c, bot_instance)))
    app.add_handler(
      CommandHandler("trades",
                     lambda u, c: trades_command(u, c, bot_instance)))
    app.add_handler(
      CommandHandler("stats",
                     lambda u, c: stats_command(u, c, bot_instance)))
    app.add_handler(
      CommandHandler("performance",
                     lambda u, c: performance_command(u, c, bot_instance)))
    app.add_handler(
      CommandHandler("mode", lambda u, c: mode_command(u, c, bot_instance)))
    app.add_handler(
      CommandHandler("screening", lambda u, c: screening_command(u, c, bot_instance)))
    app.add_handler(
      CommandHandler("force", lambda u, c: force_command(u, c, bot_instance)))
    app.add_handler(
      CommandHandler("reset", lambda u, c: reset_command(u, c, bot_instance)))

    print("📡 텔레그램 명령어 리스너 대기 중...")

    # 쓰레드 내 Async Loop 설정 (signal handler 비활성화)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # stop_signals=None으로 signal handler 비활성화 (macOS 호환)
    app.run_polling(stop_signals=None)

  except Exception as e:
    print(f"⚠️ 텔레그램 리스너 시작 실패: {e}")
    print("ℹ️ 텔레그램 기능 없이 계속 실행됩니다...")


# ============================================================
# 메인 실행 블록
# ============================================================
if __name__ == "__main__":
  # 1. 봇 인스턴스 생성
  bot = MultiFactorBot()

  # 2. 텔레그램 리스너 쓰레드 시작 (데몬 쓰레드: 메인 종료시 같이 종료)
  if TELEGRAM_LIB_AVAILABLE and bot.tg_token:
    tg_thread = threading.Thread(target=run_telegram_listener, args=(bot,),
                                 daemon=True)
    tg_thread.start()
  else:
    print("ℹ️ 텔레그램 기능 없이 실행됩니다.")

  # 3. 메인 트레이딩 루프 실행
  try:
    bot.run()
  except KeyboardInterrupt:
    print("\n🛑 프로그램 종료 요청")
    bot.send_telegram("🛑 봇 시스템 종료")
    sys.exit(0)
