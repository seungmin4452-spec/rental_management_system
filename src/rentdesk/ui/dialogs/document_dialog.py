"""확인서·견적서 미리보기 + PDF 저장·인쇄 (R6)."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QDialog, QFileDialog, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from rentdesk.context import AppContext
from rentdesk.domain.documents import Document, DocumentKind, render_html
from rentdesk.ui import document_output, feedback, theme
from rentdesk.ui.widgets import button_row

log = logging.getLogger("rentdesk.ui")

PREF_KEY = "document_dir"  # 기기별 마지막 PDF 저장 폴더(백업에 넣지 않는 기기 경로)
SAVE_FAILED = ("PDF 파일을 저장하지 못했습니다. 저장할 폴더에 쓸 수 있는지, "
               "같은 이름의 파일이 다른 프로그램에서 열려 있지 않은지 확인해주세요.")


class DocumentDialog(QDialog):
    def __init__(self, document: Document, parent: QWidget | None = None, *, preferences=None) -> None:
        super().__init__(parent)
        self.document = document
        self.preferences = preferences
        self.saved_path: Path | None = None
        self.setWindowTitle(f"{document.title} {document.order_no}")
        self.setMinimumSize(760, 600)

        self.preview = QTextBrowser()
        self.preview.setOpenLinks(False)
        # 미리보기는 테마와 상관없이 종이처럼 흰 바탕·검은 글자(다크 모드에서 표 머리글이 안 보이던 문제)
        self.preview.setObjectName("PaperPreview")
        preview_doc = document_output.text_document(document)
        self.preview.setDocument(preview_doc)
        preview_doc.setParent(self.preview)

        self.pdf_btn = theme.set_variant(QPushButton("PDF로 저장…"), "primary")
        self.pdf_btn.clicked.connect(self.save_pdf_dialog)
        self.print_btn = QPushButton("인쇄…")
        self.print_btn.clicked.connect(self.print_dialog)
        close = QPushButton("닫기")
        close.clicked.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(self.preview, 1)
        lay.addLayout(button_row(self.print_btn, self.pdf_btn, close))

    def html(self) -> str:
        return render_html(self.document)

    def _default_dir(self) -> Path:
        value = self.preferences.get(PREF_KEY) if self.preferences is not None else None
        if isinstance(value, str) and value and Path(value).is_dir():
            return Path(value)
        docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        return Path(docs) if docs else Path.home()

    def save_pdf_dialog(self) -> Path | None:
        default = self._default_dir() / f"{self.document.file_stem}.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "PDF로 저장", str(default), "PDF 파일 (*.pdf)")
        return self.save_to(Path(path)) if path else None

    def save_to(self, path: Path) -> Path | None:
        """대화상자 없이 저장(테스트에서도 사용). 실패하면 안내하고 None."""
        try:
            saved = document_output.save_pdf(self.document, path)
        except OSError:
            log.exception("document pdf save failed")
            feedback.warn(self, SAVE_FAILED, "저장하지 못했습니다")
            return None
        if self.preferences is not None:
            feedback.run(self, lambda: self.preferences.set(PREF_KEY, str(saved.parent)))
        self.saved_path = saved
        feedback.info(self, f"{self.document.title}를 PDF로 저장했습니다.\n{saved}")
        return saved

    def print_dialog(self) -> bool:
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName(self.document.file_stem)
        dlg = QPrintDialog(printer, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        return feedback.run(self, lambda: document_output.print_document(self.document, printer) or True) is not None


def open_document(parent: QWidget, ctx: AppContext, order_id: int, kind: DocumentKind) -> DocumentDialog | None:
    """문서를 만들어 미리보기 창을 연다. 만들 수 없으면(상태 등) 안내만 하고 None."""
    document = feedback.run(parent, lambda: ctx.documents.build(order_id, kind))
    if document is None:
        return None
    dlg = DocumentDialog(document, parent, preferences=ctx.preferences)
    dlg.exec()
    return dlg
