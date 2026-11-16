"""Generate round radio-button indicators (off/on/hover/disabled) for the theme."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT / "vendor" / "win32"))
sys.path.insert(0, str(ROOT / "vendor" / "win32" / "lib"))

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402

SIZE = 40            # rendered pixels (displayed at 20 px)
RING = 4             # ring width in rendered pixels (~2 px on screen)
PRIMARY = "#1D4ED8"
NEUTRAL = "#475569"
DISABLED = "#C3D0E4"


def indicator(ring_color: str, dot: bool) -> QImage:
    image = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)

    # outer ring as a filled circle with a transparent hole, so the inner dot
    # is painted *inside* the ring instead of thickening the border
    painter.setBrush(QColor(ring_color))
    painter.drawEllipse(QPointF(SIZE / 2, SIZE / 2), SIZE / 2 - 1, SIZE / 2 - 1)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    painter.drawEllipse(
        QPointF(SIZE / 2, SIZE / 2), SIZE / 2 - 1 - RING, SIZE / 2 - 1 - RING
    )
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

    if dot:
        inner = (SIZE / 2 - 1 - RING) * 0.62
        painter.setBrush(QColor(ring_color))
        painter.drawEllipse(QPointF(SIZE / 2, SIZE / 2), inner, inner)

    painter.end()
    return image


def main() -> int:
    QGuiApplication([sys.argv[0]])
    out = ROOT / "resources"
    out.mkdir(exist_ok=True)
    variants = {
        "radio-off.png": (NEUTRAL, False),
        "radio-off-hover.png": (PRIMARY, False),
        "radio-on.png": (PRIMARY, True),
        "radio-off-disabled.png": (DISABLED, False),
        "radio-on-disabled.png": (DISABLED, True),
    }
    for name, (color, dot) in variants.items():
        path = out / name
        indicator(color, dot).save(str(path), "PNG")
        print("written", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
