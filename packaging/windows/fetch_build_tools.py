"""Fetch PyInstaller and its build-time dependencies into tools/pyinstaller."""
from __future__ import annotations

import json
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TOOLS = ROOT / "tools" / "pyinstaller"
WHEELS = ROOT / "wheels" / "tools"

PACKAGES = [
    ("pyinstaller", None),
    ("pyinstaller-hooks-contrib", None),
    ("altgraph", None),
    ("packaging", None),
    ("pefile", None),
    ("pywin32-ctypes", None),
    ("setuptools", None),
]


def latest_version(name: str) -> str:
    meta = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=60))
    return meta["info"]["version"]


def pick_url(name: str, version: str) -> tuple[str, str]:
    meta = json.load(urllib.request.urlopen(
        f"https://pypi.org/pypi/{name}/{version}/json", timeout=60
    ))
    candidates = []
    for item in meta["urls"]:
        filename = item["filename"]
        if not filename.endswith(".whl"):
            continue
        if "win_amd64" in filename or "none-any" in filename:
            candidates.append(item)
    if not candidates:
        raise SystemExit(f"нет wheel для {name} {version}")
    candidates.sort(key=lambda item: ("win_amd64" not in item["filename"], item["filename"]))
    return candidates[0]["filename"], candidates[0]["url"]


def main() -> int:
    WHEELS.mkdir(parents=True, exist_ok=True)
    TOOLS.mkdir(parents=True, exist_ok=True)
    for name, version in PACKAGES:
        version = version or latest_version(name)
        filename, url = pick_url(name, version)
        target = WHEELS / filename
        if not target.exists():
            print(f"скачиваю {filename}")
            urllib.request.urlretrieve(url, target)
        with zipfile.ZipFile(target) as archive:
            archive.extractall(TOOLS)
    print("готово:", TOOLS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
