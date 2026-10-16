"""엑셀로 가져오기 (USABILITY_PLAN U10): 양식 저장 → 파일 선택 → 미리보기 → 오류 없는 행만 등록.

파일 읽기·검사·등록은 `services.bulk_import`에만 있다. 이 화면은 선택과 결과 표시만 한다.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rentdesk.context import AppContext
from rentdesk.domain.display import format_won
from rentdesk.services.bulk_import import (
    KIND_LABELS,
    ImportKind,
    ImportPreview,
    ImportResult,
    ImportRow,
    template_name,
)
from rentdesk.ui import feedback, theme
from rentdesk.ui.dialogs.customer_dialog import CUSTOMER_TYPE_LABELS
from rentdesk.ui.widgets import DataTable, button_row

FILE_FILTER = "Excel 통합 문서 (*.xlsx)"
_HEADERS = {
    ImportKind.ASSETS: ["행", "결과", "관리번호", "장비명", "카테고리", "일 대여료", "오류 이유·안내"],
    ImportKind.CUSTOMERS: ["행", "결과", "이름", "구분", "전화번호", "이메일", "오류 이유·안내"],
}


def _documents_dir() -> Path:
    docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(docs) if docs else Path.home()


class ImportDialog(QDialog):
    def __init__(self, ctx: AppContext, kind: ImportKind, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.kind = kind
        self.preview: ImportPreview | None = None
        self.result: ImportResult | None = None
        self.last_dir: Path | None = None
        label = KIND_LABELS[kind]
        self.setWindowTitle(f"{label} 엑셀로 가져오기")
        self.setMinimumSize(860, 560)

        steps = QLabel(
            f"① [양식 파일 저장]으로 {label} 양식을 받아 엑셀에서 채웁니다.  "
            "② [파일 선택]으로 고르면 행마다 등록 가능 여부를 먼저 보여줍니다.  "
            "③ 오류 없는 행만 등록하거나 취소할 수 있습니다."
        )
        steps.setWordWrap(True)
        self.template_btn = QPushButton("양식 파일 저장")
        self.template_btn.clicked.connect(self.save_template)
        self.choose_btn = theme.set_variant(QPushButton("파일 선택…"), "primary")
        self.choose_btn.clicked.connect(self.choose_file)
        self.file_label = QLabel("선택한 파일 없음")
        theme.set_tone(self.file_label, "muted")
        top = QHBoxLayout()
        top.addWidget(self.template_btn)
        top.addWidget(self.choose_btn)
        top.addWidget(self.file_label, 1)

        self.create_categories = QCheckBox("파일에 있는 새 카테고리를 만들고 등록")
        self.create_categories.setChecked(True)
        self.create_categories.toggled.connect(self._show_preview)
        self.create_categories.setVisible(kind == ImportKind.ASSETS)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.notices = QLabel()
        self.notices.setWordWrap(True)
        theme.set_tone(self.notices, "muted")
        self.table = DataTable(_HEADERS[kind])

        backup_text = (
            "예제 데이터 모드에서는 가져오기 전 백업을 만들지 않습니다."
            if ctx.import_backup_dir is None
            else "등록 직전에 지금 데이터를 자동으로 백업합니다(앱 데이터 폴더 backups/before_import)."
        )
        backup_note = QLabel(backup_text)
        backup_note.setWordWrap(True)
        theme.set_tone(backup_note, "muted")

        self.import_btn = theme.set_variant(QPushButton("등록"), "primary")
        self.import_btn.clicked.connect(self.run_import)
        cancel = QPushButton("취소(등록 안 함)")
        cancel.clicked.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(steps)
        lay.addLayout(top)
        lay.addWidget(self.create_categories)
        lay.addWidget(self.summary)
        lay.addWidget(self.notices)
        lay.addWidget(self.table, 1)
        lay.addWidget(backup_note)
        lay.addLayout(button_row(cancel, self.import_btn))
        self._show_preview()

    # ------------------------------------------------------------------ 파일

    def _start_dir(self) -> Path:
        return self.last_dir or _documents_dir()

    def save_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "양식 파일 저장", str(self._start_dir() / template_name(self.kind)), FILE_FILTER
        )
        if path:
            self.save_template_to(Path(path))

    def save_template_to(self, path: Path) -> Path | None:
        # 저장 대화상자가 이미 덮어쓰기를 확인했다.
        saved = feedback.run(self, lambda: self.ctx.imports.save_template(self.kind, path, overwrite=True))
        if saved is not None:
            self.last_dir = saved.parent
            feedback.info(self, f"양식 파일을 저장했습니다.\n{saved}\n\n엑셀에서 채운 뒤 [파일 선택]으로 골라주세요.")
        return saved

    def choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, f"가져올 {KIND_LABELS[self.kind]} 엑셀 파일", str(self._start_dir()),
            FILE_FILTER + ";;모든 파일 (*)",
        )
        if path:
            self.load_file(Path(path))

    def load_file(self, path: Path) -> ImportPreview | None:
        self.last_dir = path.parent
        preview = feedback.run(self, lambda: self.ctx.imports.preview(self.kind, path))
        self.preview = preview
        self.file_label.setText(f"{path.name} · 시트 '{preview.sheet_name}'" if preview else "선택한 파일 없음")
        self._show_preview()
        return preview

    # ------------------------------------------------------------------ 미리보기

    def _cells(self, row: ImportRow) -> list[str]:
        if self.kind == ImportKind.ASSETS:
            a = row.asset
            if a is not None:
                return [a.asset_code or "(자동)", a.name, a.category_name, format_won(a.daily_rate_won)]
            keys = ("asset_code", "name", "category", "daily_rate_won")
        else:
            c = row.customer
            if c is not None:
                return [c.name, CUSTOMER_TYPE_LABELS[c.customer_type], c.phone or "", c.email or ""]
            keys = ("name", "customer_type", "phone", "email")
        return ["" if row.cells.get(k) is None else str(row.cells.get(k)) for k in keys]

    def _show_preview(self) -> None:
        create = self.create_categories.isChecked()
        preview = self.preview
        rows = preview.rows if preview else ()
        ok = preview.ok_rows(create) if preview else []
        bad = len(rows) - len(ok)
        by_no = {r.row_no: r for r in rows}
        self.table.set_rows([
            (r.row_no, [r.row_no, "등록 가능" if r.is_ok(create) else "오류",
                        *self._cells(r), " / ".join(r.problems(create) + r.notes)])
            for r in rows
        ], highlight=lambda key: not by_no[key].is_ok(create))
        reason_col = self.table.columnCount() - 1
        for i in range(self.table.rowCount()):  # 오류가 여러 개면 칸이 잘리므로 마우스를 올리면 전체 표시
            item = self.table.item(i, reason_col)
            item.setToolTip(item.text().replace(" / ", "\n"))
        if preview is None:
            self.summary.setText("파일을 선택하면 여기에 행별 결과가 나옵니다.")
        else:
            text = f"전체 <b>{len(rows):,}</b>행 · 등록 가능 <b>{len(ok):,}</b>행"
            if bad:
                text += f" · <span style='color:{theme.color('danger').name()}'>오류 <b>{bad:,}</b>행(등록 안 됨)</span>"
            if preview.new_categories:
                names = ", ".join(preview.new_categories)
                text += f"<br>새 카테고리: {names}" + ("" if create else " — 만들지 않으면 해당 행은 오류로 둡니다.")
            self.summary.setText(text)
        self.notices.setText("\n".join(preview.notices) if preview else "")
        self.notices.setVisible(bool(preview and preview.notices))
        self.import_btn.setEnabled(bool(ok))
        if ok and bad:
            self.import_btn.setText(f"오류 없는 {len(ok):,}행만 등록")
        else:
            self.import_btn.setText(f"{len(ok):,}행 등록" if ok else "등록")

    # ------------------------------------------------------------------ 등록

    def run_import(self) -> None:
        if self.preview is None:
            return
        create = self.create_categories.isChecked()
        ok = len(self.preview.ok_rows(create))
        bad = len(self.preview.rows) - ok
        label = KIND_LABELS[self.kind]
        question = f"{label} {ok:,}건을 등록합니다."
        if bad:
            question += f"\n오류가 있는 {bad:,}행은 등록하지 않습니다(파일을 고쳐 나중에 다시 가져올 수 있습니다)."
        if not feedback.confirm(self, question, "엑셀로 가져오기"):
            return
        result = feedback.run(self, lambda: self.ctx.imports.run_import(
            self.preview, create_categories=create, backup_dir=self.ctx.import_backup_dir,
        ))
        if result is None:
            return
        self.result = result
        lines = [f"{label} {result.created:,}건을 등록했습니다."]
        if result.skipped:
            lines.append(f"등록하지 않은 행: {result.skipped:,}행")
        if result.created_categories:
            lines.append(f"새로 만든 카테고리: {', '.join(result.created_categories)}")
        if result.backup_path is not None:
            lines.append(f"\n가져오기 전 백업: {result.backup_path}")
        feedback.info(self, "\n".join(lines), "가져오기 완료")
        self.accept()
