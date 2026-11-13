"""Visible signature block: layout, PDF vector output and on-screen painting.

The block is rendered with vector glyph outlines, so the appearance stream does
not depend on fonts installed on the viewing machine and Cyrillic text looks
identical in every PDF reader.
"""
from __future__ import annotations

from dataclasses import dataclass, field

App = None  # lazily created QGuiApplication, retained to keep Qt alive


@dataclass
class StampContent:
    title: str = "ДОКУМЕНТ ПОДПИСАН ЭЛЕКТРОННОЙ ПОДПИСЬЮ"
    name: str = ""
    position: str = ""
    organization: str = ""
    validity: str = ""
    signed_at: str = ""
    serial: str = ""
    accent: tuple[int, int, int] = (0x1D, 0x4E, 0xD8)
    text: tuple[int, int, int] = (0x1C, 0x1B, 0x1F)
    muted: tuple[int, int, int] = (0x5F, 0x63, 0x68)


@dataclass
class TextItem:
    text: str
    x: float
    baseline: float
    size: float
    bold: bool
    color: tuple[int, int, int]


@dataclass
class RectItem:
    x: float
    y: float
    width: float
    height: float
    color: tuple[int, int, int]
    radius: float = 0.0


@dataclass
class LineItem:
    x1: float
    y1: float
    x2: float
    y2: float
    color: tuple[int, int, int]
    width: float = 0.6


@dataclass
class StampLayout:
    width: float
    height: float
    texts: list[TextItem] = field(default_factory=list)
    rects: list[RectItem] = field(default_factory=list)
    lines: list[LineItem] = field(default_factory=list)


FONT_FAMILY = "Segoe UI"


def ensure_qt_app():
    """Return a QGuiApplication instance, creating one when running headless."""
    global App
    from PySide6.QtGui import QGuiApplication

    instance = QGuiApplication.instance()
    if instance is not None:
        return instance
    if App is None:
        App = QGuiApplication([])
    return App


def _font(size: float, bold: bool):
    from PySide6.QtGui import QFont

    font = QFont(FONT_FAMILY)
    font.setPixelSize(max(1, int(round(size))))
    font.setBold(bold)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


def measure_text(text: str, size: float, bold: bool = False) -> float:
    from PySide6.QtGui import QFontMetricsF

    if not text:
        return 0.0
    ensure_qt_app()
    return QFontMetricsF(_font(size, bold)).horizontalAdvance(text)


def _fit_size(text: str, size: float, bold: bool, max_width: float) -> float:
    if max_width <= 1 or not text:
        return size
    width = measure_text(text, size, bold)
    if width <= max_width:
        return size
    return max(4.0, size * max_width / width)


def _ellipsize(text: str, size: float, bold: bool, max_width: float) -> str:
    if measure_text(text, size, bold) <= max_width:
        return text
    result = text
    while len(result) > 1 and measure_text(result + "…", size, bold) > max_width:
        result = result[:-1]
    return result.rstrip() + "…"


def _line_height(size: float) -> float:
    return size * 1.32


def layout_stamp(width: float, height: float, content: StampContent) -> StampLayout:
    """Build a layout for the given box size (PDF points, top-left origin)."""
    ensure_qt_app()
    layout = StampLayout(width=max(1.0, width), height=max(1.0, height))
    pad = min(9.0, max(4.0, min(width, height) * 0.09))
    bar = max(2.2, min(3.4, width * 0.012))
    inner_x = pad + bar + 5.0
    inner_w = layout.width - inner_x - pad
    if inner_w < 10:
        inner_x = pad
        inner_w = max(10.0, layout.width - 2 * pad)

    layout.rects.append(RectItem(0, 0, layout.width, layout.height, (255, 255, 255), 3.0))
    layout.rects.append(RectItem(0, 0, layout.width, layout.height, (255, 255, 255), 3.0))
    layout.rects.append(RectItem(0, 0, bar, layout.height, content.accent, 0.0))

    candidates: list[tuple[str, float, bool, tuple[int, int, int], float]] = []
    candidates.append((content.title, 5.4, True, content.muted, 0))
    if content.name:
        candidates.append((content.name, 9.2, True, content.text, 2))
    if content.position:
        candidates.append((content.position, 6.6, False, content.text, 1))
    if content.organization:
        candidates.append((content.organization, 6.6, False, content.text, 0))
    meta: list[str] = []
    if content.validity:
        meta.append(content.validity)
    if content.signed_at:
        meta.append(content.signed_at)
    if content.serial:
        meta.append(content.serial)
    for index, line in enumerate(meta[:2]):
        candidates.append((line, 5.5, False, content.muted, 3 if index == 0 else 1))

    available = layout.height - 2 * pad
    total = sum(size * 1.32 + gap for _, size, _, _, gap in candidates)
    scale = 1.0
    if total > available and total > 0:
        scale = max(0.62, available / total)

    # Drop the lowest-priority meta lines when even the shrunk text does not fit.
    while len(candidates) > 2:
        total = sum(size * scale * 1.32 + gap for _, size, _, _, gap in candidates)
        if total <= available:
            break
        candidates.pop()

    cursor = pad
    for text, size, bold, color, gap in candidates:
        cursor += gap * scale
        size = size * scale
        if cursor + size * 1.05 > layout.height - pad:
            break
        fitted = _fit_size(text, size, bold, inner_w)
        text_value = _ellipsize(text, fitted, bold, inner_w)
        layout.texts.append(
            TextItem(text_value, inner_x, cursor + size * 0.92, fitted, bold, color)
        )
        cursor += size * 1.32
    return layout


