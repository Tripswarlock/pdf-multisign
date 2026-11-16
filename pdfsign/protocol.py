"""Optional signing protocol (a printable PDF listing every signature)."""
from __future__ import annotations

import datetime as dt
import os

FONT_CANDIDATES = (
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
)

BOLD_CANDIDATES = (
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
)


def _first_existing(paths) -> str:
    for path in paths:
        if os.path.isfile(path):
            return path
    return ""


def _font_names():
    """(regular, bold) font paths; empty string means the built-in font."""
    return _first_existing(FONT_CANDIDATES), _first_existing(BOLD_CANDIDATES)


def build_protocol(
    *,
    doc_name: str,
    mode_label: str,
    session_code: str,
    started_at: str,
    participants: list[dict],
    sig_names: dict[str, str],
    finished_at: str = "",
) -> bytes:
    """Render a one-page signing protocol and return PDF bytes."""
    import pymupdf

    regular, bold = _font_names()
    font_regular = ("F0", regular) if regular else ("helv", "")
    font_bold = ("F1", bold or regular) if (bold or regular) else ("hebo", "")

    document = pymupdf.open()
    page = document.new_page(width=595, height=842)  # A4

    def text(x: float, y: float, value: str, size: float = 10, bold_face: bool = False):
        name, path = font_bold if bold_face else font_regular
        page.insert_text(
            (x, y), value, fontsize=size, fontname=name,
            **({"fontfile": path} if path else {}),
        )

    text(48, 64, "ПРОТОКОЛ ПОДПИСАНИЯ ДОКУМЕНТА", 15, True)
    text(48, 92, f"Документ: {doc_name}", 11)
    text(48, 110, f"Схема: {mode_label}", 10)
    text(48, 126, f"Код сессии: {session_code}", 10)
    text(48, 142, f"Начало сессии: {started_at}", 10)
    if finished_at:
        text(48, 158, f"Завершение: {finished_at}", 10)

    top = 186
    text(48, top, "№", 10, True)
    text(74, top, "Участник", 10, True)
    text(230, top, "Организация / должность", 10, True)
    text(430, top, "Статус", 10, True)
    page.draw_line(pymupdf.Point(48, top + 5), pymupdf.Point(547, top + 5))

    row_height = 34
    y = top + 24
    for index, item in enumerate(participants, start=1):
        if y > 760:
            page = document.new_page(width=595, height=842)
            y = 80
        text(48, y, str(index))
        text(74, y, str(item.get("name") or "—")[:38])
        organization = " / ".join(
            part for part in (item.get("organization"), item.get("position")) if part
        )
        text(230, y, organization[:44] or "—", 9)
        status = str(item.get("status_label") or "")
        sig_file = sig_names.get(str(item.get("id") or ""), "")
        if sig_file:
            status = f"{status}"
        text(430, y, status[:30], 9)
        if sig_file:
            text(74, y + 12, f"файл подписи: {sig_file}", 8)
        time_text = str(item.get("signed_at") or item.get("joined_at") or "")
        if time_text:
            text(430, y + 12, time_text, 8)
        page.draw_line(pymupdf.Point(48, y + 18), pymupdf.Point(547, y + 18))
        y += row_height

    text(48, 800, "Подписи являются отсоединёнными (CAdES-BES) и хранятся в файлах .sig.", 9)
    text(48, 816, "Проверка выполняется штатными средствами CryptoPro CSP (cryptcp -vsign).", 9)
    text(48, 832, f"Сформировано: {dt.datetime.now():%d.%m.%Y %H:%M}", 9)

    try:  # keep the package small: embed only the used glyphs
        document.subset_fonts()
    except Exception:
        pass
    data = document.tobytes(garbage=4, deflate=True)
    document.close()
    return data
