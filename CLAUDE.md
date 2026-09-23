# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **US Multi-Factor Quantitative Trading Bot** that implements a systematic trading strategy for US stocks using multiple quantitative factors. The bot targets an annual return of 30% through a combination of momentum, value, quality, and low-volatility factors.

**Broker**: "KIS" throughout this codebase means **Korea Investment & Securities (한국투자증권)**. Orders, balance and holdings queries go through the KIS Open API (`brokers/kis_*.py`); market data comes primarily from yfinance with KIS as fallback. KIS provides separate real and virtual (모의투자) endpoints, selected by `KIS_VIRTUAL`.

**Core Strategy:**
- 4-Factor Model: Momentum (40%), Value (20%), Quality (20%), Low Volatility (20%)
- Portfolio: Top 15 stocks selected via composite Z-scores
- Rebalancing: Every 30 days
- Risk Management: -15% stop loss, -15% trailing stop, min 7 days / max 90 days holding period

## Development Commands

### Environment Setup
```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Install in development mode (editable)
pip install -e .
```

### Running the Bot

```bash
# Backtest the strategy (ALWAYS run this first)
python scripts/run_backtest.py
# or: multifactor-backtest (if installed with pip install -e .)

# Dynamic backtest (with S&P500+NASDAQ100 screening)
python scripts/run_dynamic_backtest.py

# Run bot in safe mode (DRY_RUN=True, KIS_VIRTUAL=True forced)
python scripts/run_us_kis_multifactor_trading_bot_safe.py

# Run bot in normal mode (uses .env settings)
python scripts/run_us_kis_multifactor_trading_bot.py
# or: multifactor-bot (if installed)
```

### Testing

```bash
# Run all tests (pyproject adds --cov automatically)
pytest

# Fast run without coverage
pytest --no-cov -q

# Test telegram connection (interactive script, not a pytest test)
python scripts/check_telegram_connection.py
```

**Test layout** (`tests/`, no network and no PostgreSQL required):
- `conftest.py`: `FakeDB` (records SQL, returns canned rows by substring), `FakeResponse`, `fake_kis_client`
- `test_kis_client.py`: token issue/refresh/retry, per-market singleton
- `test_kis_trader.py`: balance/holdings parsing, required KIS params, pagination, `None` on failure, dry-run orders
- `test_live_trading.py`: cash bookkeeping, `last_synced_at` preservation, position merge, first-sync and deposit/withdrawal detection
- `test_paper_trading.py`: buy/sell cash effects, reset
- `test_bot.py`: DB holdings restore, live sync wiring, scoring, factor helpers, buy/sell, risk rules, rebalance plan, ET-based market hours (incl. DST and KST-weekend edge cases)
- `test_config.py`: weight sums and threshold sanity
- `test_sectors.py`, `test_dynamic_screener.py`: sector normalization, filter, yfinance fallback cache
- `test_historical_constituents.py`, `test_dynamic_backtest.py`: point-in-time universe, cache/refresh, liquidity filter, sector filter

When adding logic that touches KIS or the DB, test it through these fakes rather than hitting the real services. `KISClient._instances` must be reset between tests (see `reset_singleton` fixture).

### Code Quality

```bash
# Format code with black
black src/ tests/ scripts/

# Check code style
flake8 src/ tests/

# Type checking
mypy src/
```

## Architecture

### Three-Layer System Architecture

1. **Data Layer (`brokers/`)**
   - `kis_client.py`: KIS API client with auto-refreshing tokens (singleton per market)
   - `kis_data_provider.py`: Unified data interface (yfinance primary, KIS API fallback)
   - `kis_trader.py`: Order execution (buy/sell with dry-run support)

2. **Strategy Layer**
   - `screening/dynamic_screener.py`: Dynamic universe selection from S&P500 + NASDAQ100
   - `core/bot.py`: Main trading bot with telegram control and market hours check
   - `core/bot_dynamic.py`: Alternative bot using dynamic screening
   - Factor calculation happens in bot classes (momentum, value, quality, volatility)

3. **Persistence Layer (`database/`)**
   - `db_manager.py`: PostgreSQL connection pooling
   - `paper_trading.py`: Virtual trading database manager
   - `live_trading.py`: Real trading database manager with KIS API sync
   - Schema in `sql/schema.sql`: Separate tables for paper vs live trading

### Key Design Patterns

**Singleton Pattern**: `KISClient` uses per-market singleton to avoid redundant token refreshes

**Dual-Mode Trading**:
- Paper trading uses local PostgreSQL database to track virtual portfolio
- Live trading syncs with KIS API for real account balance
- Both modes share same strategy logic but different persistence

