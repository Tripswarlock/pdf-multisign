"""About / diagnostics dialog, mirroring the reference project."""
from __future__ import annotations

import datetime as dt
import platform
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
)

from . import __version__, theme
from . import crypto

APP_NAME = "PDF Мультиподпись"
AUTHOR = "Петровский Дмитрий"
AUTHOR_EMAIL = "tripswarlock@gmail.com"


def _components_html() -> str:
    rows = [
        ("Python", platform.python_version()),
        ("PySide6 / Qt", _qt_version()),
        ("PyMuPDF", _distribution_version("PyMuPDF")),
        ("pypdf", _distribution_version("pypdf")),
        ("asn1crypto", _distribution_version("asn1crypto")),
        ("gostcrypto", _distribution_version("gostcrypto")),
        ("pywin32", _distribution_version("pywin32") if sys.platform == "win32" else "—"),
    ]
    items = "".join(
        f"<tr><td style='color:{theme.ON_SURFACE_VARIANT};padding-right:14px'>{name}</td>"
        f"<td>{version or '—'}</td></tr>"
        for name, version in rows
    )
    return f"<table cellspacing='2'>{items}</table>"


def _qt_version() -> str:
    try:
        from PySide6 import QtCore

        return QtCore.qVersion()
    except Exception:
        return ""


def _distribution_version(name: str) -> str:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        pass
    try:
        module = __import__(name.lower().replace("pyside6", "PySide6"))
        return str(getattr(module, "__version__", "") or "")
    except Exception:
        return ""


def build_about_html() -> str:
    available, detail = crypto.crypto_status()
    today = dt.date.today().strftime("%d.%m.%Y")
    accent = theme.PRIMARY
    muted = theme.ON_SURFACE_VARIANT
    state_color = theme.SUCCESS if available else theme.ERROR
    crypto_note = detail.replace("&", "&amp;").replace("<", "&lt;")
    platform_text = f"{platform.system()} {platform.release()} · {platform.machine()}"

    return f"""
    <h2 style="margin:0 0 2px 0;">{APP_NAME}</h2>
    <p style="color:{muted};margin:0 0 14px 0;">
        Версия {__version__} · {today} · {platform_text}
    </p>

    <h3 style="margin-bottom:4px;">Автор</h3>
    <p style="margin-top:0;">
        <b>{AUTHOR}</b><br>
        Email: <a href="mailto:{AUTHOR_EMAIL}">{AUTHOR_EMAIL}</a>
    </p>

    <h3 style="margin-bottom:4px;">Что делает программа</h3>
    <p style="margin-top:0;">
        Подписывает PDF несколькими подписями CryptoPro CSP за один проход, добавляя
        каждую подпись отдельной ревизией файла. По желанию рисует видимый блок подписи.
    </p>

    <h3 style="margin-bottom:4px;">Технологии и сторонние компоненты</h3>
    {_components_html()}
    <p style="margin-top:8px;">
        <b>CryptoPro CSP 5.0</b> — проприетарная ГОСТ-криптография; устанавливается
        пользователем отдельно и не входит в поставку.<br>
        <b>Qt / PySide6</b> — LGPL-3.0. <b>PyMuPDF</b> — AGPL-3.0. <b>pypdf</b> — BSD-3-Clause.
    </p>

    <h3 style="margin-bottom:4px;">CryptoPro</h3>
    <p style="margin-top:0;">
        <b style="color:{state_color};">{'Доступен' if available else 'Недоступен'}</b><br>
        <span style="color:{muted};">{crypto_note}</span>
    </p>

    <h3 style="margin-bottom:4px;">Ответственность</h3>
    <p style="margin-top:0;color:{muted};">
        Программа подтверждает математическую верность подписи и совпадение ГОСТ-хеша,
        но не гарантирует юридическую значимость: она зависит от CryptoPro CSP,
        сертификата, удостоверяющего центра и настроек доверия.
    </p>
    """


def show_about(parent=None) -> None:
    dialog = QDialog(parent)
    dialog.setWindowTitle("О программе")
    dialog.resize(660, 640)
    dialog.setStyleSheet(theme.stylesheet())

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 18, 20, 16)
    layout.setSpacing(12)

    browser = QTextBrowser(dialog)
    browser.setOpenExternalLinks(True)
    browser.setFrameShape(QTextBrowser.Shape.NoFrame)
    browser.setHtml(build_about_html())
    browser.setStyleSheet(f"background: {theme.SURFACE};")
    layout.addWidget(browser, 1)

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
    close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
    close_button.setText("Закрыть")
    close_button.setObjectName("Tonal")
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)

    dialog.exec()
