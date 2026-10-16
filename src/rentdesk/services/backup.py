"""백업·복원 (PRD 10.2~10.3, CROSS_PLATFORM F, T-19/T-20).

백업 파일 = ZIP(`manifest.json` + `rentdesk.db`). OS 독립 포맷이라 Mac ↔ Windows 복원이 가능하다.

- 실행 중인 DB를 파일 복사하지 않고 SQLite Online Backup API(`Connection.backup`)로 스냅샷한다.
- ZIP 안에는 절대경로·라이선스 파일·로그를 넣지 않는다. 스냅샷의 `default_backup_dir` 설정도 비운다.
- 복원 전: ZIP 구조, manifest(제품/포맷/스키마 버전), SHA-256, `integrity_check`, 건수·합계 대조.
- 복원: 현재 DB를 보호 백업(같은 ZIP 포맷) → 연결 종료 → 검증된 파일로 교체 → 다시 열기.
  교체 후 열기에 실패하면 원래 파일을 되돌린다. 손상 백업은 검증 단계에서 거부되므로 기존 DB를 건드리지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from rentdesk import LEGACY_PRODUCTS, PRODUCT, __version__
from rentdesk.domain.errors import DomainError
from rentdesk.domain.timeutil import parse_utc_str, to_seoul, to_utc_str
from rentdesk.persistence import repositories as repo
from rentdesk.persistence.db import (
    DatabaseError,
    connect,
    current_schema_version,
    integrity_problems,
    latest_schema_version,
    open_database,
    transaction,
)
from rentdesk.platform.environment import get_environment_info
from rentdesk.services.common import Clock, current_time, default_clock

BACKUP_FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
DB_MEMBER = "rentdesk.db"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_DB_BYTES = 4 * 1024**3
REMINDER_DAYS = 7
LAST_BACKUP_KEY = "last_backup_at_utc"
SUMMARY_TABLES = (
    "categories", "assets", "customers", "rental_orders", "rental_items",
    "maintenance_blocks", "charge_lines", "money_entries",
)

UNENCRYPTED_NOTICE = (
    "백업 파일은 암호화되어 있지 않습니다. 고객 정보가 들어 있으므로 "
    "USB·외장 드라이브 등 다른 사람이 볼 수 없는 안전한 곳에 보관해주세요."
)
_CORRUPT = "백업 파일이 손상되었거나 변경되었습니다. 다른 백업 파일을 선택해주세요."


class BackupError(DomainError):
    """백업 생성/검증/복원 실패. 메시지는 사용자에게 그대로 보여줄 수 있다."""


class BackupExistsError(BackupError):
    """같은 이름의 백업 파일이 이미 있음. 화면은 덮어쓰기 확인 후 overwrite=True로 다시 호출한다."""


@dataclass(frozen=True)
class DataSummary:
    """복원 전후 대조용 건수·핵심 합계(T-19). 금액은 KRW 정수."""

    counts: dict[str, int]
    charge_total_won: int
    money_totals_won: dict[str, int]

    def to_json(self) -> dict:
        return {
            "counts": dict(self.counts),
            "charge_total_won": self.charge_total_won,
            "money_totals_won": dict(self.money_totals_won),
        }

    @classmethod
    def from_json(cls, data: object) -> DataSummary:
        if not isinstance(data, dict):
            raise ValueError("summary")
        counts = data.get("counts")
        money = data.get("money_totals_won")
        total = data.get("charge_total_won")
        if not (isinstance(counts, dict) and isinstance(money, dict) and _is_int(total)):
            raise ValueError("summary")
        if not all(isinstance(k, str) and _is_int(v) for k, v in (*counts.items(), *money.items())):
            raise ValueError("summary")
        return cls(dict(counts), total, dict(money))


@dataclass(frozen=True)
class BackupManifest:
    product: str
    backup_format_version: int
    schema_version: int
    app_version: str
    created_at_utc: str
    sha256: str
    size_bytes: int
    summary: DataSummary
    source_os: str = ""  # 참고용. 포맷 판정에 쓰지 않는다(CROSS_PLATFORM F).
    db_file: str = DB_MEMBER

    def to_json(self) -> str:
        return json.dumps({
            "product": self.product,
            "backup_format_version": self.backup_format_version,
            "schema_version": self.schema_version,
            "app_version": self.app_version,
            "created_at_utc": self.created_at_utc,
            "db_file": self.db_file,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "source_os": self.source_os,
            "summary": self.summary.to_json(),
        }, ensure_ascii=False, indent=2)

    @property
    def created_at(self) -> datetime:
        return parse_utc_str(self.created_at_utc)


@dataclass(frozen=True)
class BackupResult:
    path: Path
    manifest: BackupManifest


@dataclass
class ValidatedBackup:
    """검증을 통과한 백업. `db_path`는 임시 폴더에 풀린 DB 파일(호출자가 `cleanup()`)."""

    source: Path
    manifest: BackupManifest
    db_path: Path
    _workdir: tempfile.TemporaryDirectory | None = field(default=None, repr=False)

    def cleanup(self) -> None:
        if self._workdir is not None:
            self._workdir.cleanup()
            self._workdir = None


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _stamp(now: datetime) -> str:
    return to_seoul(now).strftime("%Y%m%d_%H%M%S")


def suggested_backup_name(now: datetime) -> str:
    return f"RentalDesk_backup_{_stamp(now)}.zip"


def data_summary(conn: sqlite3.Connection) -> DataSummary:
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in SUMMARY_TABLES}
    charge_total = conn.execute("SELECT COALESCE(SUM(amount_won), 0) FROM charge_lines").fetchone()[0]
    money = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT kind, SUM(amount_won) FROM money_entries GROUP BY kind ORDER BY kind"
        )
    }
    return DataSummary(counts, charge_total, money)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot(conn: sqlite3.Connection, target: Path) -> None:
    """Online Backup API로 일관된 사본을 만든다(실행 중 파일 복사 금지, PRD 10.2)."""
    dest = sqlite3.connect(str(target))
    try:
        conn.backup(dest)
        # 기기별 절대경로가 다른 OS로 넘어가지 않도록 스냅샷에서만 비운다.
        dest.execute("UPDATE app_settings SET value_json = 'null' WHERE key = 'default_backup_dir'")
        dest.commit()
    finally:
        dest.close()


def _target_path(dest: Path | str) -> Path:
    target = Path(dest)
    if target.suffix.lower() != ".zip":
        target = target.with_name(target.name + ".zip")
    return target


def create_backup(
    conn: sqlite3.Connection, dest: Path | str, *, now: datetime, overwrite: bool = False
) -> BackupResult:
    """현재 DB를 백업 ZIP으로 저장한다. 같은 폴더 임시 파일에 쓴 뒤 교체한다."""
    target = _target_path(dest)
    if target.exists() and not overwrite:
        raise BackupExistsError(f"같은 이름의 파일이 이미 있습니다: {target.name}")
    if not target.parent.is_dir():
        raise BackupError("저장할 폴더를 찾을 수 없습니다. 다른 위치를 선택해주세요.")

    tmp_zip = None
    try:
        with tempfile.TemporaryDirectory(prefix="rentdesk-backup-") as work:
            snapshot = Path(work) / DB_MEMBER
            _snapshot(conn, snapshot)
            check = connect(snapshot)
            try:
                if integrity_problems(check):
                    raise BackupError(
                        "현재 데이터 파일의 무결성 검사에 실패해 백업을 만들지 않았습니다. "
                        "판매자에게 문의해주세요."
                    )
                schema_version = current_schema_version(check)
                summary = data_summary(check)
            finally:
                check.close()
            manifest = BackupManifest(
                product=PRODUCT,
                backup_format_version=BACKUP_FORMAT_VERSION,
                schema_version=schema_version,
                app_version=__version__,
                created_at_utc=to_utc_str(now),
                sha256=_sha256(snapshot),
                size_bytes=snapshot.stat().st_size,
                summary=summary,
                source_os=get_environment_info().os_name,
            )
            fd, tmp_zip = tempfile.mkstemp(prefix=".rentdesk-", suffix=".zip", dir=str(target.parent))
            os.close(fd)
            with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(MANIFEST_NAME, manifest.to_json())
                zf.write(snapshot, DB_MEMBER)
            os.replace(tmp_zip, target)
            tmp_zip = None
    except PermissionError as exc:
        raise BackupError(
            "백업 파일을 저장할 수 없습니다. 폴더 쓰기 권한이 없거나 같은 파일이 열려 있습니다."
        ) from exc
    except OSError as exc:
        raise BackupError("백업 파일을 저장하지 못했습니다. 저장 위치와 남은 용량을 확인해주세요.") from exc
    finally:
        if tmp_zip is not None and os.path.exists(tmp_zip):
            os.remove(tmp_zip)
    return BackupResult(target, manifest)


def _parse_manifest(raw: bytes) -> BackupManifest:
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("manifest")
        if data.get("product") not in (PRODUCT, *LEGACY_PRODUCTS):
            raise BackupError("RentalDesk 백업 파일이 아닙니다.")
        fmt = data.get("backup_format_version")
        if not _is_int(fmt):
            raise ValueError("format")
        if fmt != BACKUP_FORMAT_VERSION:
            raise BackupError(
                "이 프로그램 버전에서 지원하지 않는 백업 형식입니다. 프로그램을 최신 버전으로 업데이트해주세요."
            )
        schema, size, sha = data.get("schema_version"), data.get("size_bytes"), data.get("sha256")
        created = data.get("created_at_utc")
        if not (_is_int(schema) and schema >= 1 and _is_int(size) and size > 0):
            raise ValueError("numbers")
        if not (isinstance(sha, str) and len(sha) == 64 and isinstance(created, str)):
            raise ValueError("strings")
        if data.get("db_file", DB_MEMBER) != DB_MEMBER:
            raise ValueError("db_file")
        parse_utc_str(created)
        return BackupManifest(
            product=PRODUCT,
            backup_format_version=fmt,
            schema_version=schema,
            app_version=str(data.get("app_version", "")),
            created_at_utc=created,
            sha256=sha.lower(),
            size_bytes=size,
            summary=DataSummary.from_json(data.get("summary")),
            source_os=str(data.get("source_os", "")),
        )
    except BackupError:
        raise
    except (ValueError, UnicodeDecodeError, TypeError) as exc:
        raise BackupError(_CORRUPT) from exc


def inspect_backup(path: Path | str) -> ValidatedBackup:
    """백업 ZIP을 임시 폴더에 풀어 모든 검증을 마친다. 현재 DB는 건드리지 않는다(T-20)."""
    source = Path(path)
    if not source.is_file():
        raise BackupError("백업 파일을 찾을 수 없습니다.")
    workdir = tempfile.TemporaryDirectory(prefix="rentdesk-restore-")
    extracted = Path(workdir.name) / DB_MEMBER
    try:
        try:
            with zipfile.ZipFile(source) as zf:
                infos = {i.filename: i for i in zf.infolist()}
                if set(infos) != {MANIFEST_NAME, DB_MEMBER}:
                    raise BackupError(_CORRUPT)
                if infos[MANIFEST_NAME].file_size > MAX_MANIFEST_BYTES:
                    raise BackupError(_CORRUPT)
                manifest = _parse_manifest(zf.read(MANIFEST_NAME))
                if manifest.schema_version > latest_schema_version():
                    raise BackupError(
                        "더 최신 버전의 프로그램에서 만든 백업입니다. 프로그램을 최신 버전으로 업데이트한 뒤 복원해주세요."
                    )
                db_info = infos[DB_MEMBER]
                if db_info.file_size != manifest.size_bytes or db_info.file_size > MAX_DB_BYTES:
                    raise BackupError(_CORRUPT)
                digest = hashlib.sha256()
                with zf.open(db_info) as src, open(extracted, "wb") as dst:
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        digest.update(chunk)
                        dst.write(chunk)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError) as exc:
            raise BackupError(_CORRUPT) from exc
        except OSError as exc:
            raise BackupError("백업 파일을 읽을 수 없습니다. 파일 위치와 접근 권한을 확인해주세요.") from exc
        if digest.hexdigest() != manifest.sha256:
            raise BackupError(_CORRUPT)
        _verify_database_file(extracted, manifest)
    except BaseException:
        workdir.cleanup()
        raise
    return ValidatedBackup(source, manifest, extracted, workdir)


def _verify_database_file(db_file: Path, manifest: BackupManifest) -> None:
    try:
        check = connect(db_file)
    except (sqlite3.Error, DatabaseError) as exc:
        raise BackupError(_CORRUPT) from exc
    try:
        if integrity_problems(check):
            raise BackupError("백업 파일 안의 데이터가 손상되어 복원할 수 없습니다.")
        try:
            if current_schema_version(check) != manifest.schema_version:
                raise BackupError(_CORRUPT)
            if data_summary(check) != manifest.summary:
                raise BackupError(_CORRUPT)
        except sqlite3.Error as exc:
            raise BackupError(_CORRUPT) from exc
    finally:
        check.close()


_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")


def _move_with_sidecars(src: Path, dst: Path) -> None:
    """DB 파일과 짝을 이루는 저널/WAL 파일을 함께 옮긴다(짝이 어긋나면 다른 DB에 저널이 적용될 수 있음)."""
    os.replace(src, dst)
    for suffix in _SIDECAR_SUFFIXES:
        side = Path(f"{src}{suffix}")
        if side.exists():
            os.replace(side, Path(f"{dst}{suffix}"))


def _remove_with_sidecars(path: Path) -> None:
    path.unlink(missing_ok=True)
    for suffix in _SIDECAR_SUFFIXES:
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def restore_database(
    validated: ValidatedBackup,
    db_path: Path,
    *,
    now: datetime,
    backup_dir: Path,
    keep_current_as: Path | None = None,
) -> sqlite3.Connection:
    """검증된 백업으로 DB 파일을 교체하고 새 연결을 돌려준다. 현재 DB 연결은 호출자가 먼저 닫는다.

    기존 파일은 저널/WAL 파일과 함께 옆으로 옮긴다(비정상 종료 뒤 남은 저널이 새 DB에 적용되지 않도록).
    `keep_current_as`가 있으면 기존 파일을 그 경로로 옮겨 보관한다(손상 DB 복구용).
    교체한 파일을 열지 못하면 기존 파일을 되돌리고 `BackupError`를 던진다.
    """
    stamp = _stamp(now)
    staging = db_path.parent / f".restore-{stamp}.db"
    previous = db_path.parent / f".before-restore-{stamp}.db"
    had_previous = False
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(validated.db_path, staging)
        had_previous = db_path.exists()
        if had_previous:
            _move_with_sidecars(db_path, previous)
        os.replace(staging, db_path)
    except OSError as exc:
        staging.unlink(missing_ok=True)
        if previous.exists() and not db_path.exists():
            _move_with_sidecars(previous, db_path)
        raise BackupError("데이터 파일을 교체하지 못했습니다. 저장 공간과 접근 권한을 확인해주세요.") from exc

    try:
        conn = open_database(db_path, pre_migration_backup=backup_dir / f"pre_migration_{stamp}.db")
    except (sqlite3.Error, DatabaseError, OSError) as exc:
        _remove_with_sidecars(db_path)
        if had_previous:
            _move_with_sidecars(previous, db_path)
        raise BackupError("복원한 데이터를 열지 못해 기존 데이터로 되돌렸습니다.") from exc

    if had_previous:
        if keep_current_as is not None:
            keep_current_as.parent.mkdir(parents=True, exist_ok=True)
            _move_with_sidecars(previous, keep_current_as)
        else:
            _remove_with_sidecars(previous)
    return conn


@dataclass(frozen=True)
class RestoreOutcome:
    conn: sqlite3.Connection
    restored: bool
    protection: Path | None
    error: str | None = None


def restore_with_protection(
    current: sqlite3.Connection,
    validated: ValidatedBackup,
    *,
    db_path: Path,
    backup_dir: Path,
    now: datetime,
    reopen: Callable[[Path], sqlite3.Connection],
) -> RestoreOutcome:
    """복원 전체 흐름(S-09): 보호 백업 → 현재 연결 종료 → 교체 → 새 연결.

    보호 백업에 실패하면 아무것도 바꾸지 않고 BackupError를 던진다(현재 연결 유지).
    교체 단계에서 실패하면 기존 DB를 `reopen`으로 다시 열어 돌려준다(restored=False).
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    protection = create_backup(
        current, backup_dir / f"pre_restore_{_stamp(now)}.zip", now=now, overwrite=True
    ).path
    current.close()
    try:
        conn = restore_database(validated, db_path, now=now, backup_dir=backup_dir)
    except BackupError as exc:
        return RestoreOutcome(reopen(db_path), False, protection, exc.message)
    return RestoreOutcome(conn, True, protection)


