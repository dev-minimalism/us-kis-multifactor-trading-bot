-- Trading Database Schema for Multi-Factor Trading Bot
-- PostgreSQL
-- 가상 거래(Paper Trading)와 실제 거래(Live Trading) 테이블 완전 분리

-- ============================================================================
-- 공통 테이블
-- ============================================================================

-- 1. 계좌 정보 테이블 (공통)
CREATE TABLE accounts (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    mode VARCHAR(20) NOT NULL DEFAULT 'PAPER', -- PAPER, LIVE
    initial_capital NUMERIC(15, 2) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- PAPER 또는 LIVE 모드만 허용
    CHECK (mode IN ('PAPER', 'LIVE'))
);

-- ============================================================================
-- 가상 거래 테이블 (PAPER TRADING)
-- ============================================================================

-- 2-1. 가상 거래 내역 테이블
CREATE TABLE paper_trades (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id),
    date TIMESTAMP NOT NULL,
    ticker VARCHAR(20) NOT NULL,
    action VARCHAR(10) NOT NULL, -- BUY, SELL
    shares INTEGER NOT NULL,
    price NUMERIC(12, 4) NOT NULL,
    total_value NUMERIC(15, 2) NOT NULL,
    commission NUMERIC(10, 2) DEFAULT 0,
    pnl NUMERIC(10, 2), -- 매도시에만 기록
    pnl_percent NUMERIC(8, 2), -- 수익률 (%)
    days_held INTEGER, -- 보유 일수
    exit_reason VARCHAR(30), -- SIGNAL, STOP_LOSS, TRAILING_STOP, TAKE_PROFIT
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_paper_trades_account ON paper_trades(account_id);
CREATE INDEX idx_paper_trades_date ON paper_trades(date);
CREATE INDEX idx_paper_trades_ticker ON paper_trades(ticker);
CREATE INDEX idx_paper_trades_action ON paper_trades(action);

-- 2-2. 가상 현재 포지션 테이블
CREATE TABLE paper_positions (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id),
    ticker VARCHAR(20) NOT NULL,
    shares INTEGER NOT NULL,
    avg_price NUMERIC(12, 4) NOT NULL,
    buy_date TIMESTAMP NOT NULL,
    highest_price NUMERIC(12, 4), -- 트레일링 스탑용
    current_price NUMERIC(12, 4),
    current_value NUMERIC(15, 2),
    unrealized_pnl NUMERIC(10, 2),
    unrealized_pnl_percent NUMERIC(8, 2),
    stop_loss_price NUMERIC(12, 4),
    trailing_stop_price NUMERIC(12, 4),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(account_id, ticker)
);

CREATE INDEX idx_paper_positions_account ON paper_positions(account_id);

-- 2-3. 가상 포트폴리오 스냅샷 테이블
CREATE TABLE paper_portfolio_snapshots (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id),
    snapshot_date TIMESTAMP NOT NULL,
    cash NUMERIC(15, 2) NOT NULL,
    positions_value NUMERIC(15, 2) NOT NULL,
    total_value NUMERIC(15, 2) NOT NULL,
    daily_return NUMERIC(8, 4), -- 일일 수익률 (%)
    cumulative_return NUMERIC(10, 4), -- 누적 수익률 (%)
    num_positions INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(account_id, snapshot_date)
);

CREATE INDEX idx_paper_snapshots_account_date ON paper_portfolio_snapshots(account_id, snapshot_date);

-- 2-4. 가상 거래 현금 잔고 테이블
CREATE TABLE paper_account_balance (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id) UNIQUE,
    current_cash NUMERIC(15, 2) NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 실제 거래 테이블 (LIVE TRADING)
-- ============================================================================

-- 3-1. 실제 거래 내역 테이블
CREATE TABLE live_trades (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id),
    date TIMESTAMP NOT NULL,
    ticker VARCHAR(20) NOT NULL,
    action VARCHAR(10) NOT NULL, -- BUY, SELL
    shares INTEGER NOT NULL,
    price NUMERIC(12, 4) NOT NULL,
    total_value NUMERIC(15, 2) NOT NULL,
    commission NUMERIC(10, 2) DEFAULT 0,
    pnl NUMERIC(10, 2),
    pnl_percent NUMERIC(8, 2),
    days_held INTEGER,
    exit_reason VARCHAR(30),
    order_id VARCHAR(50), -- KIS API 주문번호
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_live_trades_account ON live_trades(account_id);
CREATE INDEX idx_live_trades_date ON live_trades(date);
CREATE INDEX idx_live_trades_ticker ON live_trades(ticker);
CREATE INDEX idx_live_trades_action ON live_trades(action);

