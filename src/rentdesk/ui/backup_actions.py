"""백업 저장 / 복원 파일 선택·확인 대화상자 (S-09). 검증·교체 로직은 services.backup에만 있다."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QFileDialog, QWidget

from rentdesk.domain.display import format_dt
from rentdesk.platform.preferences import LocalPreferences
from rentdesk.services.backup import (
    UNENCRYPTED_NOTICE,
    BackupExistsError,
    BackupResult,
    BackupService,
    ValidatedBackup,
    inspect_backup,
)
from rentdesk.ui import feedback

FILE_FILTER = "RentalDesk 백업 (*.zip)"


def default_backup_folder(prefs: LocalPreferences | None) -> Path:
    chosen = prefs.backup_dir() if prefs is not None else None
    if chosen is not None:
        return chosen
    docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(docs) if docs else Path.home()


def save_backup_dialog(parent: QWidget | None, service: BackupService,
                       prefs: LocalPreferences | None) -> BackupResult | None:
    path, _ = QFileDialog.getSaveFileName(
        parent, "백업 파일 저장", str(default_backup_folder(prefs) / service.suggested_name()), FILE_FILTER
    )
    if not path:
        return None
    # Qt 저장 대화상자가 이미 덮어쓰기를 확인했다.
    return export_backup(parent, service, Path(path), overwrite=True)


def export_backup(parent: QWidget | None, service: BackupService, path: Path, *,
                  overwrite: bool = False) -> BackupResult | None:
    def on_error(exc) -> bool:
        if isinstance(exc, BackupExistsError) and feedback.confirm(
            parent, f"{exc.message}\n덮어쓸까요?", "덮어쓰기 확인"
        ):
            nonlocal result
            result = export_backup(parent, service, path, overwrite=True)
            return True
        return False

    result: BackupResult | None = None
    created = feedback.run(parent, lambda: service.create(path, overwrite=overwrite), on_domain_error=on_error)
    if created is not None:
        result = created
        counts = created.manifest.summary.counts
        feedback.info(
            parent,
            f"백업 파일을 만들었습니다.\n{created.path}\n\n"
            f"장비 {counts['assets']}대 · 고객 {counts['customers']}명 · 예약 {counts['rental_orders']}건\n\n"
            f"{UNENCRYPTED_NOTICE}",
            "백업 완료",
        )
    return result


def choose_backup_file(parent: QWidget | None, prefs: LocalPreferences | None) -> Path | None:
    path, _ = QFileDialog.getOpenFileName(
        parent, "복원할 백업 파일 선택", str(default_backup_folder(prefs)), FILE_FILTER + ";;모든 파일 (*)"
    )
    return Path(path) if path else None


def confirm_restore(parent: QWidget | None, path: Path, *, protect_current: bool = True) -> ValidatedBackup | None:
    """백업 파일을 검증하고 내용 요약과 함께 복원 여부를 묻는다. 취소하면 None."""
    validated = feedback.run(parent, lambda: inspect_backup(path))
    if validated is None:
        return None
    m = validated.manifest
    counts = m.summary.counts
    current = (
        "현재 데이터는 복원 직전에 보호 백업 파일로 자동 저장된 뒤 교체됩니다."
        if protect_current
        else "손상된 현재 데이터 파일은 삭제하지 않고 백업 폴더로 옮겨 보관합니다."
    )
    text = (
        f"이 백업으로 데이터를 복원할까요?\n\n"
        f"백업 시각: {format_dt(m.created_at)}\n"
        f"프로그램 버전: {m.app_version or '-'} · 만든 OS: {m.source_os or '-'}\n"
        f"장비 {counts.get('assets', 0)}대 · 고객 {counts.get('customers', 0)}명 · "
        f"예약 {counts.get('rental_orders', 0)}건\n\n"
        f"{current}\n복원 후 화면을 다시 엽니다."
    )
    if not feedback.confirm(parent, text, "데이터 복원 확인"):
        validated.cleanup()
        return None
    return validated
