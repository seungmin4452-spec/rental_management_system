#!/usr/bin/env python3
"""RentDesk Lite 앱 아이콘 생성기 (Qt로 직접 그려 재현 가능하게 유지).

디자인: 파란 둥근 사각 타일 위에 흰 달력 카드(주황 머리띠·고리 2개)와 초록 체크 표시
→ "예약 확정·대여 일정 관리". 작은 크기(16px)에서도 알아볼 수 있도록 형태를 단순하게 둔다.

출력
  src/rentdesk/resources/icons/app.png      앱 실행 중 창 아이콘(256px)
  packaging/icons/RentDeskLite_1024.png      원본
  packaging/macos/RentDeskLite.icns          macOS(iconutil 필요, macOS에서만 생성)
  packaging/windows/RentDeskLite.ico         Windows(16~256px, PNG 압축 ICO)

  QT_QPA_PLATFORM=offscreen python scripts/make_icons.py
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

ROOT = Path(__file__).resolve().parent.parent
BLUE_TOP = QColor("#3B82F6")
BLUE_BOTTOM = QColor("#1D4ED8")
ORANGE = QColor("#F97316")
GREEN = QColor("#16A34A")
CARD = QColor("#FFFFFF")
RING = QColor("#1E3A8A")


def render(size: int, *, margin_ratio: float) -> QImage:
    """`margin_ratio`: 타일 바깥 여백 비율. macOS는 Apple 격자(약 10%), Windows는 좁게."""
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)

    # 1) 둥근 사각 타일 + 세로 그라데이션
    m = s * margin_ratio
    tile = QRectF(m, m, s - 2 * m, s - 2 * m)
    radius = tile.width() * 0.225
    grad = QLinearGradient(tile.topLeft(), tile.bottomLeft())
    grad.setColorAt(0, BLUE_TOP)
    grad.setColorAt(1, BLUE_BOTTOM)
    path = QPainterPath()
    path.addRoundedRect(tile, radius, radius)
    p.fillPath(path, grad)

    # 2) 달력 카드
    t = tile.width()
    card = QRectF(tile.left() + t * 0.19, tile.top() + t * 0.24, t * 0.62, t * 0.58)
    card_r = t * 0.07
    shadow = QPainterPath()
    shadow.addRoundedRect(card.translated(0, t * 0.02), card_r, card_r)
    p.fillPath(shadow, QColor(0, 0, 0, 45))
    body = QPainterPath()
    body.addRoundedRect(card, card_r, card_r)
    p.fillPath(body, CARD)

    # 머리띠(주황): 카드 위쪽만 둥글게 보이도록 카드 모양으로 잘라 그린다
    header = QRectF(card.left(), card.top(), card.width(), card.height() * 0.24)
    p.save()
    p.setClipPath(body)
    p.fillRect(header, ORANGE)
    p.restore()

    # 고리 2개
    ring_w, ring_h = t * 0.055, t * 0.14
    for fx in (0.3, 0.7):
        cx = card.left() + card.width() * fx
        ring = QRectF(cx - ring_w / 2, card.top() - ring_h * 0.45, ring_w, ring_h)
        rp = QPainterPath()
        rp.addRoundedRect(ring, ring_w / 2, ring_w / 2)
        p.fillPath(rp, RING)

    # 3) 체크 표시(초록)
    pen = QPen(GREEN, t * 0.085, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    area_top = header.bottom()
    area_h = card.bottom() - area_top
    check = QPainterPath()
    check.moveTo(QPointF(card.left() + card.width() * 0.27, area_top + area_h * 0.52))
    check.lineTo(QPointF(card.left() + card.width() * 0.44, area_top + area_h * 0.72))
    check.lineTo(QPointF(card.left() + card.width() * 0.74, area_top + area_h * 0.30))
    p.drawPath(check)
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(data)


def write_ico(path: Path, images: list[QImage]) -> None:
    """PNG 압축 항목을 담은 ICO(Windows Vista 이상 지원)."""
    entries = [(img.width(), png_bytes(img)) for img in images]
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    directory, blobs = b"", b""
    for size, data in entries:
        dim = 0 if size >= 256 else size  # 0은 256을 뜻함
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    path.write_bytes(header + directory + blobs)


def write_icns(path: Path) -> bool:
    if sys.platform != "darwin" or shutil.which("iconutil") is None:
        print("iconutil 없음(macOS 전용) → .icns 생략")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "RentDeskLite.iconset"
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            render(base, margin_ratio=0.1).save(str(iconset / f"icon_{base}x{base}.png"))
            render(base * 2, margin_ratio=0.1).save(str(iconset / f"icon_{base}x{base}@2x.png"))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(path)], check=True)
    return True


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 - 그리기에 필요
    icons_dir = ROOT / "packaging" / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)
    render(1024, margin_ratio=0.1).save(str(icons_dir / "RentDeskLite_1024.png"))

    runtime = ROOT / "src" / "rentdesk" / "resources" / "icons"
    runtime.mkdir(parents=True, exist_ok=True)
    render(256, margin_ratio=0.04).save(str(runtime / "app.png"))

    write_ico(ROOT / "packaging" / "windows" / "RentDeskLite.ico",
              [render(n, margin_ratio=0.04) for n in (16, 24, 32, 48, 64, 128, 256)])
    write_icns(ROOT / "packaging" / "macos" / "RentDeskLite.icns")
    print("아이콘 생성 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
