-- 001: live_cash_flows 테이블 추가 (입출금 자동 감지 이력)
-- 이미 schema.sql 로 새로 만든 DB 에는 포함되어 있으므로 기존 DB 에만 적용한다.
-- psql -h localhost -p 5432 -U <user> -d <db> -f sql/migrations/001_live_cash_flows.sql

CREATE TABLE IF NOT EXISTS live_cash_flows (
    id SERIAL PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id),
    flow_date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    amount NUMERIC(15, 2) NOT NULL,
    kis_cash NUMERIC(15, 2) NOT NULL,
    db_cash NUMERIC(15, 2) NOT NULL,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_live_cash_flows_account_date ON live_cash_flows(account_id, flow_date);
