# kis_client.py - KIS API 클라이언트 (RSI Williams 전략용)
import json
import time
from datetime import datetime, timedelta
from threading import Lock

import requests


class KISClient:
  """
  KIS API 클라이언트
  - 토큰 자동 갱신
  - 시장별(KR/US) 분리
  """
  _instances = {}
  _lock = Lock()

  def __new__(cls, market='US', config=None):
    """싱글톤 패턴 (시장별)"""
    key = f"{market}_{id(config)}"
    if key not in cls._instances:
      with cls._lock:
        if key not in cls._instances:
          instance = super().__new__(cls)
          cls._instances[key] = instance
    return cls._instances[key]

  def __init__(self, market='US', config=None):
    """
    Args:
        market: 'KR' 또는 'US'
        config: 설정 딕셔너리 또는 모듈
    """
    if hasattr(self, '_initialized'):
      return

    self.market = market.upper()

    # Config 로드
    if config is None:
      try:
        if self.market == 'KR':
          import config_kr as cfg
        else:
          import config_rsi_william as cfg
        config = cfg
      except ImportError:
        raise Exception("설정 파일을 찾을 수 없습니다.")

    # Config에서 값 추출
    if hasattr(config, 'KIS_APP_KEY'):
      self.app_key = config.KIS_APP_KEY
      self.app_secret = config.KIS_APP_SECRET
      self.virtual = config.KIS_VIRTUAL
    elif isinstance(config, dict):
      self.app_key = config.get('KIS_APP_KEY', '')
      self.app_secret = config.get('KIS_APP_SECRET', '')
      self.virtual = config.get('KIS_VIRTUAL', True)
    else:
      raise Exception("잘못된 설정 형식입니다.")

    # API URL
    if self.virtual:
      self.base_url = "https://openapivts.koreainvestment.com:29443"
    else:
      self.base_url = "https://openapi.koreainvestment.com:9443"

    self.access_token = None
    self.token_expires_at = None
    self._token_lock = Lock()
    self._initialized = True

    # 토큰 발급
    self._refresh_token(force=True)
    print(f"✅ KIS Client 초기화 ({self.market}, {'모의' if self.virtual else '실전'})")

  def _refresh_token(self, force=False):
    """토큰 갱신"""
    now = datetime.now()

    needs_refresh = (
        self.access_token is None or
        self.token_expires_at is None or
        now >= self.token_expires_at - timedelta(minutes=5) or
        force
    )

    if needs_refresh:
      with self._token_lock:
        if force or self.access_token is None or now >= self.token_expires_at - timedelta(
            minutes=5):
          url = f"{self.base_url}/oauth2/tokenP"
          headers = {"content-type": "application/json"}
          data = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret
          }

          for attempt in range(3):
            try:
              res = requests.post(url, headers=headers,
                                  data=json.dumps(data), timeout=10)

              if res.status_code == 200:
                result = res.json()
                self.access_token = result["access_token"]
                expires_in = result.get("expires_in", 86400)
                self.token_expires_at = now + timedelta(seconds=expires_in)
                print(f"✅ 토큰 갱신 성공 ({self.market})")
                return True
              elif "EGW00133" in res.text:
                print(f"⚠️  요청 제한, 65초 대기...")
                time.sleep(65)
              else:
                print(f"❌ 토큰 실패: {res.status_code} - {res.text}")

            except Exception as e:
              print(f"❌ 토큰 오류 (시도 {attempt + 1}/3): {e}")

            time.sleep(5)

          raise Exception("토큰 갱신 실패")

    return True

  def get_headers(self, tr_id: str) -> dict:
    """API 호출용 헤더"""
    self._refresh_token()
    return {
      "Content-Type": "application/json",
      "authorization": f"Bearer {self.access_token}",
      "appkey": self.app_key,
      "appsecret": self.app_secret,
      "tr_id": tr_id
    }


# 편의 함수
def get_client(market='US', config=None):
  """KIS Client 가져오기"""
  return KISClient(market=market, config=config)


# 테스트
if __name__ == "__main__":
  print("=" * 70)
  print("KIS Client 테스트")
  print("=" * 70)

  try:
    # 미국 시장
    us_client = get_client('US')
    print(f"\n미국 클라이언트: {us_client.market}")
    print(f"Virtual: {us_client.virtual}")
    print(f"Base URL: {us_client.base_url}")
    print(
        f"Token: {us_client.access_token[:20]}..." if us_client.access_token else "None")

  except Exception as e:
    print(f"❌ 오류: {e}")
