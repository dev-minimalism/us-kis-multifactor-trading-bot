# check_telegram_connection.py - 텔레그램 연결 확인 스크립트 (pytest 대상 아님, stdin 입력 사용)

import requests
import time

# Config에서 토큰 가져오기
try:
  import config_multifactor as config

  TOKEN = config.TELEGRAM_TOKEN
  CHAT_ID = config.TELEGRAM_CHAT_ID
except:
  print("❌ config_multifactor.py를 찾을 수 없습니다.")
  print("수동으로 토큰을 입력하세요:")
  TOKEN = input("TELEGRAM_TOKEN: ")
  CHAT_ID = input("TELEGRAM_CHAT_ID: ")

print("=" * 70)
print("📱 텔레그램 연결 테스트")
print("=" * 70)
print(f"토큰: {TOKEN[:20]}..." if TOKEN else "없음")
print(f"Chat ID: {CHAT_ID}")
print("-" * 70)

if not TOKEN or not CHAT_ID:
  print("❌ 토큰 또는 Chat ID가 설정되지 않았습니다.")
  exit(1)

# 테스트 1: 기본 연결
print("\n[테스트 1] 기본 메시지 전송 (타임아웃 10초)")
url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
data = {"chat_id": CHAT_ID, "text": "🧪 테스트 메시지 - 연결 확인"}

try:
  start_time = time.time()
  response = requests.post(url, data=data, timeout=10)
  elapsed = time.time() - start_time

  print(f"응답 시간: {elapsed:.2f}초")
  print(f"상태 코드: {response.status_code}")

  if response.status_code == 200:
    print("✅ 성공! 텔레그램에서 메시지를 확인하세요.")
    result = response.json()
    if result.get('ok'):
      print(f"메시지 ID: {result['result']['message_id']}")
  elif response.status_code == 401:
    print("❌ 인증 실패: 토큰이 올바르지 않습니다.")
  elif response.status_code == 400:
    print("❌ 잘못된 요청: Chat ID가 올바르지 않을 수 있습니다.")
  else:
    print(f"⚠️ 예상치 못한 응답: {response.text[:100]}")

except requests.exceptions.Timeout:
  print("❌ 타임아웃: 네트워크 연결이 느립니다.")
  print("   해결방법:")
  print("   1. 인터넷 연결 확인")
  print("   2. VPN 사용 시 변경 시도")
  print("   3. 방화벽 설정 확인")

except requests.exceptions.ConnectionError:
  print("❌ 연결 오류: Telegram API에 접근할 수 없습니다.")
  print("   해결방법:")
  print("   1. 인터넷 연결 확인")
  print("   2. api.telegram.org 접속 가능 여부 확인")
  print("   3. 프록시/VPN 필요 여부 확인")

except Exception as e:
  print(f"❌ 오류 발생: {e}")

# 테스트 2: 긴 타임아웃으로 재시도
print("\n[테스트 2] 재시도 테스트 (타임아웃 20초)")
try:
  start_time = time.time()
  response = requests.post(url, data=data, timeout=20)
  elapsed = time.time() - start_time

  print(f"응답 시간: {elapsed:.2f}초")
  if response.status_code == 200:
    print("✅ 성공! (긴 타임아웃으로 성공)")
  else:
    print(f"⚠️ 상태 코드: {response.status_code}")

except requests.exceptions.Timeout:
  print("❌ 타임아웃: 20초로도 연결 실패")
  print("   → 네트워크 문제가 심각합니다.")

except Exception as e:
  print(f"❌ 오류: {e}")

# 테스트 3: getMe API로 봇 정보 확인
print("\n[테스트 3] 봇 정보 확인")
try:
  url_me = f"https://api.telegram.org/bot{TOKEN}/getMe"
  response = requests.get(url_me, timeout=10)

  if response.status_code == 200:
    result = response.json()
    if result.get('ok'):
      bot_info = result['result']
      print(f"✅ 봇 이름: {bot_info.get('first_name')}")
      print(f"   봇 사용자명: @{bot_info.get('username')}")
      print(f"   봇 ID: {bot_info.get('id')}")
  else:
    print(f"⚠️ 상태 코드: {response.status_code}")

except Exception as e:
  print(f"❌ 오류: {e}")

# 진단 정보
print("\n" + "=" * 70)
print("🔍 진단 정보")
print("=" * 70)

# DNS 확인
print("\n[DNS 확인]")
try:
  import socket

  ip = socket.gethostbyname('api.telegram.org')
  print(f"✅ api.telegram.org IP: {ip}")
except Exception as e:
  print(f"❌ DNS 조회 실패: {e}")
  print("   → 인터넷 연결 또는 DNS 문제")

# 네트워크 테스트
print("\n[네트워크 테스트]")
try:
  test_response = requests.get('https://www.google.com', timeout=5)
  if test_response.status_code == 200:
    print("✅ 일반 인터넷 연결 정상")
  else:
    print("⚠️ 구글 연결 이상")
except:
  print("❌ 인터넷 연결 불가")

print("\n" + "=" * 70)
print("📋 최종 진단")
print("=" * 70)

# 최종 권장사항
print("\n권장사항:")
print("1. 위 테스트에서 성공하면 → 봇 실행 가능")
print("2. 타임아웃 반복되면 → 타임아웃 설정 늘리기")
print("3. 연결 자체 실패하면 → VPN 사용 고려")
print("4. 봇 정보 확인 실패 → 토큰 재확인")
print("\n" + "=" * 70)