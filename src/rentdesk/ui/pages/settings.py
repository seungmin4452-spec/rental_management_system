"""S-09 설정: 운영 정책, 사업자 정보, 백업/복원, 라이선스, 화면 크기, 예제 데이터 모드."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from rentdesk.domain.display import format_dt
from rentdesk.platform import paths as platform_paths
from rentdesk.services import auto_backup
from rentdesk.services.auto_backup import AutoBackupSettings
from rentdesk.services.backup import UNENCRYPTED_NOTICE, BackupResult, ValidatedBackup
from rentdesk.services.settings import MAX_MINUTES
from rentdesk.ui import display_scale, feedback, theme
from rentdesk.ui.backup_actions import (
    choose_backup_file,
    confirm_restore,
    default_backup_folder,
    export_backup,
    save_backup_dialog,
)
from rentdesk.ui.dialogs.about_dialog import open_folder
from rentdesk.ui.pages.base import Page
from rentdesk.ui.widgets import FormErrors, button_row

FIELD_WIDTH = 220


def _auto_backup_dir() -> Path:
    folder = platform_paths.get_default_backup_dir() / "auto"
    folder.mkdir(parents=True, exist_ok=True)
    return folder  # 숫자·짧은 입력칸이 화면 폭 전체로 늘어나지 않게


class SettingsPage(Page):
    title = "설정"
    demo_toggle_requested = Signal()
    restore_requested = Signal(object)  # ValidatedBackup — 앱 컨트롤러가 연결 교체를 처리
    backup_created = Signal()

    def __init__(self, ctx, nav, parent: QWidget | None = None) -> None:
        super().__init__(ctx, nav, parent)
        policy_box = QGroupBox("요금·일정 정책 (이후 확정되는 예약부터 적용)")
        pf = QFormLayout(policy_box)
        self.policy_errors = FormErrors(pf)
        self.grace = QSpinBox()
        self.grace.setRange(0, MAX_MINUTES)
        self.grace.setSuffix("분")
        self.multiplier = QDoubleSpinBox()
        self.multiplier.setRange(0, 100)
        self.multiplier.setDecimals(2)
        self.multiplier.setSingleStep(0.5)
        self.multiplier.setSuffix("배")
        self.buffer = QSpinBox()
        self.buffer.setRange(0, MAX_MINUTES)
        self.buffer.setSuffix("분")
        for spin in (self.grace, self.multiplier, self.buffer):
            spin.setMaximumWidth(FIELD_WIDTH)
        self.policy_errors.add_row("연체료 없이 기다려 주는 시간", self.grace, "grace_minutes")
        self.policy_errors.add_row("연체 1일당 일 대여료의", self.multiplier, "late_multiplier")
        self.policy_errors.add_row("반납 후 다음 대여까지 준비 시간", self.buffer, "turnaround_buffer_minutes")
        save_policy = theme.set_variant(QPushButton("정책 저장"), "primary")
        save_policy.clicked.connect(self.save_policy)
        pf.addRow(button_row(save_policy))

        biz_box = QGroupBox("사업자 정보 (엑셀 출력물 헤더)")
        bf = QFormLayout(biz_box)
        self.biz_errors = FormErrors(bf)
        self.biz_name = QLineEdit()
        self.biz_name.setMaxLength(100)
        self.biz_phone = QLineEdit()
        self.biz_phone.setMaxLength(30)
        self.biz_name.setMaximumWidth(FIELD_WIDTH * 2)
        self.biz_phone.setMaximumWidth(FIELD_WIDTH)
        self.biz_errors.add_row("상호", self.biz_name, "business_name")
        self.biz_errors.add_row("전화번호", self.biz_phone, "business_phone")
        save_biz = theme.set_variant(QPushButton("사업자 정보 저장"), "primary")
        save_biz.clicked.connect(self.save_business)
        bf.addRow(button_row(save_biz))

        backup_box = QGroupBox("데이터 백업·복원")
        bl = QVBoxLayout(backup_box)
        notice = QLabel(
            f"{UNENCRYPTED_NOTICE} 다른 PC나 운영체제(Mac ↔ Windows)로 옮길 때도 이 백업 파일을 복원합니다. "
            "라이선스 파일은 백업에 포함되지 않습니다."
        )
        notice.setWordWrap(True)
        theme.set_tone(notice, "muted")
        self.last_backup_label = QLabel()
        self.backup_dir_label = QLabel()
        self.backup_dir_label.setWordWrap(True)
        bform = QFormLayout()
        bform.addRow("마지막 백업", self.last_backup_label)
        bform.addRow("기본 백업 폴더", self.backup_dir_label)
        self.backup_dir_btn = QPushButton("기본 폴더 변경…")
        self.backup_dir_btn.clicked.connect(self.choose_backup_dir)
        self.backup_btn = theme.set_variant(QPushButton("백업 파일 만들기…"), "primary")
        self.backup_btn.clicked.connect(self.backup_dialog)
        self.restore_btn = theme.set_variant(QPushButton("백업에서 복원…"), "danger")
        self.restore_btn.clicked.connect(self.restore_dialog)
        bl.addWidget(notice)
        bl.addLayout(bform)
        bl.addLayout(button_row(self.backup_dir_btn, self.backup_btn, self.restore_btn))
        self.auto_enabled = QCheckBox(
            f"프로그램을 끌 때 자동 백업 (하루 1개, 최근 {auto_backup.KEEP}개 보관)"
        )
        self.auto_enabled.toggled.connect(self._set_auto_enabled)
        self.auto_copy = QCheckBox("자동 백업을 기본 백업 폴더(USB 등)에도 복사")
        self.auto_copy.toggled.connect(self._set_auto_copy)
        self.auto_note = QLabel()
        self.auto_note.setWordWrap(True)
        theme.set_tone(self.auto_note, "muted")
        self.auto_open_btn = QPushButton("자동 백업 폴더 열기")
        self.auto_open_btn.clicked.connect(
            lambda: open_folder(self, _auto_backup_dir)
        )
        bl.addWidget(self.auto_enabled)
        bl.addWidget(self.auto_copy)
        bl.addWidget(self.auto_note)
        bl.addLayout(button_row(self.auto_open_btn))

        self.license_panel = None
        license_box = None
        if self.ctx.license is not None:
            from rentdesk.ui.dialogs.license_dialog import LicensePanel

            license_box = QGroupBox("라이선스")
            ll = QVBoxLayout(license_box)
            self.license_panel = LicensePanel(self.ctx.license, license_box)
            ll.addWidget(self.license_panel)

        scale_box = QGroupBox("화면 크기")
        sl = QFormLayout(scale_box)
        self.scale = QComboBox()
        self.scale.setMaximumWidth(FIELD_WIDTH)
        for choice in (display_scale.AUTO, *display_scale.MANUAL_CHOICES):
            self.scale.addItem(display_scale.choice_label(choice), choice)
        self.scale_note = QLabel()
        self.scale_note.setWordWrap(True)
        theme.set_tone(self.scale_note, "muted")
        save_scale = QPushButton("화면 크기 저장")
        save_scale.clicked.connect(self.save_scale)
        sl.addRow("배율", self.scale)
        sl.addRow(self.scale_note)
        sl.addRow(button_row(save_scale))

        demo_box = QGroupBox("예제 데이터 모드")
        dl = QVBoxLayout(demo_box)
        self.demo_label = QLabel()
        self.demo_label.setWordWrap(True)
        self.demo_btn = QPushButton()
        self.demo_btn.clicked.connect(self.demo_toggle_requested.emit)
        dl.addWidget(self.demo_label)
        dl.addLayout(button_row(self.demo_btn))

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.addWidget(policy_box)
        lay.addWidget(biz_box)
        lay.addWidget(backup_box)
        if license_box is not None:
            lay.addWidget(license_box)
        lay.addWidget(scale_box)
        lay.addWidget(demo_box)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.refresh()

    def refresh(self) -> None:
        p = self.ctx.settings.get_policy()
        self.grace.setValue(p.grace_minutes)
        self.multiplier.setValue(float(p.late_multiplier))
        self.buffer.setValue(p.turnaround_buffer_minutes)
        b = self.ctx.settings.get_business_info()
        self.biz_name.setText(b.name)
        self.biz_phone.setText(b.phone)
        self._refresh_backup()
        self._refresh_scale()
        if self.license_panel is not None:
            self.license_panel.refresh()
        if self.ctx.is_demo:
            self.demo_label.setText(
                "지금은 예제 데이터 모드입니다. 여기서 입력한 내용은 실제 데이터에 저장되지 않으며 "
                "예제 모드를 다시 시작하면 초기화됩니다."
            )
            self.demo_btn.setText("실제 데이터로 돌아가기")
        else:
            self.demo_label.setText(
                "연습용 예제 데이터(장비·고객·예약·연체·미정산 사례)로 프로그램을 체험합니다. "
                "실제 데이터와 분리된 별도 파일을 사용하며 실제 데이터는 변경되지 않습니다."
            )
            self.demo_btn.setText("예제 데이터 모드 시작")

    def save_policy(self) -> bool:
        self.policy_errors.clear()
        multiplier = f"{self.multiplier.value():.2f}"
        text = (f"연체료 없이 기다려 주는 시간 {self.grace.value()}분, 연체 1일당 일 대여료의 {multiplier}배, "
                f"반납 후 준비 시간 {self.buffer.value()}분으로 "
                "변경합니다.\n이미 확정된 예약에는 소급 적용되지 않습니다.")
        if not feedback.confirm(self, text, "정책 변경 확인"):
            return False
        ok = feedback.run(self, lambda: self.ctx.settings.update_policy(
            grace_minutes=self.grace.value(), late_multiplier=multiplier,
            turnaround_buffer_minutes=self.buffer.value(),
        ), on_domain_error=self.policy_errors.show)
        if ok is None:
            return False
        feedback.info(self, "정책을 저장했습니다.")
        self.refresh()
        return True

    def save_business(self) -> bool:
        self.biz_errors.clear()
        ok = feedback.run(self, lambda: self.ctx.settings.update_business_info(
            name=self.biz_name.text(), phone=self.biz_phone.text()
        ), on_domain_error=self.biz_errors.show)
        if ok is None:
            return False
        feedback.info(self, "사업자 정보를 저장했습니다.")
        return True

    # ------------------------------------------------------------------ 화면 크기

    def _refresh_scale(self) -> None:
        prefs = self.ctx.preferences
        choice = display_scale.normalize_choice(prefs.get(display_scale.PREF_KEY) if prefs else None)
        self.scale.setCurrentIndex(max(0, self.scale.findData(choice)))
        self.scale.setEnabled(prefs is not None)
        self.scale_note.setText(
            f"지금 프로그램 배율: {round(display_scale.current_factor() * 100)}% (Windows·macOS 화면 배율과 별도). "
            "자동은 창이 화면에 다 들어가도록 필요할 때만 줄입니다. "
            "글자가 작거나 크면 직접 고르세요. 프로그램을 다시 시작하면 적용됩니다."
        )

    def save_scale(self) -> bool:
        prefs = self.ctx.preferences
        if prefs is None:
            return False
        choice = self.scale.currentData()
        if feedback.run(self, lambda: prefs.set(display_scale.PREF_KEY, choice) or True) is None:
            return False
        feedback.info(self, f"화면 크기를 '{display_scale.choice_label(choice)}'(으)로 저장했습니다.\n"
                            "프로그램을 다시 시작하면 적용됩니다.")
        return True

    # ------------------------------------------------------------------ 백업/복원

    def _refresh_backup(self) -> None:
        demo = self.ctx.is_demo
        for btn in (self.backup_btn, self.restore_btn, self.backup_dir_btn):
            btn.setEnabled(not demo)
        if demo:
            self.last_backup_label.setText("예제 데이터 모드에서는 백업·복원을 사용할 수 없습니다.")
        else:
            self.last_backup_label.setText(format_dt(self.ctx.backup.last_backup_at(), "아직 없음"))
        self.backup_dir_label.setText(str(default_backup_folder(self.ctx.preferences)))
        self._refresh_auto_backup()

    def _refresh_auto_backup(self) -> None:
        prefs = self.ctx.preferences
        usable = prefs is not None and not self.ctx.is_demo
        settings = AutoBackupSettings(prefs) if prefs is not None else None
        for box, value in ((self.auto_enabled, settings.enabled if settings else False),
                           (self.auto_copy, settings.copy_to_backup_dir if settings else False)):
            box.blockSignals(True)
            box.setChecked(value)
            box.blockSignals(False)
        self.auto_enabled.setEnabled(usable)
        self.auto_open_btn.setEnabled(usable)
        has_dir = prefs is not None and prefs.get("backup_dir") not in (None, "")
        self.auto_copy.setEnabled(usable and self.auto_enabled.isChecked() and has_dir)
        if self.ctx.is_demo:
            note = "예제 데이터 모드에서는 자동 백업을 만들지 않습니다."
        else:
            last = settings.last_at() if settings else None
            note = (f"마지막 자동 백업: {format_dt(last)}" if last else "아직 자동 백업이 없습니다.")
            note += (" 자동 백업은 이 PC 안에 저장되므로 PC 고장·분실에 대비하려면 "
                     "USB 등 다른 곳에 백업 파일을 따로 보관하세요.")
            if not has_dir:
                note += " (USB 등으로 복사하려면 먼저 '기본 폴더 변경…'으로 폴더를 고르세요.)"
        self.auto_note.setText(note)

    def _set_auto_enabled(self, on: bool) -> None:
        if self.ctx.preferences is not None:
            feedback.run(self, lambda: AutoBackupSettings(self.ctx.preferences).set_enabled(on))
        self._refresh_auto_backup()

    def _set_auto_copy(self, on: bool) -> None:
        if self.ctx.preferences is not None:
            feedback.run(self, lambda: AutoBackupSettings(self.ctx.preferences).set_copy_to_backup_dir(on))
        self._refresh_auto_backup()

    def choose_backup_dir(self) -> None:
        if self.ctx.preferences is None:
            return
        path = QFileDialog.getExistingDirectory(
            self, "기본 백업 폴더 선택", str(default_backup_folder(self.ctx.preferences))
        )
        if path:
            self.set_backup_dir(Path(path))

    def set_backup_dir(self, path: Path) -> None:
        if self.ctx.preferences is None:
            return
        feedback.run(self, lambda: self.ctx.preferences.set_backup_dir(path))
        self._refresh_backup()

    def backup_dialog(self) -> BackupResult | None:
        result = save_backup_dialog(self, self.ctx.backup, self.ctx.preferences)
        self._after_backup(result)
        return result

    def backup_to(self, path: Path, *, overwrite: bool = False) -> BackupResult | None:
        """테스트·자동화용: 대화상자 없이 지정 경로에 백업."""
        result = export_backup(self, self.ctx.backup, path, overwrite=overwrite)
        self._after_backup(result)
        return result

    def _after_backup(self, result: BackupResult | None) -> None:
        if result is not None:
            self._refresh_backup()
            self.backup_created.emit()

    def restore_dialog(self) -> None:
        path = choose_backup_file(self, self.ctx.preferences)
        if path is not None:
            self.restore_from(path)

    def restore_from(self, path: Path) -> ValidatedBackup | None:
        if self.ctx.is_demo:
            feedback.warn(self, "예제 데이터 모드에서는 복원할 수 없습니다. 실제 데이터로 돌아간 뒤 복원해주세요.")
            return None
        validated = confirm_restore(self, path)
        if validated is not None:
            self.restore_requested.emit(validated)
        return validated
