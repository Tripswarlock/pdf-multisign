"""Generate the application icon (PNG + ICO) with Qt, no external assets."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT / "vendor" / "win32"))
sys.path.insert(0, str(ROOT / "vendor" / "win32" / "lib"))

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

BLUE = QColor("#1D4ED8")
BLUE_DARK = QColor("#1E40AF")
WHITE = QColor("#FFFFFF")


def render(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    margin = size * 0.06
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, BLUE)
    gradient.setColorAt(1.0, BLUE_DARK)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(rect, size * 0.22, size * 0.22)

    # A signature-like stroke.
    stroke = QPainterPath()
    stroke.moveTo(size * 0.22, size * 0.66)
    stroke.cubicTo(size * 0.34, size * 0.28, size * 0.44, size * 0.86, size * 0.56, size * 0.52)
    stroke.cubicTo(size * 0.64, size * 0.28, size * 0.70, size * 0.62, size * 0.80, size * 0.44)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(WHITE, max(2.0, size * 0.055), Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.drawPath(stroke)

    # Underline.
    painter.setPen(QPen(QColor(255, 255, 255, 200), max(1.5, size * 0.022)))
    painter.drawLine(QPointF(size * 0.24, size * 0.76), QPointF(size * 0.76, size * 0.76))

    painter.end()
    return image


def main() -> int:
    QGuiApplication([sys.argv[0]])
    resources = ROOT / "resources"
    resources.mkdir(exist_ok=True)

    png_path = resources / "pdf-multisign.png"
    icon = render(512)
    icon.scaled(256, 256, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation).save(str(png_path), "PNG")
    print("written", png_path)

    ico_path = resources / "pdf-multisign.ico"
    from PIL import Image  # bundled with the workspace Python

    image = Image.open(png_path)
    image.save(ico_path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("written", ico_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
