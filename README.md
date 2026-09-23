# 멀티팩터 퀀트 트레이딩 봇

미국 주식 시장에서 연평균 30% 수익률을 목표로 하는 멀티팩터 퀀트 전략 트레이딩 봇입니다.

주문·잔고·보유종목 조회는 **한국투자증권(KIS, Korea Investment & Securities) Open API**를 사용합니다. 프로젝트 이름과 코드에 등장하는 `kis`는 모두 한국투자증권을 뜻합니다. 시세 데이터는 yfinance를 우선 사용하고 KIS API를 보조로 씁니다. 모의투자 계좌와 실전 계좌를 `KIS_VIRTUAL` 설정으로 전환합니다.

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## 📋 목차

- [전략 개요](#-전략-개요)
- [빠른 시작](#-빠른-시작)
- [프로젝트 구조](#-프로젝트-구조)
- [설치 및 설정](#-설치-및-설정)
- [사용 방법](#-사용-방법)
- [백테스트 결과](#-백테스트-결과)
- [파라미터 튜닝](#-파라미터-튜닝)
- [데이터베이스](#-데이터베이스)
- [문제 해결](#-문제-해결)
- [주의사항](#-주의사항)

---

## 📊 전략 개요

### 4가지 팩터를 결합한 전략

1. **모멘텀 팩터** (40%): 6개월 수익률
2. **가치 팩터** (20%): P/E, P/B 역수의 평균
3. **퀄리티 팩터** (20%): ROE - (부채비율/100)
4. **저변동성 팩터** (20%): 변동성의 역수

### 작동 원리

- 각 팩터를 Z-score로 정규화
- 가중치를 적용하여 종합 점수 계산
- 상위 15개 종목을 선택하여 포트폴리오 구성
- 월간 리밸런싱 (30일마다)

### 운영 시간

세션 판정은 **미국 동부시간(ET)** 기준이라 서머타임이 자동 반영됩니다. 로그와 텔레그램은 한국시간으로 표시합니다.

| 세션 | ET | KST (겨울) | KST (여름) |
|---|---|---|---|
| 데이마켓 (KIS 주간거래) | 20:00 ~ 03:50 | 10:00 ~ 17:50 | 09:00 ~ 16:50 |
| 프리마켓 | 04:00 ~ 09:30 | 18:00 ~ 23:30 | 17:00 ~ 22:30 |
| 정규장 | 09:30 ~ 16:00 | 23:30 ~ 06:00 | 22:30 ~ 05:00 |
| 애프터마켓 | 16:00 ~ 19:50 | 06:00 ~ 09:50 | 05:00 ~ 08:50 |

- 열린 시간에는 매시 01분, 31분에 스캔합니다. 리밸런싱 주기(30일)가 지났으면 리밸런싱을, 이어서 손절·트레일링·보유기간 만료를 검사합니다.
- 토요일은 휴장, 일요일은 20:00 ET(월요일 오전 KST) 데이마켓부터 열립니다. 금요일 밤 ET 데이마켓은 없습니다.
- 미국 증시 휴장일은 반영하지 않습니다.

### 유니버스와 섹터

기본 유니버스는 S&P 500 + NASDAQ-100 구성종목(동적 스크리닝)이며, `UNIVERSE_SECTORS`로 섹터를 제한합니다. 실봇과 백테스트에 똑같이 적용됩니다.

```bash
# .env — GICS 섹터명을 쉼표로 나열. 빈 값이면 전 섹터
UNIVERSE_SECTORS=Information Technology
# 예) 빅테크까지 포함: GOOGL/META/NFLX 는 Communication Services, AMZN/TSLA 는 Consumer Discretionary
# UNIVERSE_SECTORS=Information Technology, Communication Services, Consumer Discretionary
```

- 기본값은 `Information Technology`(IT 기술주)입니다. 2026-09 기준 후보 518개 중 83개가 남습니다.
- 섹터 라벨은 위키피디아 S&P 500 표(GICS)와 NASDAQ-100 표(ICB)에서 읽고, 표에 없는 과거 종목은 yfinance로 조회해 `.cache/sector_map.json`에 저장합니다.

### 리스크 관리

- 진입가 대비 -15% 손절
- 최고가 대비 -15% 트레일링 스탑
- 최소 7일, 최대 90일 보유 제한 (손절·트레일링 스탑은 최소 보유일 이후에만 발동, 보유기간 만료는 항상 평가)

---

## 🚀 빠른 시작

### 1단계: 설치 (1분)

```bash명
# 가상환경 생성 (선택사항)
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt
```

### 2단계: 환경변수 설정 (1분)

```bash
# .env.example을 복사하여 .env 파일 생성
cp .env.example .env

# .env 파일을 편집하여 API 키 설정
# KIS_APP_KEY=your_app_key_here
# KIS_APP_SECRET=your_app_secret_here
# TELEGRAM_TOKEN=your_telegram_token_here
```

> ⚠️ 처음에는 반드시 `DRY_RUN=True` 상태로 테스트하세요!

### 3단계: 백테스팅 (2분)

전략 성과를 먼저 확인하세요:

```bash
python scripts/run_backtest.py
```

**예상 결과:**
```
================================================================================
총 수익률      : +241.77%
연평균(CAGR)   : +26.37%
승률           : 60.9%
최대 수익      : +584.06%
================================================================================

💾 결과가 backtest_result/ 폴더에 CSV로 저장되었습니다.
```

### 4단계: 봇 실행 (1분)

```bash
# 안전 모드로 실행 (DRY_RUN=True)
python scripts/run_us_kis_multifactor_trading_bot_safe.py

# 또는 일반 모드
python scripts/run_us_kis_multifactor_trading_bot.py
```

**봇이 자동으로:**
- 유니버스(IT 섹터, 기본 80여 개) 종목의 팩터를 계산
- 상위 15개 종목 선택
- 포트폴리오 리밸런싱
- 손절/익절 관리
- 30일마다 자동 리밸런싱

---

## 📁 프로젝트 구조

```
us-kis-multifactor-trading-bot/
├── src/
│   └── multifactor_bot/           # 메인 패키지
│       ├── config.py               # 설정 관리 (환경변수 지원)
│       ├── core/                   # 핵심 로직
│       │   ├── bot.py              # 트레이딩 봇
│       │   ├── bot_dynamic.py      # 동적 스크리닝 봇
│       │   └── backtest.py         # 백테스팅
│       ├── brokers/                # 한국투자증권(KIS) 연동
│       │   ├── kis_client.py       # KIS Open API 클라이언트 (토큰 자동 갱신)
│       │   ├── kis_data_provider.py # 데이터 제공자 (yfinance 우선, KIS 보조)
│       │   └── kis_trader.py       # 주문 실행, 잔고·보유종목 조회
│       ├── database/               # PostgreSQL 연동
│       │   ├── db_manager.py       # 커넥션 풀, SSH 터널
│       │   ├── paper_trading.py    # 모의 거래 기록 (DB가 진실)
│       │   └── live_trading.py     # 실거래 기록 (KIS 계좌가 진실, 입출금 감지)
│       └── notifications/          # 알림 서비스
├── scripts/                        # 실행 스크립트
│   ├── run_us_kis_multifactor_trading_bot.py       # 봇 실행
│   ├── run_us_kis_multifactor_trading_bot_safe.py  # 안전 모드
│   └── run_backtest.py             # 백테스트 실행
├── sql/                            # 데이터베이스 SQL
│   ├── schema.sql                  # 전체 스키마 (새 DB 생성용)
│   ├── reset_db.sql                # 데이터 전체 초기화 (스키마 유지)
│   └── migrations/                 # 기존 DB에 적용하는 변경 (번호 순)
├── tests/                          # 테스트
├── backtest_result/                # 백테스트 결과 (CSV)
├── .env                            # 환경변수 (비공개)
├── .env.example                    # 환경변수 템플릿
├── pyproject.toml                  # 패키지 메타데이터
├── requirements.txt                # 의존성
└── README.md                       # 이 문서
```

---

## 🔧 설치 및 설정

### 필수 요구사항

- Python 3.8 이상
- 한국투자증권(KIS) Open API 계정 — [KIS Developers](https://apiportal.koreainvestment.com)에서 앱키/시크릿 발급, 해외주식 거래 권한 필요
- (선택) 텔레그램 봇 토큰
- (선택) PostgreSQL 14 이상 — 거래 기록, 재시작 시 보유종목 복원, 실계좌 동기화에 사용

### 환경변수 설정

`.env` 파일에서 다음 항목을 설정하세요:

```bash
# 한국투자증권(KIS) Open API 설정
KIS_APP_KEY=your_app_key_here
KIS_APP_SECRET=your_app_secret_here
KIS_ACCOUNT=12345678-01   # 계좌번호-상품코드
KIS_VIRTUAL=True  # True: 모의투자 서버, False: 실전 서버

# 텔레그램 알림 (선택사항)
TELEGRAM_TOKEN=your_telegram_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# 전략 파라미터
FACTOR_WEIGHT_MOMENTUM=0.40
FACTOR_WEIGHT_VALUE=0.20
FACTOR_WEIGHT_QUALITY=0.20
FACTOR_WEIGHT_VOLATILITY=0.20

TOP_N_STOCKS=15
REBALANCE_PERIOD_DAYS=30

# 리스크 관리
STOP_LOSS_PERCENT=-15.0
TRAILING_STOP_PERCENT=-15.0

# 봇 운영
SCAN_INTERVAL=3600  # 1시간
DRY_RUN=True  # 모의 실행

# 데이터베이스 (선택)
DB_ENABLED=True
PAPER_TRADING_ENABLED=True   # True: paper_* 테이블, False: live_* 테이블 + KIS 계좌 동기화
DB_HOST=localhost
DB_PORT=5432
DB_NAME=us_multifactor_db
DB_USER=your_user
DB_PASSWORD=your_password
SSH_TUNNEL_ENABLED=False     # 원격 DB면 True (SSH_HOST, SSH_USER, SSH_KEY_PATH 필요)
LIVE_CASH_FLOW_THRESHOLD_USD=100  # KIS-DB 현금 차이가 이 값을 넘으면 입출금으로 처리
```

### 개발 모드 설치 (선택사항)

코드 수정이 즉시 반영되도록 설치:

```bash
pip install -e .

# 그 후 어디서든 실행 가능
multifactor-bot
multifactor-backtest
```

---

## 💻 사용 방법

### 백테스트 실행

```bash
# 스크립트 직접 실행
python scripts/run_backtest.py

# 패키지로 설치한 경우
multifactor-backtest
```

**동적 백테스트 (권장):**

```bash
python scripts/run_dynamic_backtest.py
# 1. 시점별 S&P500 구성종목 (기본, 생존편향 제거)
# 2. 1 + DB 기록
# 3. 현재 S&P500+NASDAQ100 스크리닝 (생존편향 있음)
# 4. 고정 WATCHLIST (IT 30개, 편향 가장 큼)
```

리밸런싱 날짜마다 "그날 S&P 500에 실제로 있던 종목"을 후보로 씁니다(GitHub `fja05680/sp500` 데이터, `.cache/`에 캐시). `UNIVERSE_SECTORS` 섹터 제한과 신호일 기준 유동성 필터(최소 주가·20일 평균 거래량)가 적용됩니다. 리밸런싱 사이에는 거래일마다 일봉 종가로 실봇과 같은 리스크 규칙(손절, 트레일링 스탑, 최소·최대 보유일)을 검사합니다. 끄려면 `BACKTEST_RISK_RULES=False`. 남은 한계: value/quality 팩터는 yfinance의 현재 재무정보를 쓰고, 상장폐지된 종목은 가격 이력이 없어 계산에서 빠집니다.

`run_backtest.py`(정적)는 `config.WATCHLIST`(GICS IT 섹터 30개, 현재 시점 대형주) 고정 유니버스를 씁니다. 현재 시점 목록으로 과거를 돌리는 것이라 생존편향이 커서 참고용으로만 보세요.

**결과 파일:**
- `backtest_result/portfolio_YYYYMMDD_HHMMSS.csv` - 포트폴리오 히스토리
- `backtest_result/trades_YYYYMMDD_HHMMSS.csv` - 거래 내역
- `backtest_result/summary_YYYYMMDD_HHMMSS.csv` - 성과 요약

### 트레이딩 봇 실행

```bash
# 안전 모드 (DRY_RUN=True, KIS_VIRTUAL=True 강제)
python scripts/run_us_kis_multifactor_trading_bot_safe.py

# 일반 모드 (.env 설정 따름)
python scripts/run_us_kis_multifactor_trading_bot.py

# 패키지로 설치한 경우
multifactor-bot
```

### Python 코드에서 사용

```python
from multifactor_bot import config
from multifactor_bot.core.bot import MultiFactorBot
from multifactor_bot.core.backtest import MultiFactorBacktest

# 봇 실행
bot = MultiFactorBot()
bot.run()

# 백테스트 실행
backtest = MultiFactorBacktest()
backtest.run()
```

### 텔레그램 명령어

봇이 실행 중일 때 텔레그램으로 제어할 수 있습니다:

**포지션 관리:**
- `/positions` - 보유 포지션 상세 조회 (수량, 평단가, 수익률, 보유일)
- `/close [TICKER]` - 특정 종목 수동 청산 (예: `/close AAPL`)

**거래 내역:**
- `/trades` - 최근 거래 내역 조회 (최대 15건)
- `/stats` - 거래 통계 조회 (승률, 평균수익률, 최대손익 등)

**정보 조회:**
- `/status` - 현재 봇 상태 및 포트폴리오 조회
- `/performance` - 성과 통계 조회

**제어 명령:**
- `/mode` - 🧪 모의투자 ↔ 💸 실전투자 전환
- `/screening` - 🔍 동적 ↔ 📋 고정 스크리닝 전환
- `/force` - ⚠️ 즉시 리밸런싱 강제 실행 (장 운영시간에만)
- `/reset` → `/reset confirm` - Paper Trading 데이터 초기화 (Live 포함 전체 초기화는 `sql/reset_db.sql`)

**기타:**
- `/start` - 봇 소개
- `/help` - 명령어 도움말

### 백그라운드 실행 (nohup)

서버에서 봇을 백그라운드로 실행하려면 `nohup`을 사용하세요:

```bash
# 기본 실행 (단일 로그 파일)
nohup python scripts/run_us_kis_multifactor_trading_bot.py > bot.log 2>&1 &

# 날짜+시간이 포함된 로그 파일로 실행
nohup python scripts/run_us_kis_multifactor_trading_bot.py > bot_$(date +%Y%m%d_%H%M%S).log 2>&1 &

# logs 폴더에 날짜별 로그 저장 (권장)
mkdir -p logs
nohup python -u scripts/run_us_kis_multifactor_trading_bot.py > logs/bot_$(date +%Y%m%d).log 2>&1 &

# 안전 모드로 실행
nohup python -u scripts/run_us_kis_multifactor_trading_bot_safe.py > logs/bot_safe_$(date +%Y%m%d).log 2>&1 &

# 프로세스 ID 확인
echo $!

# 또는 프로세스 목록에서 확인
ps aux | grep "multifactor"
```

**로그 모니터링:**

```bash
# 실시간 로그 확인
tail -f bot.log
tail -f logs/bot_$(date +%Y%m%d).log

# 최근 100줄 확인
tail -n 100 bot.log

# 에러만 확인
grep -i "error\|fail\|exception" bot.log

# 특정 키워드 실시간 모니터링
tail -f logs/bot_$(date +%Y%m%d).log | grep -i "매수\|매도\|rebalance"
```

**봇 종료:**

```bash
# 프로세스 ID로 종료
kill <PID>

# 프로세스 찾아서 종료
pkill -f "multifactor"

# 강제 종료 (권장하지 않음)
kill -9 <PID>
```

**systemd 서비스로 등록 (권장):**

장기 운영시 systemd 서비스로 등록하면 자동 재시작, 로깅 관리가 편리합니다:

```bash
# /etc/systemd/system/trading-bot.service 파일 생성
sudo nano /etc/systemd/system/trading-bot.service
```

```ini
[Unit]
Description=Multi-Factor Trading Bot
After=network.target

[Service]
Type=simple
User=your_username
WorkingDirectory=/path/to/us-kis-multifactor-trading-bot
Environment="PATH=/path/to/.venv/bin"
ExecStart=/path/to/.venv/bin/python scripts/run_us_kis_multifactor_trading_bot_safe.py
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
```

```bash
# 서비스 활성화 및 시작
sudo systemctl daemon-reload
sudo systemctl enable trading-bot
sudo systemctl start trading-bot

# 상태 확인
sudo systemctl status trading-bot

# 로그 확인
sudo journalctl -u trading-bot -f
```

---

## 📈 백테스트 결과

### 최근 백테스트 성과 (2020-2025)

| 지표 | 결과 |
|------|------|
| 기간 | 2020-08-07 ~ 2025-11-07 |
| 초기 자본 | $100,000 |
| 최종 자산 | $341,770 |
| **총 수익률** | **+241.77%** |
| **연평균 수익률 (CAGR)** | **+26.37%** |
| 매수 횟수 | 148회 |
| 매도 횟수 | 133회 |
| **승률** | **60.9%** |
| 평균 수익률 | +14.92% |
| 평균 보유일 | 157.7일 |
| 최대 수익 | +584.06% |
| 최대 손실 | -36.39% |

### 연도별 수익률

| 연도 | 수익률 | 최종 자산 |
|------|--------|-----------|
| 2020 | +10.67% | $110,674 |
| 2021 | +28.20% | $141,888 |
| 2022 | -8.35% | $130,035 |
| 2023 | +28.84% | $167,534 |
| 2024 | +54.51% | $258,864 |
| 2025 | +32.03% | $341,770 |

---

## ⚙️ 파라미터 튜닝

### 보수적 설정 (안정성 우선)

`.env` 파일 수정:

```bash
FACTOR_WEIGHT_MOMENTUM=0.20
FACTOR_WEIGHT_VALUE=0.30
FACTOR_WEIGHT_QUALITY=0.30
FACTOR_WEIGHT_VOLATILITY=0.20

TOP_N_STOCKS=30
STOP_LOSS_PERCENT=-7.0
TRAILING_STOP_PERCENT=-10.0
```

**특징:**
- 더 많은 종목으로 분산
- 빠른 손절로 위험 감소
- 가치/퀄리티 비중 증가

### 공격적 설정 (수익성 우선)

```bash
FACTOR_WEIGHT_MOMENTUM=0.50
FACTOR_WEIGHT_VALUE=0.15
FACTOR_WEIGHT_QUALITY=0.15
FACTOR_WEIGHT_VOLATILITY=0.20

TOP_N_STOCKS=10
STOP_LOSS_PERCENT=-20.0
TRAILING_STOP_PERCENT=-20.0
```

**특징:**
- 소수 종목에 집중 투자
- 여유있는 손절선
- 모멘텀 비중 증가

### 균형 설정 (현재 기본값)

```bash
FACTOR_WEIGHT_MOMENTUM=0.40
FACTOR_WEIGHT_VALUE=0.20
FACTOR_WEIGHT_QUALITY=0.20
FACTOR_WEIGHT_VOLATILITY=0.20

TOP_N_STOCKS=15
STOP_LOSS_PERCENT=-15.0
TRAILING_STOP_PERCENT=-15.0
```

---

## 🗄️ 데이터베이스

`DB_ENABLED=True`면 모든 매매, 보유종목, 스냅샷을 PostgreSQL에 기록합니다. 봇을 재시작하면 DB의 보유종목과 마지막 리밸런싱 시점을 복원해 손절·트레일링 스탑·보유기간 규칙이 끊기지 않습니다.

### 새 DB 만들기

```bash
psql -U postgres -c "CREATE DATABASE us_multifactor_db;"
psql -U postgres -d us_multifactor_db -f sql/schema.sql
```

### 데이터 전체 초기화

기록을 처음부터 다시 쌓고 싶을 때 사용합니다. 테이블은 유지하고 데이터만 지운 뒤 계좌를 초기 자본금으로 다시 만듭니다. 코드가 `account_id=1`(Paper), `2`(Live)를 고정으로 쓰므로 계좌 순서가 중요합니다.

```bash
# 1. 봇을 먼저 종료 (실행 중 초기화 금지)
# 2. dry-run: 실제 변경 없이 실행 결과만 확인
sed 's/^COMMIT;/ROLLBACK;/' sql/reset_db.sql \
  | psql -h localhost -p 5432 -U your_user -d us_multifactor_db -v ON_ERROR_STOP=1
# 3. 실제 실행 (초기 자본금 변경: -v paper_capital=200000 -v live_capital=30000)
psql -h localhost -p 5432 -U your_user -d us_multifactor_db -f sql/reset_db.sql
```

텔레그램 `/reset confirm`은 Paper 테이블 3개만 지웁니다. Live 테이블, 팩터 점수, 리밸런싱 이벤트까지 모두 지우려면 위 SQL을 쓰세요.

### 기존 DB에 변경 적용

스키마가 바뀌면 `sql/migrations/`에 번호 순으로 파일이 추가됩니다. 모두 멱등이라 여러 번 실행해도 안전합니다.

```bash
psql -h localhost -p 5432 -U your_user -d us_multifactor_db -f sql/migrations/001_live_cash_flows.sql
```

### 실계좌(Live) 자동 동기화

`PAPER_TRADING_ENABLED=False`로 실행하면 봇이 시작할 때와 리밸런싱 직전에 한국투자증권 계좌를 조회해 DB에 반영합니다.

- **첫 동기화** (DB 초기화 직후): 실제 현금 + 보유 평가액을 `initial_capital`로 잡습니다. 수익률은 이 값을 기준으로 계산됩니다.
- **입출금 자동 반영**: DB의 현금은 봇 매매로만 바뀌므로, KIS 현금과의 차이는 봇이 모르는 변동입니다. 차이가 `LIVE_CASH_FLOW_THRESHOLD_USD`(기본 $100)를 넘으면 입출금으로 보고 `initial_capital`에 더한 뒤 `live_cash_flows` 테이블에 기록하고 텔레그램으로 알립니다. 그 이하는 수수료·환율 오차로 보고 잔고만 맞춥니다.
- **보유종목 병합**: 수량·평단은 KIS 값, 매수일·고점은 DB 값을 씁니다. KIS에 없는 종목은 DB에서 지웁니다.
- **조회 실패 시**: DB를 건드리지 않고 동기화를 건너뜁니다. 잔고 0으로 덮어쓰는 일은 없습니다.

```sql
-- 감지된 입출금 이력
SELECT * FROM live_cash_flows ORDER BY flow_date DESC;
```

---

## 🔧 문제 해결

### yfinance 데이터 오류

```bash
# yfinance 재설치
pip install --upgrade yfinance
```

### KIS API 토큰 오류

- 인터넷 연결 확인
- API 키 정보 재확인 (`.env` 파일). 모의투자 앱키와 실전 앱키는 서로 다릅니다
- `KIS_VIRTUAL` 값이 앱키 발급 유형(모의/실전)과 일치하는지 확인
- 5분 후 재시도. 한국투자증권은 토큰 발급 횟수를 제한합니다
- `kis_client.py`에서 토큰이 자동 갱신됩니다

### 잔고·보유종목 조회 실패

- 로그에 `잔고 조회 실패` / `보유 종목 조회 실패`가 찍히면 그 회차의 DB 동기화는 건너뜁니다
- 해외주식 거래 권한이 있는 계좌인지, `KIS_ACCOUNT`의 상품코드(`-01`)가 맞는지 확인

### 팩터 계산 실패

- 정상입니다. 일부 종목은 재무 데이터 부족으로 제외될 수 있습니다
- `WATCHLIST`에서 문제 종목 제거 후 재실행
- 최소 15개 이상 종목이 선택되면 OK

### Import 오류

```bash
# 패키지가 설치되지 않은 경우
pip install -e .

# 또는 PYTHONPATH 설정
export PYTHONPATH="${PYTHONPATH}:/path/to/us-kis-multifactor-trading-bot/src"
```

### 텔레그램 연결 실패

```bash
# python-telegram-bot 설치
pip install python-telegram-bot

# 연결 테스트
python scripts/check_telegram_connection.py
```

---

## ⚠️ 주의사항

### 리스크 고지

1. **과거 성과가 미래를 보장하지 않습니다**
2. **30% 수익률은 매우 도전적인 목표입니다** (워렌 버핏 장기 수익률 ~20%)
3. **높은 수익률은 높은 변동성과 손실 위험을 동반합니다**
4. **반드시 모의투자로 충분히 테스트 후 실전 진입하세요**

### 권장 사항

- 처음에는 `DRY_RUN=True`로 최소 1개월 이상 테스트
- 소액으로 시작하여 점진적으로 확대
- 정기적으로 백테스팅 결과와 실제 성과 비교
- 시장 환경 변화에 따라 파라미터 조정
- 최소 권장 자본: $30,000 (종목당 $2,000 × 15종목)

### 실전 진입 체크리스트

- [ ] 1개월 이상 모의투자 테스트 완료
- [ ] 백테스팅 결과 이해 및 검증
- [ ] 손절 규칙 숙지
- [ ] 소액으로 시작 (전체 자산의 10% 이하)
- [ ] 감정적 개입 최소화 계획
- [ ] 정기 모니터링 일정 수립

---

## 📚 추가 개선 아이디어

1. **머신러닝 적용**: 팩터 가중치를 동적으로 최적화
2. **추가 팩터**: 사이즈, 수익성, 투자 팩터 등
3. **섹터 중립**: 섹터별 균형 유지
4. **동적 리밸런싱**: 시장 변동성에 따라 주기 조정
5. **포트폴리오 최적화**: 마코위츠 최적화 적용

---

## 🤝 기여

이슈 및 풀 리퀘스트를 환영합니다!

---

## 📄 라이센스

이 코드는 교육 및 연구 목적으로 제공됩니다.
실제 거래에 사용하기 전에 충분히 테스트하세요.

**면책 조항**: 이 소프트웨어는 "있는 그대로" 제공되며, 투자 손실에 대한 책임은 사용자에게 있습니다.

---

**준비되셨나요? 지금 바로 시작하세요!**

```bash
python scripts/run_backtest.py
```