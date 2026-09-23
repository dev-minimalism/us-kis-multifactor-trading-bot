#!/usr/bin/env python3
"""
Run the multifactor trading bot in safe mode (DRY_RUN=True, KIS_VIRTUAL=True).
"""

import sys
import os
import threading
from pathlib import Path

# Force safe mode
os.environ['DRY_RUN'] = 'True'
os.environ['KIS_VIRTUAL'] = 'True'

# Add src to path
src_path = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(src_path))

from multifactor_bot.core.bot import (
    MultiFactorBot,
    run_telegram_listener,
    TELEGRAM_LIB_AVAILABLE
)

if __name__ == "__main__":
    print("=" * 80)
    print("🔒 SAFE MODE: DRY_RUN=True, KIS_VIRTUAL=True")
    print("=" * 80)

    # 1. 봇 인스턴스 생성
    bot = MultiFactorBot()

    # 2. 텔레그램 리스너 스레드 시작
    if TELEGRAM_LIB_AVAILABLE and bot.tg_token:
        tg_thread = threading.Thread(
            target=run_telegram_listener,
            args=(bot,),
            daemon=True
        )
        tg_thread.start()
        print("📡 텔레그램 리스너 시작됨")
    else:
        print("ℹ️ 텔레그램 기능 없이 실행됩니다.")

    # 3. 메인 트레이딩 루프 실행
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n🛑 프로그램 종료 요청")
        bot.send_telegram("🛑 봇 시스템 종료")
        sys.exit(0)