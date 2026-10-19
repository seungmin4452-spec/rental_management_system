"""예약 작성/수정 3단계 (S-04, W-02).

1. 고객·시작·약정 반납 일시
2. 사용 가능한 장비 선택(표. 사용 불가 장비는 선택 불가 + 이유/겹치는 예약번호를 열로 표시).
   [세트 추가]로 세트 구성 장비를 한 번에 담는다(안 되는 장비는 같은 카테고리로 대체·빼기, R4).
3. 예상 대여료·보증금·할인 확인 후 `임시 저장` 또는 `예약 확정`

확정 시 충돌은 서비스가 DB 트랜잭션 안에서 다시 검사한다(화면 사전 검사만 신뢰하지 않음).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rentdesk.context import AppContext
from rentdesk.domain.display import format_dt, format_won
from rentdesk.domain.errors import ConflictError, DomainError, ValidationError
from rentdesk.domain.models import OrderStatus, RentalOrder
from rentdesk.services.kits import KitPick
from rentdesk.services.reservations import ReservationInput
from rentdesk.ui import feedback, theme
from rentdesk.ui.widgets import FormErrors, SeoulDateTimeEdit, WonSpinBox, next_hour

STEP_TITLES = ["고객·기간", "장비 선택", "금액 확인"]
ASSET_COLUMNS = ["선택", "관리번호", "장비명", "일 대여료", "보증금", "상태", "사용 불가 이유", "세트"]


class ReservationWizard(QDialog):
    def __init__(self, ctx: AppContext, order: RentalOrder | None = None,
                 parent: QWidget | None = None, *, customer_id: int | None = None,
                 asset_ids: Sequence[int] = (), start_at: datetime | None = None,
                 due_at: datetime | None = None):
        """새 예약은 `asset_ids`·`start_at`·`due_at`로 장비·기간을 미리 채울 수 있다(예약 달력)."""
        super().__init__(parent)
        self.ctx = ctx
        self.order = order
        self.result_order: RentalOrder | None = None
        self.setMinimumSize(900, 540)
        self._selected: set[int] = set(i.asset_id for i in order.items
                                       if i.status.value != "CANCELED") if order else set(asset_ids)
        # 세트로 담은 장비: 세트 id → 장비 id 목록(담은 순서), 표시용 이름
        self._kits: dict[int, list[int]] = {}
        self._kit_names: dict[int, str] = {}
        if order:
            for kit in order.kits:
                self._kits[kit.kit_id] = [i.asset_id for i in order.items if i.order_kit_id == kit.id]
                self._kit_names[kit.kit_id] = kit.name

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_step1(customer_id, start_at, due_at))
        self.stack.addWidget(self._build_step2())
        self.stack.addWidget(self._build_step3())

        self.prev_btn = QPushButton("이전")
        self.next_btn = theme.set_variant(QPushButton("다음"), "primary")
        self.cancel_btn = QPushButton("취소")
        self.draft_btn = QPushButton("임시 저장")
        self.confirm_btn = theme.set_variant(QPushButton("예약 확정"), "primary")
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

    def _build_step1(self, customer_id: int | None, start_at: datetime | None,
                     due_at: datetime | None) -> QWidget:
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

        start = start_at or next_hour(self.ctx.now())
        due = due_at or start + timedelta(days=1)
        self.start = SeoulDateTimeEdit(self.order.start_at if self.order else start)
        self.due = SeoulDateTimeEdit(self.order.due_at if self.order else due)
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
                self.customer.addItem(f"{c.name} (숨김)", c.id)
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
        self.asset_table = QTableWidget(0, len(ASSET_COLUMNS))
        self.asset_table.setHorizontalHeaderLabels(ASSET_COLUMNS)
        self.asset_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.asset_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.asset_table.setShowGrid(False)
        self.asset_table.setWordWrap(False)
        self.asset_table.verticalHeader().setVisible(False)
        self.asset_table.horizontalHeader().setStretchLastSection(True)
        self.asset_table.itemChanged.connect(self._on_item_changed)
        self.selected_label = QLabel()
        self.errors2 = QLabel()
        theme.set_tone(self.errors2, "danger")
        self.errors2.setWordWrap(True)
        self.add_kit_btn = QPushButton("세트 추가")
        self.add_kit_btn.setToolTip("세트 구성 장비를 한 번에 담습니다. 안 되는 장비는 같은 카테고리 장비로 바꿀 수 있습니다.")
        self.add_kit_btn.clicked.connect(self.add_kit)
        self.remove_kit_btn = QPushButton("선택 세트 빼기")
        self.remove_kit_btn.clicked.connect(self.remove_selected_kit)
        self.kit_list = QListWidget()
        self.kit_list.setMaximumHeight(72)
        self.kit_list.itemSelectionChanged.connect(self._update_kit_buttons)
        kit_row = QHBoxLayout()
        kit_row.addWidget(self.add_kit_btn)
        kit_row.addWidget(self.kit_list, 1)
        kit_row.addWidget(self.remove_kit_btn)
        lay.addLayout(filters)
        lay.addWidget(self.asset_table, 1)
        lay.addLayout(kit_row)
        lay.addWidget(self.selected_label)
        lay.addWidget(self.errors2)
        self._refresh_kit_list()
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
        theme.set_tone(warn, "warning")
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
            self.days_label.setText(
                self.ctx.reservations.period_days_text(self.start.value(), self.due.value())
            )
        except DomainError as exc:
            self.days_label.setText(exc.message)

    def _load_assets(self) -> None:
        exclude = self.order.id if self.order else None
        # 준비 시간은 장비 카테고리별(R2). 확정 예약 수정이면 담긴 장비는 확정 때 스냅샷 값(서비스가 처리).
        options = self.ctx.availability.asset_options(
            self.start.value(), self.due.value(), query=self.asset_query.text(),
            category_id=self.category.currentData(), exclude_order_id=exclude,
        )
        shown = [o for o in options if not (self.only_available.isChecked() and not o.available
                                             and o.asset.id not in self._selected)]
        shown.sort(key=lambda o: not (o.available or o.asset.id in self._selected))  # 고를 수 있는 장비를 위로
        table = self.asset_table
        table.blockSignals(True)
        table.setRowCount(len(shown))
        muted = theme.color("text_muted")
        for r, opt in enumerate(shown):
            a = opt.asset
            selectable = opt.available or a.id in self._selected
            check = QTableWidgetItem()
            check.setData(Qt.ItemDataRole.UserRole, a.id)
            flags = Qt.ItemFlag.ItemIsUserCheckable
            if selectable:
                flags |= Qt.ItemFlag.ItemIsEnabled  # 사용 불가 장비는 체크 비활성
            check.setFlags(flags)
            check.setCheckState(
                Qt.CheckState.Checked if a.id in self._selected else Qt.CheckState.Unchecked
            )
            table.setItem(r, 0, check)
            values = [a.asset_code, a.name, format_won(a.daily_rate_won), format_won(a.deposit_won),
                      "사용 가능" if opt.available else "사용 불가", "" if opt.available else opt.short_reason,
                      self._kit_name_of(a.id)]
            for c, value in enumerate(values, start=1):
                cell = QTableWidgetItem(value)
                cell.setFlags(Qt.ItemFlag.ItemIsEnabled)
                if c in (3, 4):
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if not opt.available:
                    cell.setForeground(theme.color("danger") if c == 6 else muted)
                    cell.setToolTip(opt.reason)
                table.setItem(r, c, cell)
        table.resizeColumnsToContents()
        table.blockSignals(False)
        self._update_selected_label(options)

    def _kit_of(self, asset_id: int) -> int | None:
        return next((k for k, ids in self._kits.items() if asset_id in ids), None)

    def _kit_name_of(self, asset_id: int) -> str:
        kit_id = self._kit_of(asset_id)
        return "" if kit_id is None else self._kit_names.get(kit_id, "")

    def _kit_picks(self) -> list[KitPick]:
        return [KitPick(k, list(ids)) for k, ids in self._kits.items() if ids]

    def _refresh_kit_list(self) -> None:
        codes = {a.id: a.asset_code for a in self.ctx.assets.search_assets(include_retired=True)}
        self.kit_list.clear()
        for kit_id, ids in self._kits.items():
            item = QListWidgetItem(
                f"{self._kit_names.get(kit_id, '세트')}: " + ", ".join(codes.get(i, str(i)) for i in ids)
            )
            item.setData(Qt.ItemDataRole.UserRole, kit_id)
            self.kit_list.addItem(item)
        self.kit_list.setVisible(bool(self._kits))
        self._update_kit_buttons()

    def _update_kit_buttons(self) -> None:
        self.remove_kit_btn.setVisible(bool(self._kits))
        self.remove_kit_btn.setEnabled(bool(self.kit_list.selectedItems()))

    def apply_kit_pick(self, pick: KitPick, name: str) -> None:
        """세트 담기(대체·제외 반영된 장비). 따로 담겨 있던 구성 장비는 세트로 옮긴다."""
        self._kits[pick.kit_id] = list(pick.asset_ids)
        self._kit_names[pick.kit_id] = name
        self._selected |= set(pick.asset_ids)
        self._refresh_kit_list()
        self._load_assets()

    def add_kit(self) -> None:
        from rentdesk.ui.dialogs.kit_pick_dialog import KitPickDialog

        in_kits = {a for ids in self._kits.values() for a in ids}
        dlg = KitPickDialog(
            self.ctx, self.start.value(), self.due.value(),
            order_id=self.order.id if self.order else None,
            taken=sorted(self._selected - in_kits), locked=sorted(in_kits),
            exclude_kits=list(self._kits), parent=self,
        )
        if dlg.exec() and dlg.result_pick is not None and dlg.plan is not None:
            self.apply_kit_pick(dlg.result_pick, dlg.plan.kit.name)

    def remove_kit(self, kit_id: int) -> None:
        """세트 빼기: 세트로 담은 장비도 예약에서 함께 뺀다."""
        for asset_id in self._kits.pop(kit_id, []):
            self._selected.discard(asset_id)
        self._kit_names.pop(kit_id, None)
        self._refresh_kit_list()
        self._load_assets()

    def remove_selected_kit(self) -> None:
        items = self.kit_list.selectedItems()
        if items:
            self.remove_kit(items[0].data(Qt.ItemDataRole.UserRole))

    def _update_selected_label(self, options=None) -> None:
        in_kits = sum(len(ids) for ids in self._kits.values())
        self.selected_label.setText(
            f"선택된 장비: {len(self._selected)}개" + (f" (세트 {len(self._kits)}개 · {in_kits}개 포함)" if self._kits else "")
        )
        if options is not None:
            blocked = [o for o in options if o.asset.id in self._selected and not o.available]
            self.errors2.setText(
                "선택한 장비 중 이 기간에 사용할 수 없는 장비가 있습니다:\n"
                + "\n".join(o.reason for o in blocked) if blocked else ""
            )

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        asset_id = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._selected.add(asset_id)
        else:
            self._selected.discard(asset_id)
            kit_id = self._kit_of(asset_id)
            if kit_id is not None:  # 세트 장비를 빼면 세트에서도 뺀다(마지막 장비면 세트도 빠짐)
                self._kits[kit_id].remove(asset_id)
                if not self._kits[kit_id]:
                    del self._kits[kit_id]
                    self._kit_names.pop(kit_id, None)
                self._refresh_kit_list()
        self._update_selected_label()

    def set_selected(self, asset_ids) -> None:
        """테스트·외부 호출용 장비 선택(빠진 장비는 세트에서도 뺀다)."""
        self._selected = set(asset_ids)
        self._kits = {k: [a for a in ids if a in self._selected] for k, ids in self._kits.items()}
        self._kits = {k: ids for k, ids in self._kits.items() if ids}
        self._refresh_kit_list()
        self._load_assets()

    def _input(self) -> ReservationInput:
        return ReservationInput(
            customer_id=self._customer_id() or 0,
            start_at=self.start.value(),
            due_at=self.due.value(),
            asset_ids=sorted(self._selected),
            discount_won=self.discount.value(),
            notes=self.notes.toPlainText(),
            kits=self._kit_picks(),
        )

    def _update_quote(self) -> None:
        names = {
            a.id: a.asset_code for a in self.ctx.assets.search_assets(include_retired=True)
        }
        customer = self.customer.currentText()
        codes = ", ".join(names.get(i, str(i)) for i in sorted(self._selected))
        kits = "".join(
            f"<br>세트: {self._kit_names.get(k, '세트')} ({', '.join(names.get(i, str(i)) for i in ids)})"
            for k, ids in self._kits.items()
        )
        self.summary.setText(
            f"고객: <b>{customer}</b><br>기간: {format_dt(self.start.value())} ~ "
            f"{format_dt(self.due.value())}<br>장비: {codes}{kits}"
        )
        self.errors3.clear()
        try:
            q = self.ctx.reservations.preview_quote(
                sorted(self._selected), self.start.value(), self.due.value(),
                self.discount.value(), order_id=self.order.id if self.order else None,
                kits=self._kit_picks(),
            )
        except DomainError as exc:
            if not self.errors3.show(exc):
                self.quote_label.setText(exc.message)
            return
        price_lines = self.ctx.reservations.price_basis(
            sorted(self._selected), self.start.value(), self.due.value(),
            order_id=self.order.id if self.order else None,
        )
        kit_text = "".join(
            f" − 세트 할인({k.name}, {k.label}) {format_won(k.amount_won)}" for k in q.kit_discounts if k.amount_won
        )
        self.quote_label.setText(
            f"대여일수 {q.days_label} · 기본 대여료 {format_won(q.base_rental_fee_won)}{kit_text}"
            f" − 할인 {format_won(q.discount_won)} = <b>{format_won(q.expected_total_rental_fee_won)}</b>"
            f"<br>보증금 예상: <b>{format_won(q.expected_deposit_won)}</b>"
            + "".join(f"<br>{line}" for line in price_lines)
            + "".join(f"<br>{line}" for line in self.ctx.reservations.policy_basis(sorted(self._selected)))
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
        if field in ("asset_ids", "kits"):
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
