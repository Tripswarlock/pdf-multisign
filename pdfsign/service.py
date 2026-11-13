"""High level signing service: several certificates in one pass."""
from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass, field
from io import BytesIO
from typing import Callable

from pypdf import PdfReader

from . import crypto, pdf as pdfmod


def store_label() -> str:
    """Human readable name of the certificate store for the current platform."""
    if sys.platform == "win32":
        return "Хранилище CurrentUser\\My"
    return "Хранилище пользователя (uMy)"


@dataclass
class SignOptions:
    visible: bool = True
    page_index: int = -1  # -1 = last page
    reason: str = "Подписание документа"
    location: str = ""


@dataclass
class ProgressEvent:
    index: int
    total: int
    title: str
    detail: str = ""
    done: bool = False


ProgressCallback = Callable[[ProgressEvent], None]


@dataclass
class SignatureReport:
    field_name: str
    signer: str
    cms_size: int
    status: str = ""
    detail: str = ""
    ok: bool = False


@dataclass
class SignOutcome:
    output: bytes
    reports: list[SignatureReport] = field(default_factory=list)
    error: str = ""


def page_sizes(data: bytes) -> list[tuple[float, float]]:
    reader = PdfReader(BytesIO(data))
    sizes: list[tuple[float, float]] = []
    for page in reader.pages:
        box = page.get("/MediaBox")
        try:
            width = float(box[2]) - float(box[0])
            height = float(box[3]) - float(box[1])
        except Exception:
            width, height = 595.0, 842.0
        sizes.append((max(1.0, width), max(1.0, height)))
    return sizes


def page_count(data: bytes) -> int:
    return len(page_sizes(data))


BLOCK_WIDTH = 226.0
BLOCK_HEIGHT = 72.0
_BLOCK_GAP = 9.0
_MARGIN = 30.0


def _existing_signature_count(data: bytes) -> int:
    try:
        return len(pdfmod.list_signatures(data))
    except Exception:
        return 0


def stacked_rects(
    count: int, page_width: float, page_height: float
) -> list[tuple[float, float, float, float]]:
    """Return ``count`` non-overlapping blocks stacked from the bottom right."""
    if count <= 0:
        return []
    width = min(BLOCK_WIDTH, max(120.0, page_width * 0.45))
    height = min(BLOCK_HEIGHT, max(48.0, page_height * 0.12))
    left = max(12.0, page_width - width - _MARGIN)
    bottom = max(12.0, page_height * 0.06)
    per_column = max(1, int((page_height * 0.8) // (height + _BLOCK_GAP)))
    rects: list[tuple[float, float, float, float]] = []
    for index in range(count):
        column = index // per_column
        row = index % per_column
        x0 = max(8.0, left - column * (width + _BLOCK_GAP))
        x1 = x0 + width
        y0 = bottom + row * (height + _BLOCK_GAP)
        y1 = y0 + height
        if y1 > page_height - 8:
            y0 = page_height - 8 - height
            y1 = page_height - 8
        if x1 > page_width:
            x0, x1 = page_width - width - 8, page_width - 8
        rects.append((x0, y0, x1, y1))
    return rects


def sign_document(
    data: bytes,
    certificates,
    options: SignOptions,
    progress: ProgressCallback | None = None,
) -> SignOutcome:
    total = len(certificates)
    if total == 0:
        return SignOutcome(output=data, error="Не выбран ни один сертификат")

    for certificate in certificates:
        if not getattr(certificate, "sign_capable", True):
            return SignOutcome(
                output=data,
                error=(
                    f"Сертификат «{certificate.display_name}» нельзя использовать для подписи: "
                    f"{getattr(certificate, 'signing_note', '') or 'ключ недоступен'}"
                ),
            )
        if not getattr(certificate, "is_valid_now", True):
            return SignOutcome(
                output=data,
                error=f"Срок действия сертификата «{certificate.display_name}» истёк",
            )

    sizes = page_sizes(data)
    if not sizes:
        return SignOutcome(output=data, error="PDF не содержит страниц")
    page_index = options.page_index if options.page_index >= 0 else len(sizes) - 1
    page_index = max(0, min(page_index, len(sizes) - 1))
    page_width, page_height = sizes[page_index]

    rects = (
        stacked_rects(total + _existing_signature_count(data), page_width, page_height)[
            _existing_signature_count(data) :
        ]
        if options.visible
        else [None] * total
    )

    start_index = len(pdfmod.list_signatures(data)) + 1
    result = data
    reports: list[SignatureReport] = []

    for position, certificate in enumerate(certificates):
        title = certificate.display_name or certificate.thumbprint[:16]
        if progress:
            progress(
                ProgressEvent(
                    position,
                    total,
                    title,
                    "CryptoPro создаёт отсоединённую CAdES-BES…",
                )
            )
        request = pdfmod.SignRequest(
            thumbprint=certificate.thumbprint,
            reason=options.reason,
            location=options.location,
            signer_name=certificate.display_name,
            page_index=page_index,
            visible=options.visible and rects[position] is not None,
            rect=rects[position],
            certificate_info=certificate,
            stamp_title="ДОКУМЕНТ ПОДПИСАН ЭЛЕКТРОННОЙ ПОДПИСЬЮ",
        )
        field_name = f"Signature{start_index + position}"
        try:
            outcome = pdfmod.append_signature(
                result,
                request,
                lambda raw, thumb=certificate.thumbprint: crypto.sign_detached(raw, thumb),
                field_name=field_name,
            )
        except (pdfmod.PdfSignError, crypto.CryptoError) as exc:
            return SignOutcome(output=result, reports=reports, error=str(exc))

        result = outcome.output
        reports.append(
            SignatureReport(
                field_name=outcome.field_name,
                signer=certificate.display_name,
                cms_size=outcome.cms_size,
            )
        )
        if progress:
            progress(
                ProgressEvent(
                    position + 1,
                    total,
                    title,
                    "Подпись добавлена как новая ревизия PDF",
                    done=True,
                )
            )
    return SignOutcome(output=result, reports=reports)


def describe_signatures(
    data: bytes, verify: Callable[[bytes, bytes], tuple[bool, str]] | None = None
) -> list[SignatureReport]:
    """Inspect the signatures already present in a document."""
    reports: list[SignatureReport] = []
    for field in pdfmod.list_signatures(data):
        status = "покрывает весь файл" if field.covers_whole_file else "версия на момент подписания"
        if field.modified_after_signing and not field.covers_whole_file:
            status = "после подписи добавлены данные"
        ok, detail = False, ""
        if verify is not None and field.contents and field.signed_bytes:
            ok, detail = verify(field.contents, field.signed_bytes)
        reports.append(
            SignatureReport(
                field_name=field.field_name,
                signer=field.signature_time or "—",
                cms_size=len(field.contents),
                status=status,
                detail=detail,
                ok=ok,
            )
        )
    return reports
