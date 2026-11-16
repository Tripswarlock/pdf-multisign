"""Blur signer personal data in the published screenshots (docs/*.png).

Run after re-rendering screenshots:  python packaging/blur_docs.py
Coordinates are (left, top, right, bottom) in pixels of each PNG.
"""
from __future__ import annotations

import pathlib

from PIL import Image, ImageFilter

BILINEAR = Image.Resampling.BILINEAR

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

# Hand-picked regions that contain certificate/signature owner data
# (names, organizations, validity dates, thumbprints, serials).
REGIONS: dict[str, list[tuple[int, int, int, int]]] = {
    "screenshot-main.png": [
        (728, 168, 1172, 425),    # certificate rows in the «Подписанты» card
    ],
    "screenshot-signed.png": [
        (728, 165, 1172, 425),    # certificate rows
        (730, 670, 1168, 742),    # log line «Signature2 — <signer>»
        (488, 588, 648, 726),     # tiny visible signature blocks in the preview
    ],
    "session-host.png": [
        (48, 305, 710, 448),      # certificate rows of the organizer
    ],
    "session-join.png": [
        (48, 308, 710, 448),      # certificate rows (name, org, validity)
    ],
    "astra-ui.png": [
        (728, 168, 1172, 235),    # certificate row on Astra
    ],
    "signature-block.png": [
        (883, 608, 1172, 890),    # both visible signature stamps on the page
    ],
}


def blur_file(path: pathlib.Path, regions: list[tuple[int, int, int, int]]) -> bool:
    image = Image.open(path).convert("RGB")
    width, height = image.size
    changed = False
    for left, top, right, bottom in regions:
        box = (
            max(0, left),
            max(0, top),
            min(width, right),
            min(height, bottom),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        region = image.crop(box)
        blurred = region.filter(ImageFilter.GaussianBlur(radius=12))
        # extra pixelation so short words cannot be recovered from the blur
        small = blurred.resize((max(1, region.width // 6), max(1, region.height // 6)),
                               BILINEAR)
        pixelated = small.resize(region.size, BILINEAR)
        image.paste(pixelated, box)
        changed = True
    if changed:
        image.save(path, "PNG")
    return changed


def main() -> int:
    for name, regions in REGIONS.items():
        path = DOCS / name
        if not path.exists():
            print("пропущен (нет файла):", name)
            continue
        if blur_file(path, regions):
            print("заблюрен:", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