**Source of Truth per Mode**:
- Paper: the DB is the truth for cash and positions
- Live: the KIS account is the truth for cash, share counts and average price; the DB is the truth for `buy_date` and `highest_price` (needed for hold-period and trailing-stop rules, which KIS does not provide)

**Live Account Sync** (`LiveTradingManager.sync_from_kis`, called from `bot._sync_live_account_from_kis`):
- Runs at bot startup and again right before each rebalance, only when `DB_ENABLED=True` and `PAPER_TRADING_ENABLED=False`
- First sync (`live_account_balance.last_synced_at IS NULL`, i.e. right after a DB reset) overwrites `accounts.initial_capital` with KIS cash + holdings value
- Later syncs compare KIS cash with DB cash. DB cash only changes through bot trades, so the difference is "cash the bot did not cause". Below `LIVE_CASH_FLOW_THRESHOLD_USD` it is treated as drift (fees, FX) and just overwritten. Above it, it is treated as a deposit/withdrawal: added to `initial_capital`, logged to `live_cash_flows`, and announced on Telegram
- Positions are merged, not replaced: tickers only in KIS are added (buy_date = now), tickers in both keep their DB buy_date/highest_price, tickers only in the DB are deleted
- If the KIS query fails, `KISTrader.get_balance()` / `get_positions()` return `None` and the sync is skipped. Never treat `None` as zero cash

**Dynamic vs Static Universe**:
- Static: Fixed `WATCHLIST` in `config.py` (30 GICS Information Technology tickers, fallback only)
- Dynamic: Real-time filtering of S&P500 + NASDAQ100 by market cap, price, volume
- Screener caches results to avoid excessive API calls

**Look-Ahead / Survivorship Bias Prevention**:
- Backtests use historical data ONLY up to signal date
- Order execution uses next-day open price (realistic slippage)
- Dynamic backtest (default mode) uses point-in-time S&P 500 constituents per rebalance date from `screening/historical_constituents.py` (GitHub `fja05680/sp500` ticker start/end dataset, cached in `.cache/`), then the sector filter and a signal-date liquidity filter
- Known remaining bias: value/quality factors read current yfinance `info`; delisted tickers have no price history and drop out. `config.WATCHLIST` (30 current GICS IT large caps, chosen 2026-09-23) is a fallback only and is heavily survivorship-biased

### Critical Flow: Bot Execution

1. **Market Hours Check** (`bot.is_market_open`): judged in `MARKET_TIMEZONE` (America/New_York, DST-aware) against `config.MARKET_SESSIONS` (day_market 20:00-03:50, pre 04:00-09:30, regular 09:30-16:00, after 16:00-19:50 ET). Saturday closed; Sunday opens at the day-market start; Friday closes at the day-market start. Logs and Telegram render times in `DISPLAY_TIMEZONE` (Asia/Seoul). US holidays are not handled
2. **Rebalancing Decision** (`bot.py:224-228`): Check if N days passed since last rebalance
3. **Universe Selection**:
   - Dynamic mode: `screener.screen_universe()` fetches current S&P500+NASDAQ100 constituents
   - Static mode: Uses `config.WATCHLIST`
4. **Factor Calculation** (`bot.py:294-338`):
   - Downloads 1-year price data for each ticker
   - Calculates 4 factors, normalizes to Z-scores, combines with weights
5. **Portfolio Construction**: Select top N stocks by composite score
6. **Order Execution**:
   - Sell tickers not in new portfolio
   - Buy new tickers with equal-weighted allocation
   - DB recording (if enabled) tracks all trades
7. **Risk Management** (`bot.py:498-528`): Continuous monitoring for stop-loss, trailing stop, time-based exits

### Database Schema Design

The database uses **complete separation** between paper and live trading:

- **Paper Trading Tables**: `paper_trades`, `paper_positions`, `paper_account_balance`, `paper_portfolio_snapshots`
- **Live Trading Tables**: `live_trades`, `live_positions`, `live_account_balance`, `live_portfolio_snapshots`, `live_cash_flows` (auto-detected deposits/withdrawals)
- **Shared Tables**: `accounts`, `factor_scores`, `rebalancing_events`

This prevents accidental mixing of virtual and real trading data.

**Account IDs are hard-coded**: `PaperTradingManager` defaults to `account_id=1` and `LiveTradingManager` to `account_id=2` (overridable via `PAPER_ACCOUNT_ID` / `LIVE_ACCOUNT_ID`). Any reset or re-seed must recreate the accounts in that order.

**SQL files live in `sql/`**:
- `sql/schema.sql`: full schema for a new database
- `sql/reset_db.sql`: wipe all data and re-seed accounts (see Database Operations)
- `sql/migrations/NNN_*.sql`: idempotent changes to apply to an existing database, in number order