-- 3-2. 실제 현재 포지션 테이블
CREATE TABLE live_positions (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id),
    ticker VARCHAR(20) NOT NULL,
    shares INTEGER NOT NULL,
    avg_price NUMERIC(12, 4) NOT NULL,
    buy_date TIMESTAMP NOT NULL,
    highest_price NUMERIC(12, 4),
    current_price NUMERIC(12, 4),
    current_value NUMERIC(15, 2),
    unrealized_pnl NUMERIC(10, 2),
    unrealized_pnl_percent NUMERIC(8, 2),
    stop_loss_price NUMERIC(12, 4),
    trailing_stop_price NUMERIC(12, 4),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(account_id, ticker)
);

CREATE INDEX idx_live_positions_account ON live_positions(account_id);

-- 3-3. 실제 포트폴리오 스냅샷 테이블
CREATE TABLE live_portfolio_snapshots (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id),
    snapshot_date TIMESTAMP NOT NULL,
    cash NUMERIC(15, 2) NOT NULL,
    positions_value NUMERIC(15, 2) NOT NULL,
    total_value NUMERIC(15, 2) NOT NULL,
    daily_return NUMERIC(8, 4),
    cumulative_return NUMERIC(10, 4),
    num_positions INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(account_id, snapshot_date)
);

CREATE INDEX idx_live_snapshots_account_date ON live_portfolio_snapshots(account_id, snapshot_date);

-- 3-4. 실제 거래 현금 잔고 테이블 (KIS API 동기화)
CREATE TABLE live_account_balance (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id) UNIQUE,
    current_cash NUMERIC(15, 2) NOT NULL,
    synced_from_kis BOOLEAN DEFAULT FALSE,
    last_synced_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3-5. 실제 계좌 입출금 이력 (KIS 동기화 시 자동 감지)
--      DB 현금과 KIS 현금의 차이가 LIVE_CASH_FLOW_THRESHOLD_USD 를 넘으면
--      입출금으로 간주해 여기에 기록하고 accounts.initial_capital 을 조정한다.
CREATE TABLE live_cash_flows (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id),
    flow_date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    amount NUMERIC(15, 2) NOT NULL,        -- +입금 / -출금
    kis_cash NUMERIC(15, 2) NOT NULL,      -- 감지 시점 KIS 현금
    db_cash NUMERIC(15, 2) NOT NULL,       -- 감지 시점 DB 현금 (봇이 예상한 값)
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_live_cash_flows_account_date ON live_cash_flows(account_id, flow_date);

-- ============================================================================
-- 공통 분석 테이블
-- ============================================================================

-- 4. 팩터 점수 기록 테이블 (가상/실제 공통)
CREATE TABLE factor_scores (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id),
    calculation_date TIMESTAMP NOT NULL,
    ticker VARCHAR(20) NOT NULL,
    price NUMERIC(12, 4),
    momentum_score NUMERIC(8, 4),
    value_score NUMERIC(8, 4),
    quality_score NUMERIC(8, 4),
    volatility_score NUMERIC(8, 4),
    composite_score NUMERIC(8, 4),
    rank INTEGER,
    selected BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_factors_account_date ON factor_scores(account_id, calculation_date);
CREATE INDEX idx_factors_ticker ON factor_scores(ticker);

-- 5. 리밸런싱 이벤트 테이블 (가상/실제 공통)
CREATE TABLE rebalancing_events (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id),
    event_date TIMESTAMP NOT NULL,
    portfolio_value_before NUMERIC(15, 2),
    portfolio_value_after NUMERIC(15, 2),
    num_sells INTEGER DEFAULT 0,
    num_buys INTEGER DEFAULT 0,
    mode VARCHAR(20) NOT NULL, -- PAPER, LIVE
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_rebalancing_account ON rebalancing_events(account_id);

-- ============================================================================
-- 트리거 및 함수
-- ============================================================================

-- 업데이트 시간 자동 갱신 트리거
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_accounts_updated_at BEFORE UPDATE ON accounts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_paper_positions_updated_at BEFORE UPDATE ON paper_positions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_live_positions_updated_at BEFORE UPDATE ON live_positions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_paper_balance_updated_at BEFORE UPDATE ON paper_account_balance
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_live_balance_updated_at BEFORE UPDATE ON live_account_balance
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- 초기 데이터 및 뷰
-- ============================================================================

-- 초기 계좌 생성 (Paper Trading)
INSERT INTO accounts (name, mode, initial_capital)
VALUES ('Paper Trading Account', 'PAPER', 100000.00);

