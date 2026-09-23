# live_trading.py - 실제 거래 데이터베이스 관리자

from datetime import datetime
from typing import Dict, List, Optional
import logging
from .db_manager import get_db_manager

logger = logging.getLogger(__name__)


class LiveTradingManager:
    """실제 거래(Live Trading) 데이터베이스 매니저"""

    def __init__(self, account_id: int = 2):
        self.db = get_db_manager()
        self.account_id = account_id

    # ===========================
    # 현금 잔고 관리
    # ===========================

    def get_cash_balance(self) -> float:
        """현재 현금 잔고 조회"""
        query = "SELECT current_cash FROM live_account_balance WHERE account_id = %s"
        result = self.db.fetch_one(query, (self.account_id,))
        return float(result['current_cash']) if result else 0.0

    def update_cash_balance(self, new_cash: float, synced_from_kis: bool = False) -> None:
        """현금 잔고 업데이트

        synced_from_kis : 이 값이 KIS 에서 직접 온 값인지 (봇 매매로 가감한 추정치면 False)
        last_synced_at  : KIS 동기화 시각. 추정치 갱신 때는 건드리지 않는다.
                          (NULL 이면 아직 한 번도 동기화한 적 없음 → is_first_sync)
        """
        if synced_from_kis:
            query = """
            UPDATE live_account_balance
            SET current_cash = %s, synced_from_kis = TRUE, last_synced_at = %s
            WHERE account_id = %s
            """
            params = (new_cash, datetime.now(), self.account_id)
        else:
            query = """
            UPDATE live_account_balance
            SET current_cash = %s, synced_from_kis = FALSE
            WHERE account_id = %s
            """
            params = (new_cash, self.account_id)
        self.db.execute_query(query, params)
        logger.info(f"💰 Live Trading 현금 잔고 업데이트: ${new_cash:,.2f} (KIS 동기화: {synced_from_kis})")

    def adjust_cash(self, amount: float) -> float:
        """현금 증감 (매수시 -, 매도시 +)"""
        current = self.get_cash_balance()
        new_cash = current + amount
        self.update_cash_balance(new_cash, synced_from_kis=False)
        return new_cash

    def sync_balance_from_kis(self, kis_balance: float) -> None:
        """KIS API에서 잔고 동기화"""
        self.update_cash_balance(kis_balance, synced_from_kis=True)

    # ===========================
    # 거래 내역 기록
    # ===========================

    def record_buy(self, ticker: str, shares: int, price: float,
                   order_id: str = None, commission: float = 0.0,
                   notes: str = None) -> int:
        """매수 기록"""
        # numpy 타입을 Python 네이티브 타입으로 변환
        shares = int(shares)
        price = float(price)
        commission = float(commission)
        total_value = shares * price
        query = """
        INSERT INTO live_trades (account_id, date, ticker, action, shares, price,
                                total_value, commission, order_id, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """
        result = self.db.fetch_one(query, (
            self.account_id, datetime.now(), ticker, 'BUY', shares, price,
            total_value, commission, order_id, notes
        ))

        # 현금 차감
        self.adjust_cash(-(total_value + commission))

        logger.info(f"🛒 Live Trading 매수 기록: {ticker} {shares}주 @ ${price:.2f} (Order: {order_id})")
        return result['id'] if result else None

    def record_sell(self, ticker: str, shares: int, price: float,
                    buy_price: float, buy_date: datetime,
                    order_id: str = None, commission: float = 0.0,
                    exit_reason: str = 'SIGNAL', notes: str = None) -> int:
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
        INSERT INTO live_trades (account_id, date, ticker, action, shares, price,
                                total_value, commission, pnl, pnl_percent,
                                days_held, exit_reason, order_id, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """
        result = self.db.fetch_one(query, (
            self.account_id, datetime.now(), ticker, 'SELL', shares, price,
            total_value, commission, pnl, pnl_percent, days_held, exit_reason,
            order_id, notes
        ))

        # 현금 증가
        self.adjust_cash(total_value - commission)

        logger.info(f"💰 Live Trading 매도 기록: {ticker} {shares}주 @ ${price:.2f} (PnL: {pnl_percent:+.2f}%, Order: {order_id})")
        return result['id'] if result else None

    # ===========================
    # 포지션 관리
    # ===========================

    def get_positions(self) -> List[Dict]:
        """현재 보유 포지션 조회"""
        query = "SELECT * FROM live_positions WHERE account_id = %s"
        return self.db.fetch_all(query, (self.account_id,))

    def get_position(self, ticker: str) -> Optional[Dict]:
        """특정 종목 포지션 조회"""
        query = "SELECT * FROM live_positions WHERE account_id = %s AND ticker = %s"
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
        INSERT INTO live_positions (account_id, ticker, shares, avg_price,
                                    buy_date, highest_price, current_price, current_value)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (account_id, ticker) DO UPDATE
        SET shares = live_positions.shares + EXCLUDED.shares,
            avg_price = ((live_positions.avg_price * live_positions.shares) +
                        (EXCLUDED.avg_price * EXCLUDED.shares)) /
                        (live_positions.shares + EXCLUDED.shares),
            current_value = (live_positions.shares + EXCLUDED.shares) *
                           ((live_positions.avg_price * live_positions.shares +
                             EXCLUDED.avg_price * EXCLUDED.shares) /
                            (live_positions.shares + EXCLUDED.shares))
        """
        self.db.execute_query(query, (
            self.account_id, ticker, shares, avg_price, buy_date, avg_price, avg_price, current_value
        ))
        logger.info(f"📊 Live Trading 포지션 추가: {ticker} {shares}주")

    def remove_position(self, ticker: str) -> None:
        """포지션 제거"""
        query = "DELETE FROM live_positions WHERE account_id = %s AND ticker = %s"
        self.db.execute_query(query, (self.account_id, ticker))
        logger.info(f"📊 Live Trading 포지션 제거: {ticker}")

    def update_position_price(self, ticker: str, current_price: float) -> None:
        """포지션 현재 가격 업데이트"""
        query = """
        UPDATE live_positions
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

    def sync_positions_from_kis(self, kis_positions: List[Dict]) -> Dict:
        """KIS API 보유 종목을 DB 에 병합한다.

        원칙: 수량/평단/현재가는 KIS 가 진실, buy_date/highest_price 는 DB 가 진실.
        - KIS 에만 있는 종목  → 신규 추가 (buy_date = 지금, highest = max(평단, 현재가))
        - 양쪽에 있는 종목    → 수량/평단/현재가만 갱신, buy_date/highest 유지
        - DB 에만 있는 종목   → 삭제 (실계좌에 없으므로)
        """
        existing = {p['ticker']: p for p in self.get_positions()}
        kis_map = {p['ticker']: p for p in kis_positions if p.get('shares', 0) > 0}

        added, updated, removed = [], [], []

        for ticker, pos in kis_map.items():
            shares = int(pos['shares'])
            avg_price = float(pos['avg_price'])
            current_price = float(pos.get('current_price') or avg_price) or avg_price

            if ticker in existing:
                query = """
                UPDATE live_positions
                SET shares = %s, avg_price = %s, current_price = %s,
                    current_value = %s,
                    highest_price = GREATEST(COALESCE(highest_price, 0), %s)
                WHERE account_id = %s AND ticker = %s
                """
                self.db.execute_query(query, (
                    shares, avg_price, current_price, shares * current_price,
                    current_price, self.account_id, ticker
                ))
                updated.append(ticker)
            else:
                query = """
                INSERT INTO live_positions (account_id, ticker, shares, avg_price,
                                            buy_date, highest_price, current_price, current_value)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """
                self.db.execute_query(query, (
                    self.account_id, ticker, shares, avg_price, datetime.now(),
                    max(avg_price, current_price), current_price, shares * current_price
                ))
                added.append(ticker)

            # 손절/트레일링 가격 등 파생 칼럼 갱신
            self.update_position_price(ticker, current_price)

        for ticker in existing:
            if ticker not in kis_map:
                self.remove_position(ticker)
                removed.append(ticker)

        logger.info(f"🔄 KIS 포지션 동기화: 추가 {added}, 갱신 {len(updated)}개, 삭제 {removed}")
        return {'added': added, 'updated': updated, 'removed': removed}

    # ===========================
    # KIS 계좌 동기화 (현금 + 포지션 + 입출금 감지)
    # ===========================

    def is_first_sync(self) -> bool:
        """아직 한 번도 KIS 와 동기화한 적이 없는지 (초기화 직후 상태)"""
        row = self.db.fetch_one(
            "SELECT last_synced_at FROM live_account_balance WHERE account_id = %s",
            (self.account_id,)
        )
        return not (row and row['last_synced_at'])

    def record_cash_flow(self, amount: float, kis_cash: float, db_cash: float,
                         note: str = None) -> None:
        """입출금(설명되지 않는 현금 변동) 이력 기록"""
        query = """
        INSERT INTO live_cash_flows (account_id, flow_date, amount, kis_cash, db_cash, note)
        VALUES (%s, %s, %s, %s, %s, %s)
        """
        self.db.execute_query(query, (
            self.account_id, datetime.now(), float(amount), float(kis_cash), float(db_cash), note
        ))

    def sync_from_kis(self, kis_cash: float, kis_positions: Optional[List[Dict]],
                      cash_flow_threshold: float = 100.0) -> Dict:
        """봇 시작/리밸런싱 시 KIS 실계좌 상태를 DB 에 반영한다.

        입출금 자동 반영 원리:
          DB 의 current_cash 는 봇이 기록한 매매로만 증감한다. 따라서
          diff = KIS 현금 - DB 현금 은 "봇이 모르는 현금 변동" 이다.
          |diff| <= threshold → 수수료/환율/배당 등 드리프트. 잔고만 덮어쓴다.
          |diff| >  threshold → 입출금으로 간주. initial_capital 에 더해 수익률 기준을
                                 옮기고 live_cash_flows 에 이력을 남긴다.

        첫 동기화(초기화 직후)에는 diff 비교 없이
          initial_capital = KIS 현금 + 보유 평가액
        으로 기준을 잡는다.

        Returns:
            {'first_sync', 'kis_cash', 'db_cash_before', 'cash_flow',
             'initial_capital', 'positions': {...}}
        """
        kis_cash = float(kis_cash)
        first_sync = self.is_first_sync()
        db_cash_before = self.get_cash_balance()

        positions_result = None
        if kis_positions is not None:
            positions_result = self.sync_positions_from_kis(kis_positions)

        account = self.db.get_account_info(self.account_id) or {}
        initial_capital = float(account.get('initial_capital') or 0)
        cash_flow = 0.0

        if first_sync:
            holdings_value = sum(
                float(p['shares']) * float(p.get('current_price') or p['avg_price'])
                for p in (kis_positions or [])
            )
            initial_capital = kis_cash + holdings_value
            self.db.set_initial_capital(self.account_id, initial_capital)
            logger.info(f"🏁 Live 첫 동기화: initial_capital = ${initial_capital:,.2f} "
                        f"(현금 ${kis_cash:,.2f} + 보유 ${holdings_value:,.2f})")
        else:
            diff = kis_cash - db_cash_before
            if abs(diff) > cash_flow_threshold:
                cash_flow = diff
                initial_capital += diff
                self.db.set_initial_capital(self.account_id, initial_capital)
                self.record_cash_flow(
                    diff, kis_cash, db_cash_before,
                    note=f"auto-detected (threshold ${cash_flow_threshold:,.0f})"
                )
                logger.warning(f"💸 입출금 감지: {diff:+,.2f} → initial_capital ${initial_capital:,.2f}")
            elif abs(diff) > 0.005:
                logger.info(f"🔧 현금 드리프트 보정: {diff:+,.2f} (허용 오차 이내)")

        self.sync_balance_from_kis(kis_cash)

        return {
            'first_sync': first_sync,
            'kis_cash': kis_cash,
            'db_cash_before': db_cash_before,
            'cash_flow': cash_flow,
            'initial_capital': initial_capital,
            'positions': positions_result,
        }

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
            SELECT total_value FROM live_portfolio_snapshots
            WHERE account_id = %s
            ORDER BY snapshot_date DESC
            LIMIT 1
        """, (self.account_id,))

        daily_return = 0.0
        if prev_snapshot:
            prev_value = float(prev_snapshot['total_value'])
            daily_return = ((total_value - prev_value) / prev_value) * 100

        query = """
        INSERT INTO live_portfolio_snapshots
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
        logger.info(f"📸 Live Trading 스냅샷 저장: ${total_value:,.2f}")

    # ===========================
    # 통계 조회
    # ===========================

    def get_portfolio_status(self) -> Optional[Dict]:
        """포트폴리오 현재 상태 조회"""
        query = "SELECT * FROM v_live_portfolio_status WHERE account_id = %s"
        return self.db.fetch_one(query, (self.account_id,))

    def get_trade_statistics(self) -> Optional[Dict]:
        """거래 통계 조회"""
        query = "SELECT * FROM v_live_trade_statistics WHERE account_id = %s"
        return self.db.fetch_one(query, (self.account_id,))

    def get_recent_trades(self, limit: int = 10) -> List[Dict]:
        """최근 거래 내역 조회"""
        query = """
        SELECT * FROM live_trades
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