### Configuration System

All parameters are environment-variable based via `.env` file:

**Critical Config Groups**:
- `KIS_*`: Korean Investment & Securities API credentials
- `TELEGRAM_*`: Telegram bot for remote control
- `FACTOR_WEIGHT_*`: Factor model weights (must sum to 1.0)
- `DB_*`: PostgreSQL connection (optional, bot works without DB)
- `SSH_TUNNEL_*`, `SSH_*`: optional SSH tunnel that `db_manager.py` opens to reach a remote PostgreSQL
- `DYNAMIC_SCREENING_*`: Universe filtering parameters
- `LIVE_CASH_FLOW_THRESHOLD_USD`: cash difference (KIS vs DB) above which a live sync records a deposit/withdrawal (default 100)
- `UNIVERSE_SECTORS`: comma-separated GICS sectors the universe is restricted to, applied identically by the live screener and the dynamic backtest (default `Information Technology`; empty = all sectors). Labels come from the Wikipedia S&P 500 (GICS) and NASDAQ-100 (ICB) tables via `screening/sectors.py`, with a cached yfinance fallback for tickers no longer in either table

**Safety Switches**:
- `DRY_RUN`: If True, no real orders are placed
- `KIS_VIRTUAL`: If True, uses KIS virtual trading API endpoint
- `DB_ENABLED`: If True, records all trades to PostgreSQL
- `PAPER_TRADING_ENABLED`: If True, uses paper trading tables (vs live trading tables)

## Common Development Workflows

### Adding a New Factor

1. Add calculation method to bot class (e.g., `_calculate_size_factor()`)
2. Add weight to `config.FACTOR_WEIGHTS` (ensure total = 1.0)
3. Update `calculate_all_factors()` to include new factor column
4. Update `normalize_and_score()` to include in composite score
5. Run backtest to validate impact

### Modifying Risk Management Rules

Risk management logic is in `check_risk_management()` in `core/bot.py`:
- Stop loss: Entry price based
- Trailing stop: Tracks `high_price` in holdings dict
- Time-based exit: Checks `hold_days` against `MAX_HOLD_DAYS`
- `MIN_HOLD_DAYS` gates stop loss AND trailing stop (decided 2026-09-23); the time-based exit ignores it. Rules are evaluated in that priority order and at most one sell fires per ticker per scan
- The dynamic backtest mirrors these rules in `DynamicMultiFactorBacktest.evaluate_exit` / `apply_risk_rules` (daily close between rebalances, reason keys STOP_LOSS / TRAILING_STOP / MAX_HOLD). Keep the two implementations in sync when changing a rule. The static `backtest/backtest.py` still sells only on rebalance signals

All thresholds come from config (e.g., `STOP_LOSS_PERCENT`, `TRAILING_STOP_PERCENT`).

### Database Operations

**Setup PostgreSQL** (if using DB features):
```bash
# Create database
psql -U postgres -c "CREATE DATABASE trading_bot;"

# Initialize schema
psql -U postgres -d trading_bot -f sql/schema.sql

# Check connection in .env
DB_ENABLED=True
DB_HOST=localhost
DB_PORT=5432
DB_NAME=trading_bot
DB_USER=postgres
DB_PASSWORD=your_password
```

**Reset all trading data** (keeps schema, re-seeds accounts 1=Paper, 2=Live):
```bash
# 1. Stop the bot first (there is no supervisor; a stopped bot stays stopped)
# 2. Dry-run: same script with COMMIT swapped for ROLLBACK
sed 's/^COMMIT;/ROLLBACK;/' sql/reset_db.sql | psql -h localhost -p 5432 -U <user> -d <db> -v ON_ERROR_STOP=1
# 3. Real run (optionally -v paper_capital=... -v live_capital=...)
psql -h localhost -p 5432 -U <user> -d <db> -f sql/reset_db.sql
```
After a reset, the next Live-mode start performs a "first sync" and sets `initial_capital` from the real KIS account. Telegram `/reset confirm` only clears the three paper tables; use the SQL for a full wipe.

**Apply a migration to an existing DB**:
```bash
psql -h localhost -p 5432 -U <user> -d <db> -f sql/migrations/001_live_cash_flows.sql
```

**Querying Trading History**:
```sql
-- Paper trading performance
SELECT * FROM v_paper_portfolio_status;
SELECT * FROM v_paper_trade_statistics;

-- Recent paper trades
SELECT * FROM paper_trades ORDER BY date DESC LIMIT 20;

-- Live trading (similar views)
SELECT * FROM v_live_portfolio_status;

-- Deposits/withdrawals detected by live sync
SELECT * FROM live_cash_flows ORDER BY flow_date DESC;
```

