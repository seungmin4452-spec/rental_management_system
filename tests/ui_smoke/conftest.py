import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rentdesk.context import AppContext  # noqa: E402
from rentdesk.persistence.db import open_database  # noqa: E402
from rentdesk.ui import feedback  # noqa: E402
from tests.support import FakeClock, Services, kst  # noqa: E402


class RecordingHandler:
    """메시지 상자를 띄우지 않고 기록한다. confirm 응답은 `answer`로 지정."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []
        self.answer = True

    def info(self, parent, title, text):
        self.messages.append(("info", title, text))

    def warn(self, parent, title, text):
        self.messages.append(("warn", title, text))

    def confirm(self, parent, title, text):
        self.messages.append(("confirm", title, text))
        return self.answer

    def texts(self, kind: str) -> list[str]:
        return [t for k, _, t in self.messages if k == kind]


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ui(qapp):
    handler = RecordingHandler()
    previous = feedback.set_handler(handler)
    yield handler
    feedback.set_handler(previous)


@pytest.fixture
def clock():
    return FakeClock(kst(10, 19, 9, 0))


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "ui.db")
    yield connection
    connection.close()


@pytest.fixture
def svc(conn, clock):
    return Services(conn, clock)


@pytest.fixture
def ctx(conn, clock, ui):
    return AppContext(conn, clock)


@pytest.fixture(autouse=True)
def opened_documents(monkeypatch):
    """확인서 미리보기 창(R6)을 모달로 띄우지 않고 기록한다(출고·반납 후 자동으로 열리므로)."""
    from rentdesk.ui.dialogs.document_dialog import DocumentDialog

    opened: list[DocumentDialog] = []

    def fake_exec(self):
        opened.append(self)
        return 1

    monkeypatch.setattr(DocumentDialog, "exec", fake_exec)
    return opened
