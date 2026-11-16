"""Generate the combo-box chevron icons (normal / disabled) into resources/."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT / "vendor" / "win32"))
sys.path.insert(0, str(ROOT / "vendor" / "win32" / "lib"))

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen  # noqa: E402


def chevron(color: str, size: int = 48) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.moveTo(QPointF(size * 0.20, size * 0.36))
    path.lineTo(QPointF(size * 0.50, size * 0.66))
    path.lineTo(QPointF(size * 0.80, size * 0.36))
    pen = QPen(QColor(color))
    pen.setWidthF(size * 0.11)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.end()
    return image


def main() -> int:
    QGuiApplication([sys.argv[0]])
    out = ROOT / "resources"
    out.mkdir(exist_ok=True)
    targets = {
        "chevron-down.png": "#475569",          # ON_SURFACE_VARIANT
        "chevron-down-disabled.png": "#C3D0E4",  # OUTLINE_VARIANT
    }
    for name, color in targets.items():
        path = out / name
        chevron(color).save(str(path), "PNG")
        print("written", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
