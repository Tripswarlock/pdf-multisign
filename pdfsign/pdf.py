"""Incremental PDF signing: signature dictionary, AcroForm field, appearance.

The implementation appends a new revision instead of rewriting the document, so
previously applied signatures keep covering their bytes.  This is the mechanism
that makes several signatures in one file possible.
"""
from __future__ import annotations

import hashlib
import re
import zlib
from dataclasses import dataclass, field
from io import BytesIO
from typing import Callable

from pypdf import PdfReader
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    DictionaryObject,
    IndirectObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

from . import appearance

CONTENTS_HEX_LENGTH = 49152  # 24 KiB of CMS capacity, plenty for GOST CAdES-BES
_NUMBER_WIDTH = 20
_NEWLINE = b"\n"

#: ``SignRequest.page_index`` sentinel: put a visible stamp on every page.
ALL_PAGES = -2


class PdfSignError(RuntimeError):
    def __init__(self, message: str, code: str = "PDF_SIGN_ERROR") -> None:
        super().__init__(message)
        self.code = code


@dataclass
class SignRequest:
    thumbprint: str
    reason: str = "Подписание документа"
    location: str = ""
    signer_name: str = ""
    page_index: int = 0
    visible: bool = True
    rect: tuple[float, float, float, float] | None = None
    stamp_title: str = ""
    pin: str | None = None
    title: str = ""
    certificate_info: object | None = None

    def resolved_rect(self, page_width: float, page_height: float) -> tuple[float, float, float, float]:
        if self.rect:
            x0, y0, x1, y1 = self.rect
            left, right = sorted((x0, x1))
            bottom, top = sorted((y0, y1))
            left = max(0.0, min(left, page_width - 4))
            right = max(left + 4, min(right, page_width))
            bottom = max(0.0, min(bottom, page_height - 4))
            top = max(bottom + 4, min(top, page_height))
            return left, bottom, right, top
        width = min(230.0, max(120.0, page_width * 0.36))
        height = min(78.0, max(52.0, page_height * 0.105))
        left = max(18.0, page_width - width - 36.0)
        bottom = max(18.0, page_height * 0.075)
        return left, bottom, left + width, bottom + height


@dataclass
class SignResult:
    output: bytes
    field_name: str
    cms_size: int
    coverage: int


@dataclass
class PendingSignature:
    """A revision prepared for signing: the /Contents placeholder is zeroed.

    ``signed_bytes`` are the two /ByteRange segments concatenated — exactly
    the bytes a signer must feed into the detached CMS.  ``commit`` inserts
    the CMS hex into the placeholder and returns the final revision.
    """

    data: bytes
    signed_bytes: bytes
    field_name: str
    _hex_start: int = 0

    def commit(self, cms: bytes) -> bytes:
        if not cms:
            raise PdfSignError("Получена пустая подпись", "SIGNING_EMPTY")
        if len(cms) * 2 > CONTENTS_HEX_LENGTH:
            raise PdfSignError("CMS не помещается в контейнер подписи", "CMS_TOO_LARGE")
        buffer = bytearray(self.data)
        payload = cms.hex().upper().encode("ascii").ljust(CONTENTS_HEX_LENGTH, b"0")
        buffer[self._hex_start : self._hex_start + CONTENTS_HEX_LENGTH] = payload
        return bytes(buffer)


@dataclass
class PdfField:
    index: int
    field_name: str
    sub_filter: str = ""
    byte_range: list[int] = field(default_factory=list)
    contents: bytes = b""
    contents_offset: int = -1
    modified_after_signing: bool = False
    covers_whole_file: bool = False
    signed_bytes: bytes = b""
    signature_time: str = ""


def _pdf_text(value: str) -> bytes:
    """Encode a PDF text string: plain ASCII when possible, else UTF-16BE."""
    if not value:
        return b"()"
    if all(32 <= ord(char) < 127 and char not in "()\\" for char in value):
        return b"(" + value.encode("ascii") + b")"
    return b"<FEFF" + value.encode("utf-16-be").hex().upper().encode("ascii") + b">"


def _whitespace_only(data: bytes) -> bool:
    return all(byte in b" \r\n\t\f\x00" for byte in data)


def _ensure_trailing_newline(data: bytes) -> bytes:
    return data if data.endswith(b"\n") else data + _NEWLINE


