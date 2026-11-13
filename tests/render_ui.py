"""Render the main window (and optionally the About dialog) to PNG files.

Usage:
    python tests/render_ui.py [--pdf path.pdf] [--out docs/screenshot.png] [--about]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT / "vendor" / "win32"))
sys.path.insert(0, str(ROOT / "vendor" / "win32" / "lib"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", default="")
    parser.add_argument("--out", default=str(ROOT / "docs" / "screenshot.png"))
    parser.add_argument("--about", action="store_true")
    parser.add_argument("--about-out", default=str(ROOT / "docs" / "about.png"))
    args = parser.parse_args()

    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import (
        QApplication,
        QDialog,
        QDialogButtonBox,
        QTextBrowser,
        QVBoxLayout,
    )

    from pdfsign import about, resources, theme, window

    app = QApplication([])
    app.setFont(QFont("Segoe UI", 10))
    icon = resources.resource_path("pdf-multisign.png")
    if icon:
        from PySide6.QtGui import QIcon

        app.setWindowIcon(QIcon(icon))

    win = window.MainWindow()
    win.resize(1200, 860)
    win.show()
    if args.pdf:
        win._load_pdf(pathlib.Path(args.pdf))
        rows = [r for r in win._rows if isinstance(r, window.CertificateRow)]
        usable = [r for r in rows if r.info.sign_capable and r.info.is_valid_now]
        for row in usable[:1]:
            row.set_checked(True)

    QTimer.singleShot(1200, lambda: (win.grab().save(args.out), print("saved", args.out)))

    if args.about:
        def snap_about() -> None:
            dialog = QDialog(win)
            dialog.setWindowTitle("О программе")
            dialog.resize(660, 660)
            dialog.setStyleSheet(theme.stylesheet())
            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(20, 18, 20, 16)
            browser = QTextBrowser(dialog)
            browser.setFrameShape(QTextBrowser.Shape.NoFrame)
            browser.setHtml(about.build_about_html())
            layout.addWidget(browser, 1)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
            buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
            buttons.button(QDialogButtonBox.StandardButton.Close).setObjectName("Tonal")
            layout.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)
            dialog.show()
            QTimer.singleShot(600, lambda: (dialog.grab().save(args.about_out),
                                            print("saved", args.about_out),
                                            dialog.close()))

        QTimer.singleShot(600, snap_about)

    QTimer.singleShot(2600, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
