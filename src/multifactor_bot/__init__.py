"""
US Multifactor Trading Bot
===========================

A quantitative trading bot using multi-factor strategy for US stocks.

Modules:
    - core: Core bot and backtesting functionality
    - brokers: Broker integrations (KIS)
    - notifications: Notification services (Telegram)
    - config: Configuration management
"""

__version__ = '1.0.0'
__author__ = 'pink-spider'

from . import config

# Lazy import to avoid import errors when dependencies are missing
def __getattr__(name):
    if name == 'MultiFactorBot':
        from .core.bot import MultiFactorBot
        return MultiFactorBot
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ['config', 'MultiFactorBot']