"""Resolve bundled resources in source runs, PyInstaller builds and .deb installs."""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def resource_path(name: str) -> str:
    """Return an absolute path to a bundled resource, or "" when missing."""
    candidates: list[str] = []
    bundle = getattr(sys, "_MEIPASS", "")
    if bundle:
        candidates.append(os.path.join(bundle, "resources", name))
    candidates.append(os.path.join(_HERE, "resources", name))
    candidates.append(os.path.join(os.path.dirname(_HERE), "resources", name))
    # .deb layout: /opt/pdf-multisign/resources next to the pdfsign package.
    candidates.append(os.path.join(os.path.dirname(_HERE), "resources", name))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return ""
