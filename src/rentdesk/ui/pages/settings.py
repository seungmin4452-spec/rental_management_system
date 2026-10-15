"""S-09 설정: 운영 정책, 사업자 정보, 백업/복원, 라이선스, 예제 데이터 모드."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
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
from rentdesk.services.backup import UNENCRYPTED_NOTICE, BackupResult, ValidatedBackup
from rentdesk.services.settings import MAX_MINUTES
from rentdesk.ui import feedback, theme
from rentdesk.ui.backup_actions import (
    choose_backup_file,
    confirm_restore,
    default_backup_folder,
    export_backup,
    save_backup_dialog,
)
from rentdesk.ui.pages.base import Page
from rentdesk.ui.widgets import FormErrors, button_row


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
        self.buffer = QSpinBox()
        self.buffer.setRange(0, MAX_MINUTES)
        self.buffer.setSuffix("분")
        self.policy_errors.add_row("연체 유예시간", self.grace, "grace_minutes")
        self.policy_errors.add_row("연체 배수", self.multiplier, "late_multiplier")
        self.policy_errors.add_row("정비·대여 전환 버퍼", self.buffer, "turnaround_buffer_minutes")
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

        self.license_panel = None
        license_box = None
        if self.ctx.license is not None:
            from rentdesk.ui.dialogs.license_dialog import LicensePanel

            license_box = QGroupBox("라이선스")
            ll = QVBoxLayout(license_box)
            self.license_panel = LicensePanel(self.ctx.license, license_box)
            ll.addWidget(self.license_panel)

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
        text = (f"유예 {self.grace.value()}분, 연체 배수 {multiplier}, 전환 버퍼 {self.buffer.value()}분으로 "
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
