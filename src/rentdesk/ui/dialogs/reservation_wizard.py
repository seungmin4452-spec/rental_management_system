"""예약 작성/수정 3단계 (S-04, W-02).

1. 고객·시작·약정 반납 일시
2. 사용 가능한 장비 선택(사용 불가 장비는 선택 불가 + 사유/겹치는 예약번호 표시)
3. 예상 대여료·보증금·할인 확인 후 `임시 저장` 또는 `예약 확정`

확정 시 충돌은 서비스가 DB 트랜잭션 안에서 다시 검사한다(화면 사전 검사만 신뢰하지 않음).
"""

from __future__ import annotations

from datetime import timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from rentdesk.context import AppContext
from rentdesk.domain.display import format_dt, format_won
from rentdesk.domain.errors import ConflictError, DomainError, ValidationError
from rentdesk.domain.models import OrderStatus, RentalOrder
from rentdesk.services.reservations import ReservationInput
from rentdesk.ui import feedback
from rentdesk.ui.widgets import FormErrors, SeoulDateTimeEdit, WonSpinBox, next_hour

STEP_TITLES = ["고객·기간", "장비 선택", "금액 확인"]


class ReservationWizard(QDialog):
    def __init__(self, ctx: AppContext, order: RentalOrder | None = None,
                 parent: QWidget | None = None, *, customer_id: int | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.order = order
        self.result_order: RentalOrder | None = None
        self.setMinimumSize(720, 520)
        self._selected: set[int] = set(i.asset_id for i in order.items
                                       if i.status.value != "CANCELED") if order else set()

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_step1(customer_id))
        self.stack.addWidget(self._build_step2())
        self.stack.addWidget(self._build_step3())

        self.prev_btn = QPushButton("이전")
        self.next_btn = QPushButton("다음")
        self.cancel_btn = QPushButton("취소")
        self.draft_btn = QPushButton("임시 저장")
        self.confirm_btn = QPushButton("예약 확정")
        self.save_btn = QPushButton("변경 저장")
        self.prev_btn.clicked.connect(self.go_prev)
        self.next_btn.clicked.connect(self.go_next)
        self.cancel_btn.clicked.connect(self.reject)
        self.draft_btn.clicked.connect(lambda: self.save(confirm=False))
        self.confirm_btn.clicked.connect(lambda: self.save(confirm=True))
        self.save_btn.clicked.connect(lambda: self.save(confirm=False))

        nav = QHBoxLayout()
        nav.addWidget(self.cancel_btn)
        nav.addStretch(1)
        for b in (self.prev_btn, self.next_btn, self.draft_btn, self.confirm_btn, self.save_btn):
            nav.addWidget(b)
        lay = QVBoxLayout(self)
        self.step_label = QLabel()
        lay.addWidget(self.step_label)
        lay.addWidget(self.stack, 1)
        lay.addLayout(nav)
        self._show_step(0)

    # ------------------------------------------------------------------ build

    @property
    def is_confirmed_edit(self) -> bool:
        return self.order is not None and self.order.status == OrderStatus.CONFIRMED

    def _build_step1(self, customer_id: int | None) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.errors1 = FormErrors(form)
        self.customer = QComboBox()
        self.customer.setEditable(True)
        self.customer.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.customer.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.customer.completer().setCompletionMode(
            self.customer.completer().CompletionMode.PopupCompletion
        )
        self._load_customers()
        new_customer = QPushButton("+ 신규 고객")
        new_customer.clicked.connect(self._new_customer)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.customer, 1)
        rl.addWidget(new_customer)

        start = next_hour(self.ctx.now())
        self.start = SeoulDateTimeEdit(self.order.start_at if self.order else start)
        self.due = SeoulDateTimeEdit(self.order.due_at if self.order else start + timedelta(days=1))
        self.days_label = QLabel()
        self.start.dateTimeChanged.connect(self._update_days)
        self.due.dateTimeChanged.connect(self._update_days)

        self.errors1.add_row("고객 *", row, "customer_id")
        self.errors1.add_row("예약 시작", self.start, "start_at")
        self.errors1.add_row("반납 예정", self.due, "due_at")
        form.addRow("예상 대여일수", self.days_label)
        target = self.order.customer_id if self.order else customer_id
        if target is not None:
            idx = self.customer.findData(target)
            if idx < 0:  # 보관된 고객의 기존 예약 수정
                c = self.ctx.customers.get_customer(target)
                self.customer.addItem(f"{c.name} (보관)", c.id)
                idx = self.customer.count() - 1
            self.customer.setCurrentIndex(idx)
        else:
            self.customer.setCurrentIndex(-1)
        self._update_days()
        return page

    def _build_step2(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        filters = QHBoxLayout()
        self.asset_query = QLineEdit()
        self.asset_query.setPlaceholderText("장비번호/이름 검색")
        self.category = QComboBox()
        self.category.addItem("전체 카테고리", None)
        for c in self.ctx.assets.list_categories():
            self.category.addItem(c.name, c.id)
        self.only_available = QCheckBox("사용 가능만")
        self.only_available.setChecked(False)
        self.asset_query.textChanged.connect(self._load_assets)
        self.category.currentIndexChanged.connect(self._load_assets)
        self.only_available.toggled.connect(self._load_assets)
        filters.addWidget(self.asset_query, 1)
        filters.addWidget(self.category)
        filters.addWidget(self.only_available)
        self.asset_list = QListWidget()
        self.asset_list.itemChanged.connect(self._on_item_changed)
        self.selected_label = QLabel()
        self.errors2 = QLabel()
        self.errors2.setStyleSheet("color: #c62828;")
        self.errors2.setWordWrap(True)
        lay.addLayout(filters)
        lay.addWidget(self.asset_list, 1)
        lay.addWidget(self.selected_label)
        lay.addWidget(self.errors2)
        return page

    def _build_step3(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.errors3 = FormErrors(form)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.discount = WonSpinBox(self.order.discount_won if self.order else 0)
        self.discount.valueChanged.connect(self._update_quote)
        self.quote_label = QLabel()
        self.notes = QPlainTextEdit((self.order.notes or "") if self.order else "")
        self.notes.setFixedHeight(60)
        warn = QLabel("주의: 예약 확정 시 동일 장비·기간의 충돌을 최종 다시 검사합니다. "
                      "임시 저장은 장비를 점유하지 않습니다.")
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #8a6d00;")
        form.addRow(self.summary)
        self.errors3.add_row("할인", self.discount, "discount_won")
        form.addRow("예상 금액", self.quote_label)
        self.errors3.add_row("메모", self.notes, "notes")
        form.addRow(warn)
        return page

    # ------------------------------------------------------------------ data

    def _load_customers(self) -> None:
        self.customer.clear()
        for c in self.ctx.customers.search_customers():
            self.customer.addItem(c.name, c.id)

    def _new_customer(self) -> None:
        from rentdesk.ui.dialogs.customer_dialog import CustomerDialog

        dlg = CustomerDialog(self.ctx, parent=self)
        if dlg.exec() and dlg.result_customer:
            self._load_customers()
            self.customer.setCurrentIndex(self.customer.findData(dlg.result_customer.id))

    def _customer_id(self) -> int | None:
        idx = self.customer.currentIndex()
        if idx < 0 or self.customer.itemText(idx) != self.customer.currentText():
            return None
        return self.customer.itemData(idx)

    def _update_days(self) -> None:
        try:
            q = self.ctx.reservations.preview_quote([], self.start.value(), self.due.value())
            self.days_label.setText(f"{q.rental_days}일 (24시간 단위 올림, 최소 1일)")
        except DomainError as exc:
            self.days_label.setText(exc.message)

    def _load_assets(self) -> None:
        exclude = self.order.id if self.order else None
        buffer = self.order.turnaround_buffer_snapshot_minutes if self.is_confirmed_edit else None
        options = self.ctx.availability.asset_options(
            self.start.value(), self.due.value(), query=self.asset_query.text(),
            category_id=self.category.currentData(), exclude_order_id=exclude,
            buffer_minutes=buffer,
        )
        self.asset_list.blockSignals(True)
        self.asset_list.clear()
        for opt in options:
            if self.only_available.isChecked() and not opt.available and opt.asset.id not in self._selected:
                continue
            a = opt.asset
            status = "사용 가능" if opt.available else "사용 불가"
            item = QListWidgetItem(
                f"{a.asset_code}  {a.name}   {format_won(a.daily_rate_won)}/일   "
                f"보증금 {format_won(a.deposit_won)}   {status}"
            )
            item.setData(Qt.ItemDataRole.UserRole, a.id)
            flags = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
            if not opt.available:
                item.setToolTip(opt.reason)
                if a.id not in self._selected:
                    flags = Qt.ItemFlag.ItemIsUserCheckable  # 선택 불가(비활성)
            item.setFlags(flags)
            item.setCheckState(
                Qt.CheckState.Checked if a.id in self._selected else Qt.CheckState.Unchecked
            )
            self.asset_list.addItem(item)
        self.asset_list.blockSignals(False)
        self._update_selected_label(options)

    def _update_selected_label(self, options=None) -> None:
        self.selected_label.setText(f"선택된 장비: {len(self._selected)}개")
        if options is not None:
            blocked = [o for o in options if o.asset.id in self._selected and not o.available]
            self.errors2.setText(
                "선택한 장비 중 이 기간에 사용할 수 없는 장비가 있습니다:\n"
                + "\n".join(o.reason for o in blocked) if blocked else ""
            )

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        asset_id = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._selected.add(asset_id)
        else:
            self._selected.discard(asset_id)
        self._update_selected_label()

    def set_selected(self, asset_ids) -> None:
        """테스트·외부 호출용 장비 선택."""
        self._selected = set(asset_ids)
        self._load_assets()

    def _input(self) -> ReservationInput:
        return ReservationInput(
            customer_id=self._customer_id() or 0,
            start_at=self.start.value(),
            due_at=self.due.value(),
            asset_ids=sorted(self._selected),
            discount_won=self.discount.value(),
            notes=self.notes.toPlainText(),
        )

    def _update_quote(self) -> None:
        names = {
            a.id: a.asset_code for a in self.ctx.assets.search_assets(include_retired=True)
        }
        customer = self.customer.currentText()
        codes = ", ".join(names.get(i, str(i)) for i in sorted(self._selected))
        self.summary.setText(
            f"고객: <b>{customer}</b><br>기간: {format_dt(self.start.value())} ~ "
            f"{format_dt(self.due.value())}<br>장비: {codes}"
        )
        self.errors3.clear()
        try:
            q = self.ctx.reservations.preview_quote(
                sorted(self._selected), self.start.value(), self.due.value(),
                self.discount.value(), order_id=self.order.id if self.order else None,
            )
        except DomainError as exc:
            if not self.errors3.show(exc):
                self.quote_label.setText(exc.message)
            return
        self.quote_label.setText(
            f"대여일수 {q.rental_days}일 · 기본 대여료 {format_won(q.base_rental_fee_won)}"
            f" − 할인 {format_won(q.discount_won)} = <b>{format_won(q.expected_total_rental_fee_won)}</b>"
            f"<br>보증금 예상: <b>{format_won(q.expected_deposit_won)}</b>"
        )

    # ------------------------------------------------------------------ navigation

    def _show_step(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        mode = "예약 수정" if self.order else "새 예약"
        if self.order:
            mode += f" {self.order.order_no}"
        self.setWindowTitle(f"{mode} ({idx + 1}/3)")
        self.step_label.setText(f"<b>{idx + 1}단계: {STEP_TITLES[idx]}</b>")
        last = idx == 2
        self.prev_btn.setVisible(idx > 0)
        self.next_btn.setVisible(not last)
        draft_mode = self.order is None or self.order.status == OrderStatus.DRAFT
        self.draft_btn.setVisible(last and draft_mode)
        self.confirm_btn.setVisible(last and draft_mode)
        self.save_btn.setVisible(last and not draft_mode)

    def go_prev(self) -> None:
        self._show_step(max(0, self.stack.currentIndex() - 1))

    def go_next(self) -> bool:
        idx = self.stack.currentIndex()
        if idx == 0:
            self.errors1.clear()
            if self._customer_id() is None:
                self.errors1.show(ValidationError("customer_id", "목록에서 고객을 선택해주세요."))
                return False
            if not self.start.value() < self.due.value():
                self.errors1.show(
                    ValidationError("due_at", "약정 반납 일시는 시작 일시보다 뒤여야 합니다.")
                )
                return False
            self._load_assets()
        elif idx == 1:
            if not self._selected:
                self.errors2.setText("예약할 장비를 1개 이상 선택해주세요.")
                return False
            self._update_quote()
        self._show_step(idx + 1)
        return True

    def _route_error(self, exc: DomainError) -> bool:
        if isinstance(exc, ConflictError):
            # 저장 직전 재검사에서 충돌: 메시지 상자로 충돌 예약을 보이고 장비 선택 단계로 이동
            feedback.warn(self, exc.message, "예약 충돌")
            self._show_step(1)
            self._load_assets()
            return True
        field = getattr(exc, "field", None)
        if field in ("customer_id", "start_at", "due_at"):
            self._show_step(0)
            return self.errors1.show(exc)
        if field == "asset_ids":
            self._show_step(1)
            self.errors2.setText(exc.message)
            return True
        return self.errors3.show(exc)

    def save(self, *, confirm: bool) -> bool:
        data = self._input()
        svc = self.ctx.reservations
        if self.order is None:
            action = (lambda: svc.create_confirmed(data)) if confirm else (lambda: svc.create_draft(data))
        else:
            order_id = self.order.id
            action = lambda: svc.update_order(order_id, data, confirm=confirm)  # noqa: E731
        result = feedback.run(self, action, on_domain_error=self._route_error)
        if result is None:
            return False
        self.result_order = result
        verb = "확정" if confirm else ("변경" if self.is_confirmed_edit else "임시 저장")
        feedback.info(self, f"예약 {result.order_no}이(가) {verb}되었습니다.")
        self.accept()
        return True
