"""PyInstaller entry point for the Windows build."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    import bootstrap  # noqa: F401  (adds vendor/ when running from source)
except Exception:
    pass

from pdfsign.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