class BackupService:
    """화면용: 백업 생성, 마지막 백업 시각, 백업 권장 안내."""

    def __init__(self, conn: sqlite3.Connection, clock: Clock = default_clock) -> None:
        self._conn = conn
        self._clock = clock

    def create(self, dest: Path | str, *, overwrite: bool = False) -> BackupResult:
        now = current_time(self._clock)
        result = create_backup(self._conn, dest, now=now, overwrite=overwrite)
        with transaction(self._conn):
            repo.set_setting(self._conn, LAST_BACKUP_KEY, to_utc_str(now))
            repo.insert_audit(
                self._conn, entity_type="backup", entity_id=None, action="BACKUP_CREATED",
                description=None, now=now,
            )
        return result

    def suggested_name(self) -> str:
        return suggested_backup_name(current_time(self._clock))

    def last_backup_at(self) -> datetime | None:
        try:
            value = repo.get_setting(self._conn, LAST_BACKUP_KEY)
        except KeyError:
            return None
        return parse_utc_str(value) if isinstance(value, str) else None

    def reminder(self) -> str | None:
        """백업 권장 문구(PRD 10.2). 필요 없으면 None."""
        last = self.last_backup_at()
        if last is None:
            return "아직 백업한 적이 없습니다. 설정 화면에서 백업 파일을 만들어 안전한 곳에 보관해주세요."
        if current_time(self._clock) - last >= timedelta(days=REMINDER_DAYS):
            return f"마지막 백업 후 {REMINDER_DAYS}일 이상 지났습니다. 백업을 권장합니다."
        return None
