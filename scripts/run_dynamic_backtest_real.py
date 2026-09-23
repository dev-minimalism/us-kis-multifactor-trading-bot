#!/usr/bin/env python3
"""
Run real-world dynamic universe backtesting for the multifactor trading strategy.
Look-ahead bias completely removed version.
"""

import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(src_path))

if __name__ == "__main__":
    from multifactor_bot.backtest.dynamic_backtest_real import RealWorldDynamicBacktest

    print("=" * 80)
    print("실전 수준 동적 백테스트 (Look-ahead bias 완전 제거)")
    print("=" * 80)
    print()
    print("옵션:")
    print("1. 동적 스크리닝 ON, DB 기록 OFF (기본)")
    print("2. 동적 스크리닝 ON, DB 기록 ON")
    print("3. 동적 스크리닝 OFF (고정 유니버스), DB 기록 OFF")
    print()

    choice = input("선택 (1/2/3, 엔터=1): ").strip() or "1"

    if choice == "1":
        backtest = RealWorldDynamicBacktest(
            use_dynamic_screening=True,
            db_recording=False
        )
    elif choice == "2":
        backtest = RealWorldDynamicBacktest(
            use_dynamic_screening=True,
            db_recording=True
        )
    elif choice == "3":
        backtest = RealWorldDynamicBacktest(
            use_dynamic_screening=False,
            db_recording=False
        )
    else:
        print("잘못된 선택입니다. 기본값(1)으로 실행합니다.")
        backtest = RealWorldDynamicBacktest(
            use_dynamic_screening=True,
            db_recording=False
        )

    backtest.run()