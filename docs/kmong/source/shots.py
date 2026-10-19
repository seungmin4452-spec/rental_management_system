"""Capture RentalDesk pages with demo data for Kmong images."""
import os
import sys
import tempfile
from pathlib import Path

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
scale = os.environ.get("SHOT_SCALE", "1.5")
os.environ["QT_SCALE_FACTOR"] = scale

from PySide6.QtWidgets import QApplication, QFrame  # noqa: E402

from rentdesk.app import create_demo_database  # noqa: E402
from rentdesk.context import AppContext  # noqa: E402
from rentdesk.ui.main_window import MainWindow, PAGES  # noqa: E402
from rentdesk.ui.theme import apply_theme  # noqa: E402

app = QApplication([])
apply_theme(app, dark=False, follow_system=False)
work = tempfile.mkdtemp()
conn = create_demo_database(Path(work) / "demo.db")
win = MainWindow(AppContext(conn, is_demo=True))
win.setMinimumSize(100, 100)
win.resize(1280, 800)
# Hide the demo / backup banners so screenshots show the app itself.
for f in win.findChildren(QFrame):
    if f.property("banner"):
        f.hide()
win.show()
app.processEvents()
for key, label, _ in PAGES:
    win.goto(key)
    app.processEvents()
    win.grab().save(str(out / f"{key}.png"))
    print("saved", key)
from rentdesk.ui.dialogs.document_dialog import DocumentDialog  # noqa: E402
from rentdesk.ui.dialogs.return_dialog import ReturnDialog  # noqa: E402
from rentdesk.ui.dialogs.reservation_wizard import ReservationWizard  # noqa: E402

ctx = win.ctx
for order_no, status in conn.execute("SELECT order_no, status FROM rental_orders ORDER BY id"):
    print(order_no, status)
oid = conn.execute(
    "SELECT id FROM rental_orders WHERE status='IN_RENTAL' ORDER BY id LIMIT 1").fetchone()[0]


def snap(widget, name, w, h):
    widget.resize(w, h)
    widget.show()
    app.processEvents()
    widget.grab().save(str(out / f"{name}.png"))
    widget.close()
    print("saved", name)


snap(ReturnDialog(ctx, oid, win), "return_dialog", 1000, 720)
snap(DocumentDialog(ctx.documents.rental_confirmation(oid), win), "rental_doc", 820, 900)
snap(ReservationWizard(ctx, parent=win), "wizard", 1000, 640)
win.close()
conn.close()