def _previous_startxref(pdf: bytes) -> int:
    marker = pdf.rfind(b"startxref")
    if marker < 0:
        return 0
    match = re.match(rb"startxref\s+(\d+)", pdf[marker : marker + 64])
    return int(match.group(1)) if match else 0


def _read_objects(pdf: bytes) -> PdfReader:
    try:
        return PdfReader(BytesIO(pdf))
    except Exception as exc:  # pragma: no cover - defensive
        raise PdfSignError(f"Не удалось разобрать PDF: {exc}", "PDF_PARSE_ERROR") from exc


def _next_object_number(reader: PdfReader) -> int:
    try:
        size = int(reader.trailer.get("/Size", 0))
    except Exception:
        size = 0
    highest = 0
    try:
        for start, entries in reader.xref:
            if isinstance(entries, dict):
                for number in entries:
                    if isinstance(number, int):
                        highest = max(highest, number)
    except Exception:
        pass
    return max(size, highest + 1)


def _resolve_root(reader: PdfReader) -> IndirectObject:
    root = reader.trailer.raw_get("/Root")
    if not isinstance(root, IndirectObject):
        raise PdfSignError("В PDF отсутствует ссылка /Root", "PDF_STRUCTURE_ERROR")
    return root


def _document_id(reader: PdfReader, pdf: bytes) -> list[bytes]:
    try:
        identifiers = reader.trailer.raw_get("/ID")
        if isinstance(identifiers, ArrayObject) and len(identifiers) >= 2:
            result = []
            for item in identifiers[:2]:
                raw = re.sub(rb"[^0-9a-fA-F]", b"", str(item).encode("latin-1"))
                if raw and len(raw) % 2 == 0:
                    result.append(bytes.fromhex(raw.decode("ascii")))
            if len(result) == 2:
                return result
    except Exception:
        pass
    digest = hashlib.md5(pdf).digest()
    return [digest, digest]


def _copy_dict(source) -> DictionaryObject:
    result = DictionaryObject()
    if source is not None:
        for key, value in source.items():
            result[key] = value
    return result


def _write_object(buffer: bytearray, number: int, body: bytes) -> int:
    offset = len(buffer)
    buffer += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    return offset


def _object_bytes(obj) -> bytes:
    buffer = BytesIO()
    obj.write_to_stream(buffer)
    return buffer.getvalue()


def _build_widget(
    field_name: str,
    rect: tuple[float, float, float, float],
    page_ref: IndirectObject,
    signature_ref: IndirectObject,
    appearance_ref: IndirectObject,
) -> DictionaryObject:
    widget = DictionaryObject()
    widget[NameObject("/Type")] = NameObject("/Annot")
    widget[NameObject("/Subtype")] = NameObject("/Widget")
    widget[NameObject("/FT")] = NameObject("/Sig")
    widget[NameObject("/T")] = TextStringObject(field_name)
    widget[NameObject("/F")] = NumberObject(132)  # Print | Locked
    widget[NameObject("/Rect")] = ArrayObject(
        [NumberObject(round(value, 2)) for value in rect]
    )
    widget[NameObject("/V")] = signature_ref
    widget[NameObject("/P")] = page_ref
    widget[NameObject("/AP")] = DictionaryObject(
        {NameObject("/N"): appearance_ref}
    )
    return widget


def _build_appearance(
    width: float, height: float, content: bytes, resources: DictionaryObject | None = None
) -> bytes:
    compressed = zlib.compress(content, 9)
    parts = [
        b"<< /Type /XObject /Subtype /Form /FormType 1 /BBox [0 0 ",
        f"{width:.3f} {height:.3f}".encode("ascii"),
        b"] /Resources ",
        _object_bytes(resources if resources is not None else DictionaryObject()),
        b" /Filter /FlateDecode /Length ",
        str(len(compressed)).encode("ascii"),
        b" >>\nstream\n",
        compressed,
        b"\nendstream",
    ]
    return b"".join(parts)


