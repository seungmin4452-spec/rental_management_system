#!/usr/bin/env python3
"""설치 안내서(docs/customer/INSTALL_GUIDE.md)의 앱 화면 그림 6·8·9를 만든다.

실제 PC의 기기 코드·데이터가 그림에 들어가지 않도록 가짜 기기와 임시 서명 키, 임시 폴더만 쓴다.
(임시 키로 만든 라이선스는 이 스크립트 안에서만 쓰이고 저장되지 않는다.)

  python scripts/make_guide_shots.py            # → docs/customer/images/06_license.png …
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
# Windows에서는 실제 화면 방식으로 그린다(오프스크린은 글꼴·줄바꿈 높이가 실제와 달라 글자가 겹침).
# 창이 잠깐 화면에 나타났다 닫힌다.
os.environ.setdefault("QT_QPA_PLATFORM", "windows" if sys.platform == "win32" else "offscreen")

from nacl.signing import SigningKey  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rentdesk.app import bootstrap_database  # noqa: E402
from rentdesk.context import AppContext  # noqa: E402
from rentdesk.platform.device_id import FakeDeviceIdProvider  # noqa: E402
from rentdesk.services.licensing import LicenseService, issue_license  # noqa: E402
from rentdesk.ui import theme  # noqa: E402
from rentdesk.ui.dialogs.license_dialog import LicenseDialog  # noqa: E402
from rentdesk.ui.dialogs.starter_dialog import StarterDialog  # noqa: E402
from rentdesk.ui.main_window import MainWindow  # noqa: E402

OUT = ROOT / "docs" / "customer" / "images"


def _shot(widget, name: str) -> None:
    widget.show()
    QApplication.processEvents()
    widget.grab().save(str(OUT / name))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    app = QApplication.instance() or QApplication([])
    theme.apply_theme(app, dark=False, follow_system=False)
    OUT.mkdir(parents=True, exist_ok=True)
    key = SigningKey.generate()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        service = LicenseService(tmp_path / "license", FakeDeviceIdProvider("GUIDE-SAMPLE-PC"),
                                 public_key_hex=key.verify_key.encode().hex())
        dlg = LicenseDialog(service, backup_real=lambda _p: None)
        dlg.resize(620, dlg.sizeHint().height())
        _shot(dlg, "06_license.png")
        dlg.close()

        lic = tmp_path / "RentalDesk_OO렌탈.json"
        lic.write_text(issue_license(key, machine_code=service.machine_code(), customer_label="OO렌탈",
                                     issued_at=datetime.now(UTC)), encoding="utf-8")
        service.install(lic)

        conn = bootstrap_database(tmp_path / "rentdesk.db", tmp_path / "backups")
        window = MainWindow(AppContext(conn, license=service))
        window.resize(1280, 800)
        _shot(window, "08_main_licensed.png")
        starter = StarterDialog(window.ctx, window.ctx.starter.kinds(), window, first_run=True)
        _shot(starter, "09_starter.png")
        starter.close()
        window.close()
        conn.close()
    app.processEvents()
    for p in sorted(OUT.glob("0*.png")):
        print(p.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
