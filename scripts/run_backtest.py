#!/usr/bin/env python3
"""
Run backtesting for the multifactor trading strategy.
"""

import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(src_path))

if __name__ == "__main__":
    from multifactor_bot.backtest.backtest import MultiFactorBacktest

    backtest = MultiFactorBacktest()
    backtest.run()