def _build_signature_dict(
    contents_placeholder: bytes,
    byte_range_placeholder: bytes,
    request: SignRequest,
    timestamp: str,
) -> bytes:
    parts = [
        b"<< /Type /Sig /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.detached "
        b"/ByteRange ",
        byte_range_placeholder,
        b" /Contents <",
        contents_placeholder,
        b"> /M ",
        _pdf_text(timestamp),
    ]
    if request.reason:
        parts += [b" /Reason ", _pdf_text(request.reason)]
    if request.location:
        parts += [b" /Location ", _pdf_text(request.location)]
    if request.signer_name:
        parts += [b" /Name ", _pdf_text(request.signer_name)]
    parts.append(b" >>")
    return b"".join(parts)


def _build_xref(buffer: bytearray, offsets: dict[int, int], root_number: int,
                size: int, previous: int, identifiers: list[bytes]) -> int:
    xref_offset = len(buffer)
    numbers = sorted(offsets)
    buffer += b"xref\n"
    index = 0
    while index < len(numbers):
        start = numbers[index]
        run = [start]
        index += 1
        while index < len(numbers) and numbers[index] == run[-1] + 1:
            run.append(numbers[index])
            index += 1
        buffer += f"{run[0]} {len(run)}\n".encode("ascii")
        for number in run:
            buffer += f"{offsets[number]:010d} 00000 n \n".encode("ascii")
    trailer = [
        b"trailer\n<< /Size ",
        str(size).encode("ascii"),
        b" /Root ",
        f"{root_number} 0 R".encode("ascii"),
    ]
    if previous > 0:
        trailer += [b" /Prev ", str(previous).encode("ascii")]
    trailer.append(b" /ID [<")
    trailer.append(identifiers[0].hex().upper().encode("ascii"))
    trailer.append(b"><")
    trailer.append(identifiers[1].hex().upper().encode("ascii"))
    trailer.append(b">] >>\nstartxref\n")
    trailer.append(str(xref_offset).encode("ascii"))
    trailer.append(b"\n%%EOF\n")
    buffer += b"".join(trailer)
    return xref_offset


def _pdf_timestamp() -> str:
    import datetime as dt

    now = dt.datetime.now()
    return now.strftime("D:%Y%m%d%H%M%S")


