"""COM apartment handling.

CryptoPro COM objects must outlive every call that uses them, so the apartment
is initialized once per thread and deliberately never torn down while objects
are alive.  Calling ``CoUninitialize`` between signing operations leaves stale
references behind and makes the next ``SignCades`` fail with
"Неправильный зарегистрированный набор ключей".
"""
from __future__ import annotations

import threading

try:
    import pythoncom

    PYTHONCOM_AVAILABLE = True
except Exception:  # pragma: no cover - depends on the host
    pythoncom = None  # type: ignore[assignment]
    PYTHONCOM_AVAILABLE = False

_lock = threading.Lock()
_initialized: set[int] = set()


def ensure_initialized() -> None:
    """Initialize the COM apartment for the current thread exactly once."""
    if not PYTHONCOM_AVAILABLE or pythoncom is None:
        return
    ident = threading.get_ident()
    with _lock:
        if ident in _initialized:
            return
        _initialized.add(ident)
    try:
        pythoncom.CoInitialize()
    except Exception:
        with _lock:
            _initialized.discard(ident)


def release(*objects) -> None:
    """Drop local references so CryptoPro can release the key container."""
    for obj in objects:
        try:
            del obj
        except Exception:
            pass