def _color_ops(color: tuple[int, int, int], stroke: bool = False) -> str:
    red, green, blue = (component / 255.0 for component in color)
    operator = "RG" if stroke else "rg"
    return f"{red:.4f} {green:.4f} {blue:.4f} {operator}\n"


def _rounded_rect_path(x: float, y: float, width: float, height: float, radius: float) -> str:
    radius = max(0.0, min(radius, min(width, height) / 2.0))
    if radius <= 0.01:
        return f"{x:.3f} {y:.3f} {width:.3f} {height:.3f} re\n"
    k = radius * 0.5522847498
    x0, y0, x1, y1 = x, y, x + width, y + height
    parts = [f"{x0 + radius:.3f} {y0:.3f} m\n"]
    parts.append(f"{x1 - radius:.3f} {y0:.3f} l\n")
    parts.append(f"{x1 - radius + k:.3f} {y0:.3f} {x1:.3f} {y0 + radius - k:.3f} "
                 f"{x1:.3f} {y0 + radius:.3f} c\n")
    parts.append(f"{x1:.3f} {y1 - radius:.3f} l\n")
    parts.append(f"{x1:.3f} {y1 - radius + k:.3f} {x1 - radius + k:.3f} {y1:.3f} "
                 f"{x1 - radius:.3f} {y1:.3f} c\n")
    parts.append(f"{x0 + radius:.3f} {y1:.3f} l\n")
    parts.append(f"{x0 + radius - k:.3f} {y1:.3f} {x0:.3f} {y1 - radius + k:.3f} "
                 f"{x0:.3f} {y1 - radius:.3f} c\n")
    parts.append(f"{x0:.3f} {y0 + radius:.3f} l\n")
    parts.append(f"{x0:.3f} {y0 + radius - k:.3f} {x0 + radius - k:.3f} {y0:.3f} "
                 f"{x0 + radius:.3f} {y0:.3f} c\nh\n")
    return "".join(parts)


def _glyph_path(text: str, size: float, bold: bool):
    from PySide6.QtGui import QPainterPath

    path = QPainterPath()
    path.addText(0.0, 0.0, _font(size, bold), text)
    return path


def to_pdf_content(layout: StampLayout) -> bytes:
    """Serialize the layout into a PDF form-XObject content stream."""
    ensure_qt_app()
    chunks: list[str] = []

    for rect in layout.rects:
        # Layout uses a top-left origin; PDF user space has y growing upwards.
        y = layout.height - rect.y - rect.height
        chunks.append(_color_ops(rect.color))
        chunks.append(_rounded_rect_path(rect.x, y, rect.width, rect.height, rect.radius))
        chunks.append("f\n")

    for line in layout.lines:
        chunks.append(_color_ops(line.color, stroke=True))
        chunks.append(f"{line.width:.3f} w 1 J\n")
        chunks.append(
            f"{line.x1:.3f} {layout.height - line.y1:.3f} m "
            f"{line.x2:.3f} {layout.height - line.y2:.3f} l S\n"
        )

    for item in layout.texts:
        path = _glyph_path(item.text, item.size, item.bold)
        polygons = path.toSubpathPolygons()
        if not polygons:
            continue
        chunks.append(_color_ops(item.color))
        for polygon in polygons:
            points = list(polygon)
            if len(points) < 2:
                continue
            first = points[0]
            chunks.append(
                f"{item.x + first.x():.3f} {layout.height - item.baseline - first.y():.3f} m\n"
            )
            for point in points[1:]:
                chunks.append(
                    f"{item.x + point.x():.3f} "
                    f"{layout.height - item.baseline - point.y():.3f} l\n"
                )
            chunks.append("h\n")
        chunks.append("f*\n")
    return "".join(chunks).encode("ascii")


def paint_layout(painter, layout: StampLayout, scale: float = 1.0) -> None:
    """Paint the same layout with QPainter (used for the on-screen preview)."""
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QColor, QFont, QPainterPath, QPen

    painter.save()
    painter.scale(scale, scale)
    painter.setRenderHint(painter.RenderHint.Antialiasing, True)
    painter.setRenderHint(painter.RenderHint.TextAntialiasing, True)

    for rect in layout.rects:
        color = QColor(*rect.color)
        painter.setPen(QPen(color, 0.7) if rect.radius else QPen(QColor(0, 0, 0, 26), 0.7))
        painter.setBrush(color)
        painter.drawRoundedRect(
            QRectF(rect.x, rect.y, rect.width, rect.height), rect.radius, rect.radius
        )

    for line in layout.lines:
        painter.setPen(QPen(QColor(*line.color), line.width))
        painter.drawLine(QPointF(line.x1, line.y1), QPointF(line.x2, line.y2))

    for item in layout.texts:
        font = QFont(FONT_FAMILY)
        font.setPixelSize(max(1, int(round(item.size))))
        font.setBold(item.bold)
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        painter.setFont(font)
        painter.setPen(QColor(*item.color))
        painter.drawText(QPointF(item.x, item.baseline), item.text)
    painter.restore()


def make_layout_from_certificate(
    width: float, height: float, certificate, signed_at: str, *, title: str = ""
) -> StampLayout:
    content = StampContent()
    if title:
        content.title = title
    content.name = certificate.display_name
    content.position = certificate.position
    content.organization = certificate.organization
    content.validity = (
        f"Сертификат действителен: {certificate.validity_text}" if certificate.validity_text else ""
    )
    content.signed_at = f"Подписано: {signed_at}" if signed_at else ""
    content.serial = f"Серийный номер: {certificate.serial}" if certificate.serial else ""
    return layout_stamp(width, height, content)
