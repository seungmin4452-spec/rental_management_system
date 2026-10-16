"""앱 아이콘 리소스: 실행 중 창 아이콘(PNG), Windows .ico, macOS .icns."""

import struct
from pathlib import Path

from PySide6.QtGui import QImage

from rentdesk.platform.resources import get_app_icon_path

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_icon_png_loads():
    img = QImage(str(get_app_icon_path()))
    assert not img.isNull() and img.width() == img.height() == 256


def test_windows_ico_has_all_sizes():
    data = (ROOT / "packaging" / "windows" / "RentalDesk.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1)
    sizes = {data[6 + 16 * i] or 256 for i in range(count)}
    assert {16, 32, 48, 256} <= sizes


def test_macos_icns_present():
    assert (ROOT / "packaging" / "macos" / "RentalDesk.icns").read_bytes()[:4] == b"icns"