### Telegram Bot Commands

When bot is running with `TELEGRAM_TOKEN` configured:
- `/start`: Show available commands
- `/status`: Current portfolio and P&L
- `/trades`: Recent trade history (from DB)
- `/performance`: Win rate, avg return, etc.
- `/mode`: Toggle between dry-run and live trading
- `/force`: Force immediate rebalancing (market hours only)

**Implementation**: Telegram listener runs in separate daemon thread (`bot.py:731-768`), uses async handlers that read from bot instance state.

## Backtest Interpretation

**Output Files** (in `backtest_result/`):
- `portfolio_TIMESTAMP.csv`: Daily portfolio value over time
- `trades_TIMESTAMP.csv`: All buy/sell transactions with P&L
- `summary_TIMESTAMP.csv`: Aggregated metrics (CAGR, win rate, max drawdown)

**Key Metrics**:
- **CAGR**: Compound annual growth rate (target: 30%, historical: 26%)
- **Win Rate**: % of profitable trades (target: >60%)
- **Max Drawdown**: Largest peak-to-trough decline (monitor for risk)
- **Sharpe Ratio**: Risk-adjusted return (target: >1.5)

**Backtesting Modes**:
1. **Static Backtest** (`backtest.py`): Uses fixed `WATCHLIST`, next-day open execution
2. **Dynamic Backtest** (`backtest/dynamic_backtest.py`, `scripts/run_dynamic_backtest.py`): option 1 = point-in-time S&P 500 constituents (default), 3 = current constituents screened once (biased), 4 = fixed WATCHLIST
   Risk rules (stop loss / trailing / min-max hold) are simulated on daily closes between rebalances; `BACKTEST_RISK_RULES=False` disables them for comparison

## Important Technical Notes

### API Rate Limits

**Yahoo Finance** (`yfinance`):
- Dynamic screener limits workers to 5 (configurable via `max_workers`)
- Implemented retry logic and caching in `DynamicScreener`
- If getting 401 errors, set `check_market_cap=False` to skip market cap filtering

**KIS API** (Korea Investment & Securities Open API):
- Token auto-refreshes 5 minutes before expiration (`kis_client.py:78-113`)
- Singleton pattern ensures one token per market (KR/US)
- Virtual trading uses different base URL and `tr_id` prefixes (`V...` vs `T...`) than real trading
- `KISTrader.get_balance()` uses the overseas "inquire-psamount" endpoint and returns 주문가능금액 (orderable cash), not total equity
- `KISTrader.get_positions()` uses "inquire-balance" with `OVRS_EXCG_CD=NASD` (all US exchanges) and follows `tr_cont` pagination
- Both return `None` on failure so callers can distinguish "API down" from "zero"

### Concurrency & Threading

- **Telegram listener**: Runs as daemon thread, uses `asyncio.new_event_loop()` in thread
- **Main bot loop**: Synchronous, runs in main thread
- **Signal handling**: Telegram thread disables signal handlers (`stop_signals=None`) for macOS compatibility
- **Database**: Connection pooling handled by `db_manager.py` (thread-safe)

### Error Handling Philosophy

The bot is designed to **continue running** even when individual components fail:
- Data fetch failures: Skip ticker and continue with others
- DB connection loss: Log warning but keep trading (uses in-memory state)
- KIS balance/holdings query failure at sync time: skip the sync, keep DB values (never overwrite with 0)
- API errors: Retry with exponential backoff
- Telegram send failures: Silent retry, don't block trading logic

Critical failures that **stop execution**:
- Config file missing required fields (KIS credentials)
- Unable to fetch ANY ticker data (empty universe)
- Market hours violation in live trading mode

### Security Considerations

**Never commit**:
- `.env` file (contains API keys)
- Database credentials
- Server access notes, SSH key paths, chat transcripts (keep them out of the repo; `TODO.md` and `claude_chat/` are gitignored)

**API Key Storage**:
- All secrets in `.env` file (gitignored)
- `.env.example` provides template without actual keys
- Config module loads via `python-dotenv`

**Paper Trading Default**:
- `DRY_RUN=True` and `KIS_VIRTUAL=True` are defaults
- `scripts/run_us_kis_multifactor_trading_bot_safe.py` forces these settings
- Real trading requires explicit config change + confirmation

## Getting Help

- Project repository: https://github.com/dev-minimalism/us-kis-multifactor-trading-bot
- README.md contains Korean-language user guide with backtest results
- For KIS API documentation: Check Korean Investment & Securities API docs
- For factor definitions: See strategy description in `config.py:158-185`