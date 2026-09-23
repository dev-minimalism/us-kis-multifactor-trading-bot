# paper_trading.py - 가상 거래 데이터베이스 관리자

from datetime import datetime
from typing import Dict, List, Optional
import logging
from .db_manager import get_db_manager

logger = logging.getLogger(__name__)


class PaperTradingManager:
    """가상 거래(Paper Trading) 데이터베이스 매니저"""

    def __init__(self, account_id: int = 1):
        self.db = get_db_manager()
        self.account_id = account_id

    # ===========================
    # 현금 잔고 관리
    # ===========================

    def get_cash_balance(self) -> float:
        """현재 현금 잔고 조회"""
        query = "SELECT current_cash FROM paper_account_balance WHERE account_id = %s"
        result = self.db.fetch_one(query, (self.account_id,))
        return float(result['current_cash']) if result else 0.0

    def update_cash_balance(self, new_cash: float) -> None:
        """현금 잔고 업데이트"""
        query = """
        UPDATE paper_account_balance
        SET current_cash = %s
        WHERE account_id = %s
        """
        self.db.execute_query(query, (new_cash, self.account_id))
        logger.info(f"💰 Paper Trading 현금 잔고 업데이트: ${new_cash:,.2f}")

    def adjust_cash(self, amount: float) -> float:
        """현금 증감 (매수시 -, 매도시 +)"""
        current = self.get_cash_balance()
        new_cash = current + amount
        self.update_cash_balance(new_cash)
        return new_cash

    # ===========================
    # 거래 내역 기록
    # ===========================

    def record_buy(self, ticker: str, shares: int, price: float,
                   commission: float = 0.0, notes: str = None) -> int:
        """매수 기록"""
        # numpy 타입을 Python 네이티브 타입으로 변환
        shares = int(shares)
        price = float(price)
        commission = float(commission)
        total_value = shares * price
        query = """
        INSERT INTO paper_trades (account_id, date, ticker, action, shares, price,
                                 total_value, commission, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """
        result = self.db.fetch_one(query, (
            self.account_id, datetime.now(), ticker, 'BUY', shares, price,
            total_value, commission, notes
        ))

        # 현금 차감
        self.adjust_cash(-(total_value + commission))

        logger.info(f"🛒 Paper Trading 매수 기록: {ticker} {shares}주 @ ${price:.2f}")
        return result['id'] if result else None

    def record_sell(self, ticker: str, shares: int, price: float,
                    buy_price: float, buy_date: datetime,
                    commission: float = 0.0, exit_reason: str = 'SIGNAL',
                    notes: str = None) -> int:
        """매도 기록"""
        # numpy 타입을 Python 네이티브 타입으로 변환
        shares = int(shares)
        price = float(price)
        buy_price = float(buy_price)
        commission = float(commission)
        total_value = shares * price
        pnl = (price - buy_price) * shares
        pnl_percent = ((price / buy_price) - 1) * 100
        days_held = (datetime.now() - buy_date).days

        query = """
        INSERT INTO paper_trades (account_id, date, ticker, action, shares, price,
                                 total_value, commission, pnl, pnl_percent,
                                 days_held, exit_reason, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """
        result = self.db.fetch_one(query, (
            self.account_id, datetime.now(), ticker, 'SELL', shares, price,
            total_value, commission, pnl, pnl_percent, days_held, exit_reason, notes
        ))

        # 현금 증가
        self.adjust_cash(total_value - commission)

        logger.info(f"💰 Paper Trading 매도 기록: {ticker} {shares}주 @ ${price:.2f} (PnL: {pnl_percent:+.2f}%)")
        return result['id'] if result else None

    # ===========================
    # 포지션 관리
    # ===========================

    def get_positions(self) -> List[Dict]:
        """현재 보유 포지션 조회"""
        query = "SELECT * FROM paper_positions WHERE account_id = %s"
        return self.db.fetch_all(query, (self.account_id,))

    def get_position(self, ticker: str) -> Optional[Dict]:
        """특정 종목 포지션 조회"""
        query = "SELECT * FROM paper_positions WHERE account_id = %s AND ticker = %s"
        return self.db.fetch_one(query, (self.account_id, ticker))

    def add_position(self, ticker: str, shares: int, avg_price: float,
                     buy_date: datetime = None) -> None:
        """포지션 추가"""
        # numpy 타입을 Python 네이티브 타입으로 변환
        shares = int(shares)
        avg_price = float(avg_price)
        current_value = shares * avg_price
        if buy_date is None:
            buy_date = datetime.now()

        query = """
        INSERT INTO paper_positions (account_id, ticker, shares, avg_price,
                                     buy_date, highest_price, current_price, current_value)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (account_id, ticker) DO UPDATE
        SET shares = paper_positions.shares + EXCLUDED.shares,
            avg_price = ((paper_positions.avg_price * paper_positions.shares) +
                        (EXCLUDED.avg_price * EXCLUDED.shares)) /
                        (paper_positions.shares + EXCLUDED.shares),
            current_value = (paper_positions.shares + EXCLUDED.shares) *
                           ((paper_positions.avg_price * paper_positions.shares +
                             EXCLUDED.avg_price * EXCLUDED.shares) /
                            (paper_positions.shares + EXCLUDED.shares))
        """
        self.db.execute_query(query, (
            self.account_id, ticker, shares, avg_price, buy_date, avg_price, avg_price, current_value
        ))
        logger.info(f"📊 Paper Trading 포지션 추가: {ticker} {shares}주")

    def remove_position(self, ticker: str) -> None:
        """포지션 제거"""
        query = "DELETE FROM paper_positions WHERE account_id = %s AND ticker = %s"
        self.db.execute_query(query, (self.account_id, ticker))
        logger.info(f"📊 Paper Trading 포지션 제거: {ticker}")

    def update_position_price(self, ticker: str, current_price: float) -> None:
        """포지션 현재 가격 업데이트"""
        query = """
        UPDATE paper_positions
        SET current_price = %s,
            current_value = shares * %s,
            unrealized_pnl = (shares * %s) - (shares * avg_price),
            unrealized_pnl_percent = ((%s / avg_price) - 1) * 100,
            highest_price = GREATEST(highest_price, %s),
            stop_loss_price = avg_price * (1 + %s / 100),
            trailing_stop_price = highest_price * (1 + %s / 100)
        WHERE account_id = %s AND ticker = %s
        """
        from multifactor_bot import config
        self.db.execute_query(query, (
            current_price, current_price, current_price, current_price, current_price,
            config.STOP_LOSS_PERCENT, config.TRAILING_STOP_PERCENT,
            self.account_id, ticker
        ))

    # ===========================
    # 포트폴리오 스냅샷
    # ===========================

    def save_snapshot(self, snapshot_date: datetime = None) -> None:
        """포트폴리오 스냅샷 저장"""
        if snapshot_date is None:
            snapshot_date = datetime.now()

        cash = self.get_cash_balance()
        positions = self.get_positions()
        positions_value = sum(float(p.get('current_value', 0)) for p in positions)
        total_value = cash + positions_value

        # 초기 자본 조회
        account_info = self.db.get_account_info(self.account_id)
        initial_capital = float(account_info['initial_capital'])

        # 누적 수익률 계산
        cumulative_return = ((total_value - initial_capital) / initial_capital) * 100

        # 일일 수익률 계산 (전일 대비)
        prev_snapshot = self.db.fetch_one("""
            SELECT total_value FROM paper_portfolio_snapshots
            WHERE account_id = %s
            ORDER BY snapshot_date DESC
            LIMIT 1
        """, (self.account_id,))

        daily_return = 0.0
        if prev_snapshot:
            prev_value = float(prev_snapshot['total_value'])
            daily_return = ((total_value - prev_value) / prev_value) * 100

        query = """
        INSERT INTO paper_portfolio_snapshots
               (account_id, snapshot_date, cash, positions_value, total_value,
                daily_return, cumulative_return, num_positions)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (account_id, snapshot_date) DO UPDATE
        SET cash = EXCLUDED.cash,
            positions_value = EXCLUDED.positions_value,
            total_value = EXCLUDED.total_value,
            daily_return = EXCLUDED.daily_return,
            cumulative_return = EXCLUDED.cumulative_return,
            num_positions = EXCLUDED.num_positions
        """
        self.db.execute_query(query, (
            self.account_id, snapshot_date, cash, positions_value, total_value,
            daily_return, cumulative_return, len(positions)
        ))
        logger.info(f"📸 Paper Trading 스냅샷 저장: ${total_value:,.2f}")

    # ===========================
    # 통계 조회
    # ===========================

    def get_portfolio_status(self) -> Optional[Dict]:
        """포트폴리오 현재 상태 조회"""
        query = "SELECT * FROM v_paper_portfolio_status WHERE account_id = %s"
        return self.db.fetch_one(query, (self.account_id,))

    def get_trade_statistics(self) -> Optional[Dict]:
        """거래 통계 조회"""
        query = "SELECT * FROM v_paper_trade_statistics WHERE account_id = %s"
        return self.db.fetch_one(query, (self.account_id,))

    def get_recent_trades(self, limit: int = 10) -> List[Dict]:
        """최근 거래 내역 조회"""
        query = """
        SELECT * FROM paper_trades
        WHERE account_id = %s
        ORDER BY date DESC
        LIMIT %s
        """
        return self.db.fetch_all(query, (self.account_id, limit))

    def get_performance_summary(self) -> Dict:
        """성과 요약 조회"""
        status = self.get_portfolio_status()
        stats = self.get_trade_statistics()

        if not status:
            return {}

        return {
            'total_value': float(status.get('total_value', 0)),
            'cash': float(status.get('current_cash', 0)),
            'positions_value': float(status.get('positions_value', 0)),
            'total_return': float(status.get('total_return_percent', 0)),
            'num_positions': int(status.get('num_positions', 0)),
            'total_buys': int(stats.get('total_buys', 0)) if stats else 0,
            'total_sells': int(stats.get('total_sells', 0)) if stats else 0,
            'win_rate': float(stats.get('winning_trades', 0)) / float(stats.get('total_sells', 1)) * 100 if stats and stats.get('total_sells') else 0,
            'avg_pnl_percent': float(stats.get('avg_pnl_percent', 0)) if stats else 0,
        }

    # ===========================
    # 초기화
    # ===========================

    def reset_trading_data(self, initial_cash: float = None) -> Dict:
        """
        Paper Trading 데이터 초기화
        - 모든 포지션 삭제
        - 거래 내역 삭제
        - 스냅샷 삭제
        - 현금 잔고를 초기 자본금으로 리셋

        Args:
            initial_cash: 초기 자본금 (None이면 accounts 테이블의 initial_capital 사용)

        Returns:
            Dict with reset info
        """
        # 초기 자본금 조회
        if initial_cash is None:
            account_info = self.db.get_account_info(self.account_id)
            initial_cash = float(account_info['initial_capital']) if account_info else 100000.0

        # 1. 포지션 삭제
        positions_deleted = len(self.get_positions())
        self.db.execute_query(
            "DELETE FROM paper_positions WHERE account_id = %s",
            (self.account_id,)
        )

        # 2. 거래 내역 삭제
        trades_result = self.db.fetch_one(
            "SELECT COUNT(*) as cnt FROM paper_trades WHERE account_id = %s",
            (self.account_id,)
        )
        trades_deleted = trades_result['cnt'] if trades_result else 0
        self.db.execute_query(
            "DELETE FROM paper_trades WHERE account_id = %s",
            (self.account_id,)
        )

        # 3. 스냅샷 삭제
        snapshots_result = self.db.fetch_one(
            "SELECT COUNT(*) as cnt FROM paper_portfolio_snapshots WHERE account_id = %s",
            (self.account_id,)
        )
        snapshots_deleted = snapshots_result['cnt'] if snapshots_result else 0
        self.db.execute_query(
            "DELETE FROM paper_portfolio_snapshots WHERE account_id = %s",
            (self.account_id,)
        )

        # 4. 현금 잔고 리셋
        self.update_cash_balance(initial_cash)

        logger.info(f"🔄 Paper Trading 초기화 완료: 포지션 {positions_deleted}개, "
                   f"거래 {trades_deleted}건, 스냅샷 {snapshots_deleted}개 삭제, "
                   f"잔고 ${initial_cash:,.2f}로 리셋")

        return {
            'positions_deleted': positions_deleted,
            'trades_deleted': trades_deleted,
            'snapshots_deleted': snapshots_deleted,
            'new_cash_balance': initial_cash
        }