"""CryptoPro CSP on Linux through the official CLI (cryptcp / certmgr).

Mirrors the approach of the reference project: signing and verification are
delegated to CryptoPro's own command line tools, the PIN is passed on stdin (not
in the command line), and no private key material is read.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import subprocess
import tempfile

from .crypto import (
    CertificateInfo,
    CryptoError,
    cms_digest_status,
    parse_dn,
)

CRYPTOPRO_ROOTS = ("/opt/cprocsp/bin/amd64", "/opt/cprocsp/bin/aarch64", "/opt/cprocsp/bin")

_PEM_RE = re.compile(
    r"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", re.DOTALL
)


def _find_tool(name: str) -> str:
    for root in CRYPTOPRO_ROOTS:
        candidate = os.path.join(root, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which(name) or ""


def cryptcp_path() -> str:
    return _find_tool("cryptcp")


def certmgr_path() -> str:
    return _find_tool("certmgr")


def available() -> bool:
    return bool(cryptcp_path() or certmgr_path())


def status() -> tuple[bool, str]:
    cp = cryptcp_path()
    cm = certmgr_path()
    if not cp and not cm:
        return False, (
            "CryptoPro CSP для Linux не найден: нет /opt/cprocsp/bin/*/cryptcp и certmgr. "
            "Установите CryptoPro CSP и пакет cprocsp-cptools."
        )
    parts = []
    if cp:
        parts.append(f"cryptcp: {cp}")
    if cm:
        parts.append(f"certmgr: {cm}")
    return True, "CryptoPro CSP доступен (" + "; ".join(parts) + ")"


def _run(args: list[str], stdin_data: bytes | None = None, timeout: int = 120):
    # CryptoPro tools are localized: on a Russian Astra they print «Субъект»,
    # «Издатель», «SHA1 отпечаток», which the parser cannot read.  A fixed C
    # locale gives stable English field names (the DN values stay as they are).
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env["LANGUAGE"] = "C"
    try:
        process = subprocess.run(
            args,
            input=stdin_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=env,
        )
        return process.returncode, process.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, "Таймаут операции CryptoPro"
    except OSError as exc:
        return 127, f"Не удалось запустить {args[0]}: {exc}"


def _parse_certmgr(output: str) -> list[CertificateInfo]:
    """Parse ``certmgr -list`` output into certificate descriptors.

    Both the English (C locale) and Russian (system locale) field names are
    accepted, so the parser works even if CryptoPro ignores LC_ALL.
    """
    certificates: list[CertificateInfo] = []
    for block in re.split(r"\n-{2,}\s*\n|\n\d+-{3,}", output):
        subject = _field_any(block, "Subject", "Субъект")
        if not subject:
            continue
        info = CertificateInfo()
        info.subject = subject
        info.issuer = _field_any(block, "Issuer", "Издатель")
        info.serial = _field_any(block, "Serial", "Серийный номер")
        thumb = _field_any(
            block, "SHA1 Thumbprint", "SHA1 отпечаток", "Отпечаток SHA1"
        )
        info.thumbprint = thumb.replace(" ", "").replace(":", "").upper()
        container = _field_any(block, "Container", "Контейнер")
        info.container_name = container
        provider = _field_any(block, "Provider Name", "Имя провайдера")
        info.is_crypto_pro = "crypto-pro" in provider.lower() or "crypto pro" in provider.lower()
        info.is_gost = "34.10" in provider or "gost" in provider.lower()
        link = _field_any(block, "PrivateKey Link", "Ссылка на ключ")
        info.has_private_key = (
            link.lower().startswith(("yes", "есть")) or bool(container)
        )
        if not thumb:
            continue
        info.sign_capable = info.has_private_key and info.is_crypto_pro and info.is_gost
        if info.has_private_key and not info.is_crypto_pro:
            info.signing_note = "Ключ вне CryptoPro CSP — подпись недоступна"

        dn = parse_dn(info.subject)
        info.common_name = dn.get("common_name", "")
        info.surname = dn.get("surname", "")
        info.given_name = dn.get("given_name", "")
        info.organization = dn.get("organization", "")
        info.organizational_unit = dn.get("organizational_unit", "")
        info.position = dn.get("position", "")
        info.inn = dn.get("inn", "")
        info.snils = dn.get("snils", "")
        info.email = dn.get("email", "")
        not_before, not_after = _validity(block)
        info.not_before = not_before
        info.not_after = not_after
        certificates.append(info)
    return certificates


def list_containers() -> list[str]:
    """Private-key containers known to CryptoPro (token or file based)."""
    tool = _find_tool("csptest")
    if not tool:
        return []
    code, output = _run([tool, "-keyset", "-enum_containers", "-verifyc", "-silent"], timeout=60)
    containers: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("\\\\.\\"):
            continue
        name = line.rstrip()
        if name and name not in containers:
            containers.append(name)
    return containers


def readers_present() -> bool | None:
    """True/False when the check is possible, None when csptest is missing."""
    tool = _find_tool("csptest")
    if not tool:
        return None
    _code, output = _run([tool, "-card", "-enum"], timeout=30)
    if "SCARD_E_NO_READERS_AVAILABLE" in output or "0x8010002e" in output:
        return False
    return True


def store_hint() -> str:
    """Actionable hint when the certificate list is empty."""
    notes = [
        "certmgr -list -store uMy не вернул сертификатов.",
        "Установите сертификат в хранилище пользователя: "
        "certmgr -inst -store uMy -file cert.cer (или -pfx).",
    ]
    readers = readers_present()
    if readers is False:
        notes.append(
            "Смарт-карт-ридеры не обнаружены: если ключ на токене, пробросьте "
            "устройство в систему (usbipd/USB для ВМ) и проверьте pcscd."
        )
    containers = list_containers()
    if containers:
        notes.append(
            "Найдены контейнеры без установленного сертификата: "
            + ", ".join(containers[:5])
            + ". Установите сертификат из контейнера: certmgr -inst -store uMy -cont <контейнер>."
        )
    else:
        notes.append("Контейнеры закрытых ключей не найдены.")
    return " ".join(notes)


def list_certificates() -> list[CertificateInfo]:
    tool = certmgr_path()
    if not tool:
        raise CryptoError(
            "certmgr из CryptoPro CSP не найден — список сертификатов недоступен",
            "CRYPTOPRO_UNAVAILABLE",
        )
    code, output = _run([tool, "-list", "-store", "uMy"], timeout=60)
    empty = "Список сертификатов пуст" in output or "Требуемый сертификат не существует" in output
    if code not in (0, 1) and not empty:
        raise CryptoError(
            "certmgr завершился с ошибкой: " + output.strip().splitlines()[0][:160],
            "CERTMGR_ERROR",
        )

    certificates = _parse_certmgr(output)

    # The token may hold a certificate that is not imported into uMy yet.
    # Enumerate private-key containers and try to read certificates from them.
    if not certificates:
        for container in list_containers():
            _code, dump = _run([tool, "-list", "-container", container], timeout=60)
            certificates.extend(_parse_certmgr(dump))

    seen: set[str] = set()
    unique: list[CertificateInfo] = []
    for certificate in certificates:
        if not certificate.thumbprint or certificate.thumbprint in seen:
            continue
        seen.add(certificate.thumbprint)
        unique.append(certificate)

    unique.sort(
        key=lambda item: (
            not item.sign_capable,
            not item.is_valid_now,
            -(item.not_after.timestamp() if item.not_after else 0),
        )
    )
    return unique


def _field(block: str, name: str) -> str:
    match = re.search(rf"{re.escape(name)}\s*:\s*(.+)", block)
    return match.group(1).strip() if match else ""


def _field_any(block: str, *names: str) -> str:
    """First non-empty field among English/Russian aliases."""
    for name in names:
        value = _field(block, name)
        if value:
            return value
    return ""


def _validity(block: str) -> tuple[dt.datetime | None, dt.datetime | None]:
    def parse(label: str, *aliases: str) -> dt.datetime | None:
        raw = _field_any(block, label, *aliases)
        for fmt in (
            # CryptoPro prints dates as dd/mm/yyyy in both C and Russian locales
            "%d/%m/%Y %H:%M:%S UTC",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y",
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y",
            "%Y-%m-%d %H:%M:%S",
            "%m/%d/%Y %H:%M:%S UTC",
        ):
            try:
                return dt.datetime.strptime(raw, fmt)
            except ValueError:
                continue
        return None

    return (
        parse("Not valid before", "Выдан", "Действителен с"),
        parse("Not valid after", "Истекает", "Действителен до"),
    )


def sign_detached(
    content: bytes,
    thumbprint: str,
    *,
    pin: str | None = None,
    check_chain: bool = False,
    tsa_url: str | None = None,
) -> bytes:
    tool = cryptcp_path()
    if not tool:
        raise CryptoError(
            "cryptcp из CryptoPro CSP не найден — подпись недоступна", "CRYPTOPRO_UNAVAILABLE"
        )
    if not content:
        raise CryptoError("Пустые данные для подписи", "EMPTY_CONTENT")
    if not thumbprint.strip():
        raise CryptoError("Сертификат для подписи не выбран", "CERTIFICATE_REQUIRED")
    if tsa_url:
        # cryptcp supports -t but the offline profile deliberately ignores TSA.
        pass

    with tempfile.TemporaryDirectory(prefix="pdfmultisign-") as directory:
        input_path = os.path.join(directory, "input.bin")
        signature_path = os.path.join(directory, "signature.sig")
        with open(input_path, "wb") as handle:
            handle.write(content)
        args = [
            tool,
            "-sign",
            "-detach",
            "-thumbprint",
            thumbprint.replace(" ", ""),
            "-der",
            input_path,
            signature_path,
        ]
        stdin_data = (pin.encode() + b"\n") if pin else None
        code, output = _run(args, stdin_data=stdin_data, timeout=180)
        signature = b""
        if os.path.exists(signature_path):
            with open(signature_path, "rb") as handle:
                signature = handle.read()
        if code != 0 or not signature:
            raise CryptoError(
                "cryptcp не создал подпись. Проверьте PIN и подключение токена. "
                f"Ответ CryptoPro: {output.strip()[:200]}",
                "SIGNING_FAILED",
            )
        _assert_digest(signature, content)
        return signature


def _assert_digest(cms: bytes, content: bytes) -> None:
    status, note = cms_digest_status(cms, content)
    if status is False:
        raise CryptoError(
            "cryptcp подписал не байты документа: " + note + ". "
            "Проверьте параметры cryptcp -der / -detach.",
            "CMS_DIGEST_MISMATCH",
        )


def verify_detached(cms: bytes, content: bytes) -> tuple[bool, str]:
    tool = cryptcp_path()
    status, digest_note = cms_digest_status(cms, content)
    digest_ok = status is True

    if not tool:
        if status is True:
            return True, digest_note
        if status is False:
            return False, digest_note
        return False, "cryptcp не найден — проверка подписи недоступна"

    with tempfile.TemporaryDirectory(prefix="pdfmultisign-verify-") as directory:
        input_path = os.path.join(directory, "input.bin")
        signature_path = os.path.join(directory, "signature.sig")
        with open(input_path, "wb") as handle:
            handle.write(content)
        with open(signature_path, "wb") as handle:
            handle.write(cms)
        code, output = _run(
            [tool, "-verify", "-detach", signature_path, input_path], timeout=120
        )
        if code == 0:
            note = "Подпись верна (проверено cryptcp CryptoPro CSP)"
            if digest_ok:
                note += f"; {digest_note}"
            return True, note
        if digest_ok:
            return True, (
                digest_note
                + f"; cryptcp недоступен для полной проверки: {output.strip()[:120]}"
            )
        if status is False:
            return False, digest_note
        return False, f"cryptcp: {output.strip()[:200]}"
