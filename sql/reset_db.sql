-- ============================================================================
-- reset_db.sql : 트레이딩 DB 데이터 전체 초기화 (스키마는 유지)
-- ============================================================================
--
-- 목적
--   paper / live 거래 기록, 포지션, 스냅샷, 잔고, 팩터 점수, 리밸런싱 이벤트를
--   모두 비우고 계좌를 초기 자본금 상태로 다시 만든다.
--   테이블, 인덱스, 뷰, 트리거는 그대로 둔다 (schema.sql 재실행 불필요).
--
-- 주의
--   * 코드가 account_id 를 고정으로 사용한다.
--       - PaperTradingDB : account_id = 1
--       - LiveTradingDB  : account_id = 2
--     따라서 RESTART IDENTITY 로 시퀀스를 1부터 다시 시작시키고,
--     계좌를 반드시 Paper(1) -> Live(2) 순서로 다시 넣는다.
--   * 실행 전 봇 프로세스를 먼저 내려라. 실행 중 초기화하면 봇의 메모리 상태와
--     DB 가 어긋난다.
--
-- 사용법
--   psql -h localhost -p 5432 -U <user> -d <db> -f sql/reset_db.sql
--
--   초기 자본금을 바꾸려면 -v 로 넘긴다 (기본: paper 100000, live 50000).
--   psql ... -v paper_capital=200000 -v live_capital=30000 -f sql/reset_db.sql
--
--   삭제 없이 실행만 확인하려면 (dry-run):
--   sed 's/^COMMIT;/ROLLBACK;/' sql/reset_db.sql | psql ... -v ON_ERROR_STOP=1
-- ============================================================================

\set ON_ERROR_STOP on

-- psql 변수 기본값 (-v 로 넘기지 않았을 때만 적용)
\if :{?paper_capital}
\else
  \set paper_capital 100000
\endif
\if :{?live_capital}
\else
  \set live_capital 50000
\endif

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. 초기화 전 현황 (로그용)
-- ----------------------------------------------------------------------------
\echo '--- 초기화 전 행 수 ---'
SELECT 'accounts'                  AS "table", count(*) FROM accounts                  UNION ALL
SELECT 'paper_trades',                count(*) FROM paper_trades                       UNION ALL
SELECT 'paper_positions',             count(*) FROM paper_positions                    UNION ALL
SELECT 'paper_portfolio_snapshots',   count(*) FROM paper_portfolio_snapshots          UNION ALL
SELECT 'paper_account_balance',       count(*) FROM paper_account_balance              UNION ALL
SELECT 'live_trades',                 count(*) FROM live_trades                        UNION ALL
SELECT 'live_positions',              count(*) FROM live_positions                     UNION ALL
SELECT 'live_portfolio_snapshots',    count(*) FROM live_portfolio_snapshots           UNION ALL
SELECT 'live_account_balance',        count(*) FROM live_account_balance               UNION ALL
SELECT 'live_cash_flows',             count(*) FROM live_cash_flows                    UNION ALL
SELECT 'factor_scores',               count(*) FROM factor_scores                      UNION ALL
SELECT 'rebalancing_events',          count(*) FROM rebalancing_events;

-- ----------------------------------------------------------------------------
-- 2. 데이터 전체 삭제
--    - RESTART IDENTITY : 모든 SERIAL 시퀀스를 1부터 재시작
--    - CASCADE          : accounts 를 참조하는 FK 테이블까지 함께 비움
-- ----------------------------------------------------------------------------
TRUNCATE TABLE
    paper_trades,
    paper_positions,
    paper_portfolio_snapshots,
    paper_account_balance,
    live_trades,
    live_positions,
    live_portfolio_snapshots,
    live_account_balance,
    live_cash_flows,
    factor_scores,
    rebalancing_events,
    accounts
RESTART IDENTITY CASCADE;

-- ----------------------------------------------------------------------------
-- 3. 계좌 재생성 (schema.sql 의 초기 데이터와 동일한 순서/ID)
-- ----------------------------------------------------------------------------
-- id = 1 : Paper Trading (활성)
INSERT INTO accounts (name, mode, initial_capital, is_active)
VALUES ('Paper Trading Account', 'PAPER', :paper_capital, TRUE);

INSERT INTO paper_account_balance (account_id, current_cash)
VALUES (1, :paper_capital);

-- id = 2 : Live Trading (기본 비활성)
--   last_synced_at = NULL 로 두면 봇이 Live 모드로 처음 뜰 때 KIS 실계좌 기준으로
--   initial_capital 과 current_cash 를 덮어쓴다 (LiveTradingManager.sync_from_kis)
INSERT INTO accounts (name, mode, initial_capital, is_active)
VALUES ('Live Trading Account', 'LIVE', :live_capital, FALSE);

INSERT INTO live_account_balance (account_id, current_cash, synced_from_kis)
VALUES (2, :live_capital, FALSE);

-- ----------------------------------------------------------------------------
-- 4. 검증: 계좌 ID 가 코드의 가정(1=Paper, 2=Live)과 맞는지 확인
--    맞지 않으면 예외로 트랜잭션 전체가 롤백된다.
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF (SELECT mode FROM accounts WHERE id = 1) IS DISTINCT FROM 'PAPER' THEN
        RAISE EXCEPTION 'accounts.id=1 이 PAPER 가 아닙니다. 초기화를 중단합니다.';
    END IF;
    IF (SELECT mode FROM accounts WHERE id = 2) IS DISTINCT FROM 'LIVE' THEN
        RAISE EXCEPTION 'accounts.id=2 가 LIVE 가 아닙니다. 초기화를 중단합니다.';
    END IF;
END
$$;

-- ----------------------------------------------------------------------------
-- 5. 초기화 후 현황
-- ----------------------------------------------------------------------------
\echo '--- 초기화 후 계좌 ---'
SELECT a.id, a.name, a.mode, a.initial_capital, a.is_active,
       COALESCE(p.current_cash, l.current_cash) AS current_cash
FROM accounts a
LEFT JOIN paper_account_balance p ON p.account_id = a.id
LEFT JOIN live_account_balance  l ON l.account_id = a.id
ORDER BY a.id;

COMMIT;

\echo '✅ DB 초기화 완료'
