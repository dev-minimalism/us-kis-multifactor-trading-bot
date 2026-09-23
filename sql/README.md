# sql/

| 파일 | 용도 |
|---|---|
| `schema.sql` | 테이블·뷰·트리거 최초 생성 (새 DB에 한 번) |
| `reset_db.sql` | 데이터만 전체 삭제 후 계좌를 초기 자본금으로 재생성. 스키마 유지 |
| `migrations/NNN_*.sql` | 기존 DB 에 스키마 변경을 적용 (번호 순서대로, 멱등) |

## 초기화 절차

1. 봇 프로세스 종료 (실행 중 초기화 금지)
2. SSH 터널이 열려 있는지 확인 (`DB_HOST=localhost:5432`)
3. dry-run 으로 확인
   ```bash
   sed 's/^COMMIT;/ROLLBACK;/' sql/reset_db.sql \
     | psql -h localhost -p 5432 -U <user> -d <db> -v ON_ERROR_STOP=1
   ```
4. 실제 실행
   ```bash
   psql -h localhost -p 5432 -U <user> -d <db> -f sql/reset_db.sql
   ```
5. 봇 재시작. 첫 리밸런싱부터 새로 기록됨

초기 자본금 변경: `-v paper_capital=200000 -v live_capital=30000`

Paper 계좌만 비우고 싶을 때는 SQL 대신 텔레그램 `/reset confirm` 을 쓰면 된다
(`PaperTradingDB.reset_trading_data`). 이 SQL 은 live·factor_scores·rebalancing_events 까지 모두 지운다.
