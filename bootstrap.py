"""Put the bundled dependencies on sys.path (Windows and Linux)."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

_candidates = [
    os.path.join(ROOT, "vendor"),
    os.path.join(ROOT, "vendor", "win32"),
    os.path.join(ROOT, "vendor", "win32", "lib"),
]
for _path in _candidates:
    if os.path.isdir(_path) and _path not in sys.path:
        sys.path.insert(0, _path)

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

if sys.platform == "win32":
    _dlls = os.path.join(ROOT, "vendor", "pywin32_system32")
    if os.path.isdir(_dlls):
        os.add_dll_directory(_dlls)
        os.environ["PATH"] = _dlls + os.pathsep + os.environ.get("PATH", "")