def prepare_signature(
    pdf: bytes,
    request: SignRequest,
    *,
    field_name: str,
) -> PendingSignature:
    """Build a new revision with a zeroed /Contents placeholder.

    The caller signs :attr:`PendingSignature.signed_bytes` and then commits the
    CMS with :meth:`PendingSignature.commit`.
    """
    if not pdf.startswith(b"%PDF-"):
        raise PdfSignError("Исходные данные не являются PDF", "NOT_A_PDF")
    reader = _read_objects(pdf)
    page_count = len(reader.pages)
    if page_count == 0:
        raise PdfSignError("PDF не содержит страниц", "EMPTY_PDF")

    # "all pages" puts the same stamp on every page: the primary page keeps the
    # signature dictionary and field, the rest get the same appearance widget.
    all_pages = request.page_index == ALL_PAGES
    if all_pages:
        primary_index = 0
        targets = list(range(page_count))
    else:
        if request.page_index < 0 or request.page_index >= page_count:
            raise PdfSignError("Неверный индекс страницы", "PAGE_OUT_OF_RANGE")
        primary_index = request.page_index
        targets = [primary_index]

    page = reader.pages[primary_index]
    page_ref = page.indirect_reference
    if page_ref is None:
        raise PdfSignError("Страница PDF не является косвенным объектом", "PDF_STRUCTURE_ERROR")
    page_dict = page.get_object()
    media_box = page_dict.get("/MediaBox")
    try:
        page_width = float(media_box[2]) - float(media_box[0])
        page_height = float(media_box[3]) - float(media_box[1])
    except Exception:
        page_width, page_height = 595.0, 842.0

    rect = request.resolved_rect(page_width, page_height)
    root = _resolve_root(reader)
    previous = _previous_startxref(pdf)
    next_id = _next_object_number(reader)

    acroform = None
    acroform_id = None
    try:
        root_dict = root.get_object()
        raw_acro = root_dict.raw_get("/AcroForm") if "/AcroForm" in root_dict else None
        if isinstance(raw_acro, IndirectObject):
            acroform_id = raw_acro.idnum
            acroform = raw_acro.get_object()
        elif raw_acro is not None:
            acroform = raw_acro
    except Exception:
        acroform = None

    reserved = {root.idnum, page_ref.idnum}
    if acroform_id is not None:
        reserved.add(acroform_id)

    def _page_ref_for(index: int):
        reference = reader.pages[index].indirect_reference
        if reference is not None:
            reserved.add(reference.idnum)
        return reference

    target_refs = [_page_ref_for(index) for index in targets]
    target_dicts = [reader.pages[index].get_object() for index in targets]

    def allocate() -> int:
        nonlocal next_id
        while next_id in reserved:
            next_id += 1
        value = next_id
        reserved.add(value)
        next_id += 1
        return value

    signature_id = allocate()
    widget_id = allocate()
    appearance_id = allocate()
    if acroform_id is None:
        acroform_id = allocate()
    size = next_id + 1

    buffer = bytearray(_ensure_trailing_newline(pdf))
    offsets: dict[int, int] = {}

    signature_ref = IndirectObject(signature_id, 0, reader)
    widget_ref = IndirectObject(widget_id, 0, reader)
    appearance_ref = IndirectObject(appearance_id, 0, reader)

    timestamp = _pdf_timestamp()
    timestamp_literal = timestamp
    if request.visible:
        import datetime as dt

        now = dt.datetime.now()
        human = now.strftime("%d.%m.%Y %H:%M")
        layout = appearance.make_layout_from_certificate(
            rect[2] - rect[0], rect[3] - rect[1], request.certificate_info, human,
            title=request.stamp_title,
        )
        appearance_content = appearance.to_pdf_content(layout)
    else:
        appearance_content = b""

    # --- signature dictionary with fixed-size placeholders -------------------
    contents_placeholder = b"0" * CONTENTS_HEX_LENGTH
    byte_range_placeholder = (
        b"[0 " + b"0" * _NUMBER_WIDTH + b" " + b"0" * _NUMBER_WIDTH + b" "
        + b"0" * _NUMBER_WIDTH + b"]"
    )
    signature_body = _build_signature_dict(
        contents_placeholder, byte_range_placeholder, request, timestamp_literal
    )
    signature_offset = _write_object(buffer, signature_id, signature_body)
    offsets[signature_id] = signature_offset

    body_start = signature_offset + len(f"{signature_id} 0 obj\n".encode("ascii"))
    hex_start = buffer.find(contents_placeholder, body_start)
    if hex_start < 0:
        raise PdfSignError("Внутренняя ошибка: заглушка /Contents не найдена")
    gap_start = hex_start - 1  # the '<' of the hex string
    gap_end = hex_start + CONTENTS_HEX_LENGTH + 1  # after the closing '>'
    ranges_position = buffer.find(byte_range_placeholder, body_start)
    if ranges_position < 0:
        raise PdfSignError("Внутренняя ошибка: заглушка /ByteRange не найдена")

    # --- appearance, widget, updated page ------------------------------------
    appearance_body = _build_appearance(
        rect[2] - rect[0], rect[3] - rect[1], appearance_content
    )
    offsets[appearance_id] = _write_object(buffer, appearance_id, appearance_body)

    widget = _build_widget(field_name, rect, page_ref, signature_ref, appearance_ref)
    offsets[widget_id] = _write_object(buffer, widget_id, _object_bytes(widget))

    # Stamps for the remaining pages ("all pages" mode) reference the same
    # signature, so one /Contents serves every page of the revision.
    page_widgets: dict[int, object] = {primary_index: widget_ref}
    for offset, index in enumerate(targets):
        if index == primary_index:
            continue
        reference = target_refs[offset]
        if reference is None:
            raise PdfSignError("Страница PDF не является косвенным объектом",
                               "PDF_STRUCTURE_ERROR")
        extra_id = allocate()
        offsets[extra_id] = _write_object(
            buffer,
            extra_id,
            _object_bytes(_build_widget(field_name, rect, reference,
                                        signature_ref, appearance_ref)),
        )
        page_widgets[index] = IndirectObject(extra_id, 0, reader)

    for offset, index in enumerate(targets):
        reference = target_refs[offset]
        updated_page = _copy_dict(target_dicts[offset])
        annots = updated_page.get("/Annots")
        if annots is None:
            annots_array = ArrayObject()
        else:
            try:
                annots_array = ArrayObject(list(annots.get_object()))
            except Exception:
                annots_array = ArrayObject()
        annots_array.append(page_widgets[index])
        updated_page[NameObject("/Annots")] = annots_array
        offsets[reference.idnum] = _write_object(
            buffer, reference.idnum, _object_bytes(updated_page)
        )

    # --- AcroForm ------------------------------------------------------------
    new_acroform = _copy_dict(acroform)
    fields = new_acroform.get("/Fields")
    if fields is None:
        fields_array = ArrayObject()
    else:
        try:
            fields_array = ArrayObject(list(fields.get_object()))
        except Exception:
            fields_array = ArrayObject()
    fields_array.append(widget_ref)
    new_acroform[NameObject("/Fields")] = fields_array
    new_acroform[NameObject("/SigFlags")] = NumberObject(3)
    if "/DA" not in new_acroform:
        new_acroform[NameObject("/DA")] = TextStringObject("/Helv 0 Tf 0 g")
    if "/DR" not in new_acroform:
        new_acroform[NameObject("/DR")] = DictionaryObject()
    offsets[acroform_id] = _write_object(buffer, acroform_id, _object_bytes(new_acroform))

    # --- catalog -------------------------------------------------------------
    updated_root = _copy_dict(root.get_object())
    updated_root[NameObject("/AcroForm")] = IndirectObject(acroform_id, 0, reader)
    offsets[root.idnum] = _write_object(buffer, root.idnum, _object_bytes(updated_root))

    size = max(next_id + 1, acroform_id + 1, root.idnum + 1, page_ref.idnum + 1)
    _build_xref(buffer, offsets, root.idnum, size, previous, _document_id(reader, pdf))

    # --- ByteRange and CMS ---------------------------------------------------
    file_size = len(buffer)
    ranges = (
        b"[0 "
        + str(gap_start).rjust(_NUMBER_WIDTH, "0").encode("ascii")
        + b" "
        + str(gap_end).rjust(_NUMBER_WIDTH, "0").encode("ascii")
        + b" "
        + str(file_size - gap_end).rjust(_NUMBER_WIDTH, "0").encode("ascii")
        + b"]"
    )
    if len(ranges) != len(byte_range_placeholder):
        raise PdfSignError("Внутренняя ошибка: /ByteRange изменил длину")
    buffer[ranges_position : ranges_position + len(ranges)] = ranges

    signed_bytes = bytes(buffer[:gap_start]) + bytes(buffer[gap_end:])
    return PendingSignature(
        data=bytes(buffer),
        signed_bytes=signed_bytes,
        field_name=field_name,
        _hex_start=hex_start,
    )


