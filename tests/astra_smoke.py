"""Headless smoke test for the packaged Linux build.

Run inside the target system:
    QT_QPA_PLATFORM=offscreen PYTHONPATH=/opt/pdf-multisign:/opt/pdf-multisign/vendor \
        python3 astra_smoke.py /tmp/astra-ui.png
"""
from __future__ import annotations

import sys


def main() -> int:
    output = sys.argv[1] if len(sys.argv) > 1 else "/tmp/astra-ui.png"

    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from pdfsign import about, crypto, theme, window

    app = QApplication([])
    app.setFont(QFont("DejaVu Sans", 10))

    print("crypto_status:", crypto.crypto_status())
    try:
        certificates = crypto.list_certificates()
        print("certificates:", len(certificates))
        for cert in certificates[:5]:
            print("  ", cert.thumbprint[:16], cert.display_name, cert.sign_capable, cert.is_valid_now)
    except Exception as exc:
        print("certificates error:", exc)

    win = window.MainWindow()
    win.resize(1200, 860)
    win.show()
    app.processEvents()
    win.grab().save(output)
    print("screenshot:", output)

    html = about.build_about_html()
    print("about html:", len(html), "chars")
    print("theme primary:", theme.PRIMARY)
    win.close()
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
