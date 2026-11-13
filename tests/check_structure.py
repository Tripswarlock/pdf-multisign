"""Offline structural checks: no token or CryptoPro session required.

Run with:  python tests/check_structure.py
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT / "vendor" / "win32"))
sys.path.insert(0, str(ROOT / "vendor" / "win32" / "lib"))
sys.path.insert(0, str(ROOT / "vendor" / "pywin32_system32"))

from pdfsign import pdf as pdfmod, service  # noqa: E402


class StubCertificate:
    thumbprint = "STUB0000STUB0000"
    display_name = "Тестовый Подписант"
    position = "Директор"
    organization = "ООО «Тест»"
    serial = "0123456789ABCDEF"
    validity_text = "01.01.2026 — 01.01.2028"
    sign_capable = True
    is_valid_now = True


def stub_cms(raw: bytes) -> bytes:
    """Deterministic fake CMS: enough to exercise ByteRange bookkeeping."""
    digest = hashlib.sha256(raw).digest()
    return (b"\x30\x82" + (len(digest) + 12).to_bytes(2, "big")
            + b"\x06\x03\x2a\x03\x04" + digest) * 40


def find_fixture() -> pathlib.Path:
    candidates = [
        ROOT / "fixtures" / "unsigned.pdf",
        pathlib.Path(r"D:\Deepseek-harness\git\stamp-ep-sa-main\stamp-ep-sa-main\fixtures\unsigned.pdf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise SystemExit("Не найден тестовый PDF (unsigned.pdf)")


def main() -> int:
    service.crypto.sign_detached = lambda raw, thumb: stub_cms(raw)
    source = find_fixture().read_bytes()
    certificate = StubCertificate()

    failures: list[str] = []

    outcome = service.sign_document(
        source, [certificate, certificate], service.SignOptions(visible=True, page_index=-1)
    )
    if outcome.error:
        failures.append(f"ошибка подписи: {outcome.error}")
    fields = pdfmod.list_signatures(outcome.output)
    if len(fields) != 2:
        failures.append(f"ожидалось 2 подписи, получено {len(fields)}")
    if fields and not fields[-1].covers_whole_file:
        failures.append("последняя подпись должна покрывать весь файл")
    if fields and fields[0].covers_whole_file:
        failures.append("первая подпись не может покрывать более позднюю ревизию")
    for field in fields:
        if not field.contents:
            failures.append(f"{field.field_name}: пустой /Contents")
        if not field.signed_bytes:
            failures.append(f"{field.field_name}: пустой ByteRange")
        if len(field.byte_range) != 4:
            failures.append(f"{field.field_name}: ByteRange не из 4 чисел")

    invisible = service.sign_document(
        source, [certificate], service.SignOptions(visible=False, page_index=0)
    )
    if len(pdfmod.list_signatures(invisible.output)) != 1:
        failures.append("невидимая подпись не добавлена")

    if failures:
        print("FAILED")
        for failure in failures:
            print("  -", failure)
        return 1
    print("OK: 2 подписи добавляются, ByteRange и /Contents корректны")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
