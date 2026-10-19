"""확인서·견적서 PDF 저장·인쇄 (R6). 새 의존성 없이 Qt(`QTextDocument` + `QPdfWriter`/`QPrinter`)만 쓴다.

글꼴은 OS에 설치된 한글 글꼴(Windows 맑은 고딕, macOS Apple SD Gothic Neo 등 — `theme.FONT_FAMILIES`)을
쓰고, PDF에는 Qt가 사용한 글자만 골라 글꼴을 함께 넣는다(받는 PC에 글꼴이 없어도 보이도록).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QSizeF
from PySide6.QtGui import QFont, QPageLayout, QPageSize, QPagedPaintDevice, QPdfWriter, QTextDocument

from rentdesk.domain.documents import Document, render_html
from rentdesk.ui import theme

PDF_RESOLUTION = 300
MARGIN_MM = 12
FONT_POINT_SIZE = 9.5


def text_document(doc: Document, device: QPagedPaintDevice | None = None) -> QTextDocument:
    """문서 HTML을 담은 `QTextDocument`. `device`를 주면 그 용지(여백 안쪽) 크기로 나눠 배치한다."""
    text = QTextDocument()
    font = QFont()
    font.setFamilies(theme.font_families())
    font.setPointSizeF(FONT_POINT_SIZE)
    text.setDefaultFont(font)
    text.setDocumentMargin(0)
    if device is not None:
        # 용지 크기를 직접 정해야 Qt가 기본 여백(2cm)·쪽 번호를 덧붙이지 않는다.
        text.documentLayout().setPaintDevice(device)
        rect = device.pageLayout().paintRectPixels(device.resolution())
        text.setPageSize(QSizeF(rect.width(), rect.height()))
    text.setHtml(render_html(doc))
    return text


def _a4_layout(device: QPagedPaintDevice) -> None:
    device.setPageLayout(QPageLayout(
        QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait,
        QMarginsF(MARGIN_MM, MARGIN_MM, MARGIN_MM, MARGIN_MM), QPageLayout.Unit.Millimeter,
    ))


def _print(doc: Document, device: QPagedPaintDevice) -> None:
    _a4_layout(device)
    text_document(doc, device).print_(device)


def pdf_bytes(doc: Document) -> bytes:
    """A4 PDF 내용. 파일 쓰기와 분리해 저장 실패(권한·디스크)를 OSError로 분명히 알린다."""
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QPdfWriter(buffer)
    writer.setResolution(PDF_RESOLUTION)
    writer.setTitle(f"{doc.title} {doc.order_no}")
    writer.setCreator("RentalDesk")
    _print(doc, writer)
    del writer  # 소멸 시 PDF 끝부분을 버퍼에 마저 쓴다
    buffer.close()
    return bytes(data.data())


def save_pdf(doc: Document, path: Path) -> Path:
    """PDF 파일로 저장. 확장자가 없으면 .pdf를 붙인다. 같은 폴더 임시 파일에 쓴 뒤 바꿔치기한다."""
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        path = path.with_name(path.name + ".pdf")
    content = pdf_bytes(doc)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(content)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def print_document(doc: Document, printer) -> None:
    """선택한 프린터(`QPrinter`)로 인쇄. 용지는 A4 세로로 맞춘다."""
    _print(doc, printer)