def append_signature(
    pdf: bytes,
    request: SignRequest,
    sign: Callable[[bytes], bytes],
    *,
    field_name: str,
) -> SignResult:
    """Append one signature revision to ``pdf``.

    ``sign`` receives the exact bytes that must be covered by the CMS.
    """
    pending = prepare_signature(pdf, request, field_name=field_name)
    cms = sign(pending.signed_bytes)
    output = pending.commit(cms)
    return SignResult(
        output=output,
        field_name=pending.field_name,
        cms_size=len(cms),
        coverage=len(pending.signed_bytes),
    )


# ---------------------------------------------------------------------------
# Parsing and verification of already signed documents
# ---------------------------------------------------------------------------

_BYTE_RANGE_RE = re.compile(rb"/ByteRange\s*\[([^\]]{4,400})\]")


def _parse_numbers(payload: bytes) -> list[int]:
    values: list[int] = []
    for token in re.findall(rb"-?\d+", payload):
        try:
            values.append(int(token))
        except ValueError:
            return []
    return values


def _extract_hex_at(pdf: bytes, position: int) -> tuple[bytes, int]:
    pos = position
    while pos < len(pdf) and pdf[pos] in b" \r\n\t\f\x00":
        pos += 1
    if pos >= len(pdf) or pdf[pos : pos + 1] != b"<":
        return b"", -1
    if pdf[pos : pos + 2] == b"<<":
        return b"", -1
    pos += 1
    start = pos
    while pos < len(pdf) and pdf[pos : pos + 1] != b">":
        pos += 1
    if pos >= len(pdf):
        return b"", -1
    raw = re.sub(rb"[^0-9a-fA-F]", b"", pdf[start:pos])
    if len(raw) % 2:
        raw += b"0"
    try:
        data = bytes.fromhex(raw.decode("ascii"))
    except ValueError:
        return b"", -1
    return data, pos + 1


