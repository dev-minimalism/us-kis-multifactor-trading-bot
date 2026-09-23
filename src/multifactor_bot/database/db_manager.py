# db_manager.py - PostgreSQL 연결 관리자

import psycopg2
from psycopg2 import pool, extras
from contextlib import contextmanager
from typing import Optional, Dict, List, Any, Tuple
import logging
import atexit
from multifactor_bot import config

# SSH Tunnel 지원
try:
    from sshtunnel import SSHTunnelForwarder
    SSH_TUNNEL_AVAILABLE = True
except ImportError:
    SSH_TUNNEL_AVAILABLE = False

logger = logging.getLogger(__name__)

# 전역 SSH 터널 인스턴스 (프로세스 종료시 정리)
_ssh_tunnel: Optional['SSHTunnelForwarder'] = None


def _cleanup_tunnel():
    """프로세스 종료시 SSH 터널 정리"""
    global _ssh_tunnel
    if _ssh_tunnel and _ssh_tunnel.is_active:
        _ssh_tunnel.stop()
        logger.info("🔌 SSH 터널 종료됨")


atexit.register(_cleanup_tunnel)


class DatabaseManager:
    """PostgreSQL 데이터베이스 연결 및 쿼리 관리자"""

    def __init__(self):
        global _ssh_tunnel
        self.connection_pool: Optional[pool.SimpleConnectionPool] = None
        self.enabled = config.DB_ENABLED
        self.ssh_tunnel = None
        self.db_host = config.DB_HOST
        self.db_port = config.DB_PORT

        if self.enabled:
            # SSH 터널 설정이 있으면 먼저 터널 생성
            if config.SSH_TUNNEL_ENABLED:
                self._setup_ssh_tunnel()
            self._init_connection_pool()

    def _setup_ssh_tunnel(self):
        """SSH 터널 설정"""
        global _ssh_tunnel

        if not SSH_TUNNEL_AVAILABLE:
            logger.warning("⚠️ sshtunnel 패키지 미설치. pip install sshtunnel")
            return

        if not config.SSH_HOST or not config.SSH_USER:
            logger.warning("⚠️ SSH 설정 불완전 (SSH_HOST, SSH_USER 필요)")
            return

        # 이미 활성화된 터널이 있으면 재사용
        if _ssh_tunnel and _ssh_tunnel.is_active:
            self.ssh_tunnel = _ssh_tunnel
            self.db_host = '127.0.0.1'
            self.db_port = _ssh_tunnel.local_bind_port
            logger.info(f"♻️ 기존 SSH 터널 재사용: localhost:{self.db_port}")
            return

        try:
            # SSH 키 또는 비밀번호 인증
            ssh_kwargs = {
                'ssh_address_or_host': (config.SSH_HOST, config.SSH_PORT),
                'ssh_username': config.SSH_USER,
                'remote_bind_address': (config.SSH_REMOTE_HOST, config.SSH_REMOTE_PORT),
                'local_bind_address': ('127.0.0.1', 0),  # 0 = 자동 포트 할당
            }

            if config.SSH_KEY_PATH:
                ssh_kwargs['ssh_pkey'] = config.SSH_KEY_PATH

            _ssh_tunnel = SSHTunnelForwarder(**ssh_kwargs)
            _ssh_tunnel.start()

            self.ssh_tunnel = _ssh_tunnel
            self.db_host = '127.0.0.1'
            self.db_port = _ssh_tunnel.local_bind_port

            logger.info(f"🔗 SSH 터널 연결됨: {config.SSH_HOST} -> localhost:{self.db_port}")
            print(f"🔗 SSH 터널 연결됨: {config.SSH_HOST} -> localhost:{self.db_port}")

        except Exception as e:
            logger.error(f"❌ SSH 터널 생성 실패: {e}")
            print(f"❌ SSH 터널 생성 실패: {e}")
            raise

    def _init_connection_pool(self):
        """커넥션 풀 초기화"""
        try:
            self.connection_pool = psycopg2.pool.SimpleConnectionPool(
                1,  # minconn
                config.DB_POOL_SIZE + config.DB_MAX_OVERFLOW,  # maxconn
                host=self.db_host,
                port=self.db_port,
                database=config.DB_NAME,
                user=config.DB_USER,
                password=config.DB_PASSWORD
            )
            logger.info(f"✅ DB 연결 풀 초기화 성공: {self.db_host}:{self.db_port}/{config.DB_NAME}")
            print(f"✅ DB 연결 풀 초기화 성공: {self.db_host}:{self.db_port}/{config.DB_NAME}")
        except Exception as e:
            logger.error(f"❌ DB 연결 풀 초기화 실패: {e}")
            print(f"❌ DB 연결 풀 초기화 실패: {e}")
            self.enabled = False
            raise

    def _is_connection_alive(self, conn) -> bool:
        """연결이 유효한지 확인 (health check)"""
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        except Exception:
            return False

    def _reconnect_if_needed(self, conn):
        """연결이 끊어진 경우 재연결 시도"""
        if not self._is_connection_alive(conn):
            logger.warning("⚠️ DB 연결 끊김 감지, 재연결 시도...")
            # 끊어진 연결은 풀에서 제거하고 새 연결 생성
            try:
                self.connection_pool.putconn(conn, close=True)
            except Exception:
                pass  # 이미 닫힌 연결은 무시

            # SSH 터널 확인 및 재연결
            if config.SSH_TUNNEL_ENABLED:
                global _ssh_tunnel
                if _ssh_tunnel and not _ssh_tunnel.is_active:
                    logger.warning("⚠️ SSH 터널 끊김, 재연결 시도...")
                    self._setup_ssh_tunnel()

            # 새 연결 가져오기
            conn = self.connection_pool.getconn()
            if not self._is_connection_alive(conn):
                # 풀 재초기화 필요
                logger.error("❌ 새 연결도 실패, 연결 풀 재초기화...")
                self._init_connection_pool()
                conn = self.connection_pool.getconn()
            logger.info("✅ DB 재연결 성공")
        return conn

    @contextmanager
    def get_connection(self):
        """컨텍스트 매니저로 DB 커넥션 가져오기"""
        if not self.enabled or not self.connection_pool:
            raise RuntimeError("Database is not enabled or connection pool not initialized")

        conn = None
        try:
            conn = self.connection_pool.getconn()
            conn = self._reconnect_if_needed(conn)  # health check 및 재연결
            yield conn
        finally:
            if conn:
                self.connection_pool.putconn(conn)

    @contextmanager
    def get_cursor(self, cursor_factory=None):
        """컨텍스트 매니저로 DB 커서 가져오기"""
        with self.get_connection() as conn:
            cursor = conn.cursor(cursor_factory=cursor_factory)
            try:
                yield cursor
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.error(f"DB 쿼리 오류: {e}")
                raise
            finally:
                cursor.close()

    def execute_query(self, query: str, params: Tuple = None) -> None:
        """쿼리 실행 (INSERT, UPDATE, DELETE)"""
        if not self.enabled:
            logger.warning("DB가 비활성화되어 있습니다")
            return

        with self.get_cursor() as cursor:
            cursor.execute(query, params)

    def fetch_one(self, query: str, params: Tuple = None) -> Optional[Dict]:
        """단일 결과 조회"""
        if not self.enabled:
            return None

        with self.get_cursor(cursor_factory=extras.RealDictCursor) as cursor:
            cursor.execute(query, params)
            return cursor.fetchone()

    def fetch_all(self, query: str, params: Tuple = None) -> List[Dict]:
        """여러 결과 조회"""
        if not self.enabled:
            return []

        with self.get_cursor(cursor_factory=extras.RealDictCursor) as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()

    def execute_many(self, query: str, params_list: List[Tuple]) -> None:
        """여러 쿼리 실행 (batch insert)"""
        if not self.enabled:
            return

        with self.get_cursor() as cursor:
            cursor.executemany(query, params_list)

    def close(self):
        """커넥션 풀 닫기"""
        if self.connection_pool:
            self.connection_pool.closeall()
            logger.info("DB 연결 풀 종료")

    # 헬퍼 메서드들

    def get_account_info(self, account_id: int) -> Optional[Dict]:
        """계좌 정보 조회"""
        query = "SELECT * FROM accounts WHERE id = %s"
        return self.fetch_one(query, (account_id,))

    def get_account_by_mode(self, mode: str) -> Optional[Dict]:
        """모드별 계좌 조회 (PAPER or LIVE)"""
        query = "SELECT * FROM accounts WHERE mode = %s AND is_active = TRUE LIMIT 1"
        return self.fetch_one(query, (mode,))

    def create_account(self, name: str, mode: str, initial_capital: float) -> int:
        """새 계좌 생성"""
        query = """
        INSERT INTO accounts (name, mode, initial_capital)
        VALUES (%s, %s, %s)
        RETURNING id
        """
        result = self.fetch_one(query, (name, mode, initial_capital))
        return result['id'] if result else None

    def update_account_status(self, account_id: int, is_active: bool) -> None:
        """계좌 활성화/비활성화"""
        query = "UPDATE accounts SET is_active = %s WHERE id = %s"
        self.execute_query(query, (is_active, account_id))

    def set_initial_capital(self, account_id: int, initial_capital: float) -> None:
        """계좌 초기 자본금 설정 (Live 첫 동기화 / 입출금 반영용)"""
        query = "UPDATE accounts SET initial_capital = %s WHERE id = %s"
        self.execute_query(query, (float(initial_capital), account_id))


# 싱글톤 인스턴스
_db_manager: Optional[DatabaseManager] = None


def get_db_manager() -> DatabaseManager:
    """DatabaseManager 싱글톤 인스턴스 가져오기"""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager