"""Entry point: python -m pdfsign.app"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from . import __version__, resources
from .window import MainWindow


def _exit_cleanly(code: int) -> None:
    """Leave the process without Python's COM/thread teardown.

    CryptoPro COM objects (and the LAN session server thread) stay referenced
    until the interpreter finalizes, which crashes the process with an access
    violation *after* the window closes.  Everything is already saved by then,
    so flushing the streams and exiting hard is safe and avoids the crash
    dialog.
    """
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("PDF Мультиподпись")
    app.setApplicationDisplayName("PDF Мультиподпись")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Tripswarlock")
    icon_path = resources.resource_path("pdf-multisign.png")
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    window = MainWindow()
    if icon_path:
        window.setWindowIcon(QIcon(icon_path))
    window.show()
    code = app.exec()
    _exit_cleanly(code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
