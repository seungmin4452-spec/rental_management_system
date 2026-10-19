"""Crop raw screenshots for the Kmong images; also resize final images."""
import sys
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QGuiApplication, QImage

app = QGuiApplication([])
mode = sys.argv[1]

if mode == "crop":
    src, dst = Path(sys.argv[2]), Path(sys.argv[3])
    dst.mkdir(parents=True, exist_ok=True)
    crops = {
        "dashboard": (0, 0, 2880, 1100),
        "calendar": (0, 0, 2880, 1800),
        "return_dialog": (0, 0, 2250, 840),
        "rental_doc": (0, 0, 1845, 1560),
    }
    for name, (x, y, w, h) in crops.items():
        img = QImage(str(src / f"{name}.png"))
        img.copy(QRect(x, y, w, h)).save(str(dst / f"{name}.png"))
        print("cropped", name, img.width(), img.height())
elif mode == "trim":
    # Cut the magenta filler below the section, then scale 2x render to 860 wide.
    src, dst = sys.argv[2], sys.argv[3]
    img = QImage(src)
    x = img.width() // 2
    h = img.height()
    while h > 1 and img.pixelColor(x, h - 1).name() == "#ff00ff":
        h -= 1
    out = img.copy(0, 0, img.width(), h)
    out = out.scaledToWidth(860, Qt.TransformationMode.SmoothTransformation)
    out.save(dst)
    print("trimmed", dst, out.width(), out.height())
elif mode == "resize":
    src, dst, w, h = sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
    img = QImage(src).scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    img.save(dst)
    print("resized", dst, img.width(), img.height())