INSERT INTO paper_account_balance (account_id, current_cash)
VALUES (1, 100000.00);

-- 초기 계좌 생성 (Live Trading)
INSERT INTO accounts (name, mode, initial_capital, is_active)
VALUES ('Live Trading Account', 'LIVE', 100000.00, FALSE); -- 기본은 비활성

INSERT INTO live_account_balance (account_id, current_cash, synced_from_kis)
VALUES (2, 100000.00, FALSE);

-- 유용한 뷰: 가상 거래 포트폴리오 상태
CREATE VIEW v_paper_portfolio_status AS
SELECT
    a.id as account_id,
    a.name as account_name,
    b.current_cash,
    COALESCE(SUM(p.current_value), 0) as positions_value,
    b.current_cash + COALESCE(SUM(p.current_value), 0) as total_value,
    a.initial_capital,
    ((b.current_cash + COALESCE(SUM(p.current_value), 0) - a.initial_capital) / a.initial_capital * 100) as total_return_percent,
    COUNT(p.id) as num_positions
FROM accounts a
JOIN paper_account_balance b ON a.id = b.account_id
LEFT JOIN paper_positions p ON a.id = p.account_id
WHERE a.is_active = TRUE AND a.mode = 'PAPER'
GROUP BY a.id, a.name, b.current_cash, a.initial_capital;

-- 유용한 뷰: 실제 거래 포트폴리오 상태
CREATE VIEW v_live_portfolio_status AS
SELECT
    a.id as account_id,
    a.name as account_name,
    b.current_cash,
    COALESCE(SUM(p.current_value), 0) as positions_value,
    b.current_cash + COALESCE(SUM(p.current_value), 0) as total_value,
    a.initial_capital,
    ((b.current_cash + COALESCE(SUM(p.current_value), 0) - a.initial_capital) / a.initial_capital * 100) as total_return_percent,
    COUNT(p.id) as num_positions
FROM accounts a
JOIN live_account_balance b ON a.id = b.account_id
LEFT JOIN live_positions p ON a.id = p.account_id
WHERE a.is_active = TRUE AND a.mode = 'LIVE'
GROUP BY a.id, a.name, b.current_cash, a.initial_capital;

-- 유용한 뷰: 가상 거래 통계
CREATE VIEW v_paper_trade_statistics AS
SELECT
    account_id,
    COUNT(*) FILTER (WHERE action = 'BUY') as total_buys,
    COUNT(*) FILTER (WHERE action = 'SELL') as total_sells,
    COUNT(*) FILTER (WHERE action = 'SELL' AND pnl > 0) as winning_trades,
    COUNT(*) FILTER (WHERE action = 'SELL' AND pnl <= 0) as losing_trades,
    ROUND(AVG(pnl) FILTER (WHERE action = 'SELL'), 2) as avg_pnl,
    ROUND(AVG(pnl_percent) FILTER (WHERE action = 'SELL'), 2) as avg_pnl_percent,
    ROUND(MAX(pnl_percent) FILTER (WHERE action = 'SELL'), 2) as max_gain_percent,
    ROUND(MIN(pnl_percent) FILTER (WHERE action = 'SELL'), 2) as max_loss_percent,
    ROUND(AVG(days_held) FILTER (WHERE action = 'SELL'), 1) as avg_holding_days
FROM paper_trades
GROUP BY account_id;

-- 유용한 뷰: 실제 거래 통계
CREATE VIEW v_live_trade_statistics AS
SELECT
    account_id,
    COUNT(*) FILTER (WHERE action = 'BUY') as total_buys,
    COUNT(*) FILTER (WHERE action = 'SELL') as total_sells,
    COUNT(*) FILTER (WHERE action = 'SELL' AND pnl > 0) as winning_trades,
    COUNT(*) FILTER (WHERE action = 'SELL' AND pnl <= 0) as losing_trades,
    ROUND(AVG(pnl) FILTER (WHERE action = 'SELL'), 2) as avg_pnl,
    ROUND(AVG(pnl_percent) FILTER (WHERE action = 'SELL'), 2) as avg_pnl_percent,
    ROUND(MAX(pnl_percent) FILTER (WHERE action = 'SELL'), 2) as max_gain_percent,
    ROUND(MIN(pnl_percent) FILTER (WHERE action = 'SELL'), 2) as max_loss_percent,
    ROUND(AVG(days_held) FILTER (WHERE action = 'SELL'), 1) as avg_holding_days
FROM live_trades
GROUP BY account_id;