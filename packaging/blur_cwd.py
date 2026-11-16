"""Blur docs/*.png in the CURRENT working directory.

Used by git filter-branch --tree-filter: the temp checkout cwd is the tree
being rewritten, so paths must not be resolved from this file's location.
"""
from __future__ import annotations

import pathlib

from blur_docs import REGIONS, blur_file


def main() -> int:
    docs = pathlib.Path.cwd() / "docs"
    for name, regions in REGIONS.items():
        path = docs / name
        if path.exists():
            blur_file(path, regions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
