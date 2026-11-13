"""Download Linux wheels and extract them into vendor-linux/."""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WHEELS = ROOT / "wheels" / "linux"
VENDOR = ROOT / "vendor-linux"

PACKAGES = [
    ("PySide6-Essentials", "6.12.0"),
    ("shiboken6", "6.12.0"),
    ("PyMuPDF", "1.26.5"),
    ("pypdf", "7.0.1"),
    ("asn1crypto", "1.5.1"),
    ("gostcrypto", "1.2.5"),
]


def pick_url(name: str, version: str) -> tuple[str, str]:
    meta = json.load(urllib.request.urlopen(
        f"https://pypi.org/pypi/{name}/{version}/json", timeout=60
    ))
    candidates = []
    for item in meta["urls"]:
        filename = item["filename"]
        if not filename.endswith(".whl"):
            continue
        if "manylinux" in filename and "x86_64" in filename:
            candidates.append(item)
        elif "none-any" in filename:
            candidates.append(item)
    if not candidates:
        raise SystemExit(f"нет подходящего wheel для {name} {version}")
    # Prefer the most portable manylinux tag.
    candidates.sort(key=lambda item: item["filename"])
    return candidates[0]["filename"], candidates[0]["url"]


def main() -> int:
    WHEELS.mkdir(parents=True, exist_ok=True)
    VENDOR.mkdir(parents=True, exist_ok=True)
    for name, version in PACKAGES:
        filename, url = pick_url(name, version)
        target = WHEELS / filename
        if not target.exists():
            print(f"скачиваю {filename}")
            urllib.request.urlretrieve(url, target)
        else:
            print(f"уже есть {filename}")
        with zipfile.ZipFile(target) as archive:
            archive.extractall(VENDOR)
    print("готово:", VENDOR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
