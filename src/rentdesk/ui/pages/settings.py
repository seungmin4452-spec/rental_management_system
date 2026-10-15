"""S-09 설정: 운영 정책, 사업자 정보, 예제 데이터 모드."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from rentdesk.services.settings import MAX_MINUTES
from rentdesk.ui import feedback
from rentdesk.ui.pages.base import Page
from rentdesk.ui.widgets import FormErrors, button_row


class SettingsPage(Page):
    title = "설정"
    demo_toggle_requested = Signal()

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
        save_policy = QPushButton("정책 저장")
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
        save_biz = QPushButton("사업자 정보 저장")
        save_biz.clicked.connect(self.save_business)
        bf.addRow(button_row(save_biz))

        demo_box = QGroupBox("예제 데이터 모드")
        dl = QVBoxLayout(demo_box)
        self.demo_label = QLabel()
        self.demo_label.setWordWrap(True)
        self.demo_btn = QPushButton()
        self.demo_btn.clicked.connect(self.demo_toggle_requested.emit)
        dl.addWidget(self.demo_label)
        dl.addLayout(button_row(self.demo_btn))

        lay = QVBoxLayout(self)
        lay.addWidget(policy_box)
        lay.addWidget(biz_box)
        lay.addWidget(demo_box)
        lay.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        p = self.ctx.settings.get_policy()
        self.grace.setValue(p.grace_minutes)
        self.multiplier.setValue(float(p.late_multiplier))
        self.buffer.setValue(p.turnaround_buffer_minutes)
        b = self.ctx.settings.get_business_info()
        self.biz_name.setText(b.name)
        self.biz_phone.setText(b.phone)
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
