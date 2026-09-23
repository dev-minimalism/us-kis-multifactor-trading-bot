#!/usr/bin/env python3
"""
Run dynamic universe backtesting for the multifactor trading strategy.

유니버스 모드
  1. 시점별 S&P500 구성종목 (생존편향 제거)          ← 기본
  2. 시점별 S&P500 구성종목 + DB 기록
  3. 현재 S&P500+NASDAQ100 스크리닝 (생존편향 있음)
  4. 고정 WATCHLIST (config.py IT 30개, 편향 가장 큼)

리스크 규칙(손절/트레일링/보유기간, 실봇과 동일)은 기본 ON. 끄려면 BACKTEST_RISK_RULES=False
"""

import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(src_path))

OPTIONS = {
    "1": dict(use_historical_constituents=True, use_dynamic_screening=False, db_recording=False),
    "2": dict(use_historical_constituents=True, use_dynamic_screening=False, db_recording=True),
    "3": dict(use_historical_constituents=False, use_dynamic_screening=True, db_recording=False),
    "4": dict(use_historical_constituents=False, use_dynamic_screening=False, db_recording=False),
}

if __name__ == "__main__":
    from multifactor_bot.backtest.dynamic_backtest import DynamicMultiFactorBacktest

    print("=" * 80)
    print("동적 유니버스 백테스팅 시작")
    print("=" * 80)
    print()
    print("옵션:")
    print("1. 시점별 S&P500 구성종목, DB 기록 OFF (기본, 생존편향 제거)")
    print("2. 시점별 S&P500 구성종목, DB 기록 ON")
    print("3. 현재 S&P500+NASDAQ100 스크리닝 (생존편향 있음)")
    print("4. 고정 WATCHLIST (config.py IT 30개)")
    print()

    choice = input("선택 (1/2/3/4, 엔터=1): ").strip() or "1"
    if choice not in OPTIONS:
        print("잘못된 선택입니다. 기본값(1)으로 실행합니다.")
        choice = "1"

    import os
    risk = os.getenv('BACKTEST_RISK_RULES', 'True').lower() in ('true', '1', 'yes')
    backtest = DynamicMultiFactorBacktest(risk_rules_enabled=risk, **OPTIONS[choice])
    backtest.run()