def _trim_der(data: bytes) -> bytes:
    if len(data) < 2 or data[0] != 0x30:
        return data
    if data[1] < 0x80:
        total = 2 + data[1]
    else:
        count = data[1] & 0x7F
        if len(data) < 2 + count:
            return data
        total = 2 + count + int.from_bytes(data[2 : 2 + count], "big")
    return data[:total] if 0 < total <= len(data) else data


def _pdf_string(window: bytes, key: bytes) -> str:
    match = re.search(rb"/" + key + rb"\s*(\(|<)", window)
    if not match:
        return ""
    if match.group(1) == b"<":
        data, _ = _extract_hex_at(window, match.start(1))
        if data.startswith(b"\xfe\xff"):
            return data[2:].decode("utf-16-be", "replace")
        return data.decode("latin-1", "replace")
    start = match.end(1)
    depth = 1
    index = start
    while index < len(window) and depth:
        char = window[index : index + 1]
        if char == b"\\":
            index += 2
            continue
        if char == b"(":
            depth += 1
        elif char == b")":
            depth -= 1
        index += 1
    return window[start : index - 1].decode("latin-1", "replace")


def _pdf_name(window: bytes, key: bytes) -> str:
    """Read a PDF name value such as ``/SubFilter /adbe.pkcs7.detached``."""
    match = re.search(rb"/" + key + rb"\s*/([^\s/<>\[\]()]+)", window)
    return match.group(1).decode("latin-1", "replace") if match else ""


def _field_names_by_byterange(pdf: bytes) -> dict[tuple[int, ...], str]:
    """Map /ByteRange to field name using the AcroForm signature widgets."""
    names: dict[tuple[int, ...], str] = {}
    try:
        reader = PdfReader(BytesIO(pdf))
        fields = reader.get_fields() or {}
        for name, field in fields.items():
            value = field.get("/V")
            if value is None:
                continue
            dictionary = value.get_object()
            if str(dictionary.get("/Type", "")) != "/Sig":
                continue
            ranges = dictionary.get("/ByteRange")
            if ranges is None:
                continue
            key = tuple(int(item) for item in ranges)
            names.setdefault(key, str(name))
    except Exception:
        return names
    return names


def list_signatures(pdf: bytes) -> list[PdfField]:
    """Return every signature dictionary found in the file."""
    fields: list[PdfField] = []
    seen: set[tuple[int, ...]] = set()
    names = _field_names_by_byterange(pdf)
    for match in _BYTE_RANGE_RE.finditer(pdf):
        values = _parse_numbers(match.group(1))
        if len(values) != 4 or any(value < 0 for value in values):
            continue
        key = tuple(values)
        if key in seen:
            continue
        seen.add(key)
        offset, length, second, second_length = values
        field = PdfField(index=len(fields), field_name=f"Signature{len(fields) + 1}")
        field.byte_range = values
        if offset + length > len(pdf) or second + second_length > len(pdf):
            fields.append(field)
            continue
        gap_start = offset + length
        gap_end = second
        contents, _ = _extract_hex_at(pdf, gap_start)
        field.contents = _trim_der(contents)
        field.contents_offset = gap_start
        end_of_coverage = second + second_length
        field.modified_after_signing = end_of_coverage < len(pdf) and not _whitespace_only(
            pdf[end_of_coverage:]
        )
        field.covers_whole_file = not field.modified_after_signing and gap_start <= gap_end
        # /Type /Sig sits before /Contents inside the same dictionary; anchoring
        # the search window there prevents picking up an earlier revision.
        dictionary_start = pdf.rfind(b"/Type /Sig", max(0, gap_start - 8192), gap_start)
        window_start = dictionary_start if dictionary_start >= 0 else gap_start
        window = pdf[window_start : min(len(pdf), gap_end + 4096)]
        field.sub_filter = _pdf_name(window, b"SubFilter")
        field.signature_time = _pdf_string(window, b"M")
        if key in names:
            field.field_name = names[key]
        if field.contents and gap_start <= gap_end:
            field.signed_bytes = pdf[offset : offset + length] + pdf[second : end_of_coverage]
        fields.append(field)
    return fields
