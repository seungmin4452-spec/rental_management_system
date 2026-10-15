"""SQLite 연결·트랜잭션·마이그레이션 헬퍼 (PRD 8.1, 10.3).

- 모든 연결은 `connect()`로만 만들고 `PRAGMA foreign_keys = ON`을 강제한다.
- 쓰기 트랜잭션은 `transaction()`(`BEGIN IMMEDIATE`)으로 감싼다. 예약 확정처럼
  "검사 후 저장"이 필요한 작업은 이 트랜잭션 안에서 재검사해야 동시 저장에도 안전하다.
- 연결은 `isolation_level=None`(autocommit)으로 열어 Python sqlite3 모듈의 암묵적
  BEGIN/COMMIT을 끄고 트랜잭션 경계를 명시적으로만 관리한다.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from rentdesk.domain.timeutil import now_utc, to_utc_str

BUSY_TIMEOUT_MS = 5_000
_MIGRATION_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


class DatabaseError(RuntimeError):
    """DB 열기/마이그레이션 실패. 사용자에게 보여줄 한국어 메시지를 담는다."""


class DatabaseIntegrityError(DatabaseError):
    """데이터 파일 손상(무결성 검사 실패). 앱은 백업 복원 안내를 보여준다(PRD 10.3, T-25)."""


INTEGRITY_MESSAGE = (
    "데이터 파일이 손상되어 열 수 없습니다. 기존 파일은 삭제하지 않았습니다.\n"
    "백업 파일에서 복원하거나 판매자에게 문의해주세요."
)


def integrity_problems(conn: sqlite3.Connection) -> list[str]:
    """`PRAGMA integrity_check` + `foreign_key_check` 결과. 문제가 없으면 빈 목록.

    SQLite 파일이 아닌 경우처럼 검사 자체가 실패해도 문제로 보고한다.
    """
    try:
        rows = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
        problems = [] if rows == ["ok"] else rows
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            problems.append("foreign key violation")
    except sqlite3.DatabaseError as exc:
        return [f"sqlite error: {exc.__class__.__name__}"]
    return problems


def connect(path: Path | str) -> sqlite3.Connection:
    """FK가 활성화된 SQLite 연결. `":memory:"`도 허용(테스트용)."""
    conn = sqlite3.connect(
        str(path),
        isolation_level=None,
        timeout=BUSY_TIMEOUT_MS / 1000,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        conn.close()
        raise DatabaseError("데이터베이스 관계 제약(외래키)을 활성화할 수 없습니다.")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """`BEGIN IMMEDIATE` 쓰기 트랜잭션. 예외 시 전체 롤백 후 예외를 다시 던진다.

    중첩 호출은 허용하지 않는다(서비스 내부에서 트랜잭션 경계를 한 곳에 둔다).
    """
    if conn.in_transaction:
        raise RuntimeError("이미 진행 중인 트랜잭션 안에서 transaction()을 다시 열 수 없습니다.")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def load_migrations() -> list[Migration]:
    """패키지 리소스에서 `NNN_name.sql`을 버전 순으로 읽는다(PyInstaller 번들 겸용)."""
    package = resources.files("rentdesk.persistence.migrations")
    migrations = []
    for entry in package.iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            migrations.append(
                Migration(int(match.group(1)), entry.name, entry.read_text(encoding="utf-8"))
            )
    migrations.sort(key=lambda m: m.version)
    versions = [m.version for m in migrations]
    if len(set(versions)) != len(versions):
        raise DatabaseError("마이그레이션 버전이 중복되었습니다.")
    return migrations


def current_schema_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " applied_at_utc TEXT NOT NULL)"
    )
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return row[0] or 0


def latest_schema_version() -> int:
    migrations = load_migrations()
    return migrations[-1].version if migrations else 0


def apply_migrations(
    conn: sqlite3.Connection,
    *,
    pre_migration_backup: Path | None = None,
) -> list[int]:
    """미적용 마이그레이션을 버전마다 하나의 트랜잭션으로 적용하고 적용된 버전을 반환.

    기존 데이터가 있는 DB(버전 > 0)에 새 마이그레이션을 적용할 때
    `pre_migration_backup`이 주어지면 SQLite Online Backup API로 먼저 백업한다.
    """
    current = current_schema_version(conn)
    latest = latest_schema_version()
    if current > latest:
        raise DatabaseError(
            "이 데이터 파일은 더 최신 버전의 프로그램에서 만들어졌습니다. "
            "프로그램을 최신 버전으로 업데이트해주세요."
        )
    pending = [m for m in load_migrations() if m.version > current]
    if not pending:
        return []

    if current > 0 and pre_migration_backup is not None:
        pre_migration_backup.parent.mkdir(parents=True, exist_ok=True)
        backup_conn = sqlite3.connect(str(pre_migration_backup))
        try:
            conn.backup(backup_conn)
        finally:
            backup_conn.close()

    applied = []
    for migration in pending:
        # executescript는 여러 문장을 실행할 수 있는 유일한 API이므로
        # BEGIN/COMMIT을 스크립트 안에 넣어 마이그레이션+기록을 원자적으로 처리한다.
        script = (
            "BEGIN IMMEDIATE;\n"
            f"{migration.sql}\n;\n"
            f"INSERT INTO schema_migrations (version, applied_at_utc) "
            f"VALUES ({migration.version}, '{to_utc_str(now_utc())}');\n"
            "COMMIT;"
        )
        try:
            conn.executescript(script)
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise DatabaseError(
                f"데이터베이스 구조 업데이트({migration.name})에 실패했습니다. "
                "기존 데이터는 변경되지 않았습니다."
            ) from exc
        applied.append(migration.version)
    # executescript 이후에도 FK 설정이 유지되는지 확인
    conn.execute("PRAGMA foreign_keys = ON")
    return applied


def open_database(path: Path | str, *, pre_migration_backup: Path | None = None) -> sqlite3.Connection:
    """연결 + 무결성 검사 + 마이그레이션.

    비정상 종료 뒤 재실행(T-25)에도 같은 경로를 탄다. SQLite가 남은 롤백 저널을 먼저 처리한 뒤
    `integrity_check`로 파일 상태를 확인하고, 손상된 파일에는 마이그레이션을 적용하지 않는다.
    """
    conn = connect(path)
    try:
        if integrity_problems(conn):
            raise DatabaseIntegrityError(INTEGRITY_MESSAGE)
        apply_migrations(conn, pre_migration_backup=pre_migration_backup)
        if integrity_problems(conn):
            raise DatabaseIntegrityError(INTEGRITY_MESSAGE)
    except BaseException:
        conn.close()
        raise
    return conn
