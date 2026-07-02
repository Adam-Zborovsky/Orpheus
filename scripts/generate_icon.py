"""Generate the Orpheus app icon: orpheus/assets/orpheus.ico + orpheus.png.

Paints a mic badge with Qt at each standard size and packs them into a single
multi-size .ico (PNG-compressed entries, Vista+). Run after design changes:

    .venv\\Scripts\\python scripts\\generate_icon.py
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QApplication

SIZES = (16, 24, 32, 48, 64, 128, 256)
ASSETS = Path(__file__).resolve().parent.parent / "orpheus" / "assets"


def draw_icon(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    cx = s / 2

    # Badge: dark navy gradient disc with a faint inner ring.
    gradient = QLinearGradient(0, 0, 0, s)
    gradient.setColorAt(0.0, QColor("#2a3050"))
    gradient.setColorAt(1.0, QColor("#0d0f18"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(gradient)
    p.drawEllipse(QRectF(0, 0, s, s))
    ring = max(1.0, s / 64)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 26), ring))
    p.drawEllipse(QRectF(ring / 2, ring / 2, s - ring, s - ring))

    # Mic capsule (off-white, matches the pill bars).
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(238, 240, 245))
    cap_w, cap_h = 0.26 * s, 0.38 * s
    p.drawRoundedRect(QRectF(cx - cap_w / 2, 0.17 * s, cap_w, cap_h),
                      cap_w / 2, cap_w / 2)

    # Holder arc in the accent green (same green as the pill's "Done").
    stroke = max(1.0, 0.05 * s)
    p.setBrush(Qt.BrushStyle.NoBrush)
    pen = QPen(QColor(150, 235, 170), stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    arc_r = 0.225 * s
    p.drawArc(QRectF(cx - arc_r, 0.44 * s - arc_r, 2 * arc_r, 2 * arc_r),
              200 * 16, 140 * 16)

    # Stem + base (off-white).
    pen = QPen(QColor(238, 240, 245), stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QRectF(cx, 0.665 * s, 0, 0.09 * s).topLeft(),
               QRectF(cx, 0.665 * s, 0, 0.09 * s).bottomLeft())
    p.drawLine(QRectF(cx - 0.09 * s, 0.79 * s, 0.18 * s, 0).topLeft(),
               QRectF(cx - 0.09 * s, 0.79 * s, 0.18 * s, 0).topRight())
    p.end()
    return image


def png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def write_ico(path: Path, images: dict[int, bytes]) -> None:
    """Pack PNG blobs into an .ico container (ICONDIR + ICONDIRENTRY table)."""
    entries, blobs = [], []
    offset = 6 + 16 * len(images)
    for size, blob in sorted(images.items()):
        entries.append(struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0, size if size < 256 else 0,
            0, 0, 1, 32, len(blob), offset))
        blobs.append(blob)
        offset += len(blob)
    path.write_bytes(b"".join(
        [struct.pack("<HHH", 0, 1, len(images))] + entries + blobs))


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    ASSETS.mkdir(parents=True, exist_ok=True)
    rendered = {size: draw_icon(size) for size in SIZES}
    write_ico(ASSETS / "orpheus.ico",
              {size: png_bytes(img) for size, img in rendered.items()})
    rendered[256].save(str(ASSETS / "orpheus.png"), "PNG")
    print(f"wrote {ASSETS / 'orpheus.ico'} ({len(SIZES)} sizes) and orpheus.png")


if __name__ == "__main__":
    main()
