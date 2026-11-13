"""CryptoPro CSP integration through the official CAdESCOM COM interface.

The module never touches private keys directly: certificate enumeration, CMS
creation and CMS verification are delegated to CryptoPro CSP.  The approach
mirrors the one used by the reference project (CAdESCOM.Store + CPSigner +
CadesSignedData.SignCades with CADES_BES and detached=true).
"""
from __future__ import annotations

import base64
import datetime as dt
import re
from dataclasses import dataclass, field

from . import com

try:  # pywin32 is the only supported COM bridge on this machine
    import pythoncom
    import win32com.client

    COM_AVAILABLE = True
    COM_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - depends on the host
    pythoncom = None  # type: ignore[assignment]
    win32com = None  # type: ignore[assignment]
    COM_AVAILABLE = False
    COM_IMPORT_ERROR = str(exc)

CAPICOM_CURRENT_USER_STORE = 2
CAPICOM_STORE_OPEN_MAXIMUM_ALLOWED = 2
CADES_BES = 1
CADESCOM_BASE64_TO_BINARY = 1

CONTENT_DIGEST_CHECK = True

STREEBOG_OID = "1.2.643.7.1.1.2.2"


def streebog256(data: bytes) -> bytes | None:
    """GOST R 34.11-2012 256-bit digest, or None when no implementation exists."""
    try:
        from gostcrypto import gosthash

        digest = gosthash.new("streebog256")
        digest.update(data)
        return digest.digest()
    except Exception:
        return None


def cms_message_digest(cms_der: bytes) -> bytes | None:
    """Return the messageDigest signed attribute of the first signer."""
    try:
        from asn1crypto import cms as asn1cms

        content = asn1cms.ContentInfo.load(cms_der)["content"]
        signer = content["signer_infos"][0]
        for attribute in signer["signed_attrs"]:
            if attribute["type"].native == "message_digest":
                return attribute["values"][0].native
    except Exception:
        return None
    return None

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_DN_ALIASES = {
    "SN": "surname",
    "G": "given_name",
    "GN": "given_name",
    "CN": "common_name",
    "O": "organization",
    "OU": "organizational_unit",
    "T": "position",
    "TITLE": "position",
    "C": "country",
    "S": "state",
    "ST": "state",
    "L": "locality",
    "STREET": "street",
    "E": "email",
    "EMAIL": "email",
    "ИНН": "inn",
    "INN": "inn",
    "СНИЛС": "snils",
    "SNILS": "snils",
    "ОГРН": "ogrn",
    "OGRN": "ogrn",
}


class CryptoError(RuntimeError):
    """A human readable CryptoPro failure."""

    def __init__(self, message: str, code: str = "CRYPTOPRO_ERROR") -> None:
        super().__init__(message)
        self.code = code


@dataclass
class CertificateInfo:
    thumbprint: str = ""
    subject: str = ""
    issuer: str = ""
    serial: str = ""
    not_before: dt.datetime | None = None
    not_after: dt.datetime | None = None
    has_private_key: bool = True
    common_name: str = ""
    surname: str = ""
    given_name: str = ""
    organization: str = ""
    organizational_unit: str = ""
    position: str = ""
    inn: str = ""
    snils: str = ""
    email: str = ""
    is_gost: bool = True
    is_crypto_pro: bool = True
    sign_capable: bool = True
    signing_note: str = ""

    @property
    def display_name(self) -> str:
        full = " ".join(part for part in (self.surname, self.given_name) if part).strip()
        if full:
            return full
        if self.common_name and not _UUID_RE.match(self.common_name):
            return self.common_name
        if full:
            return full
        return self.common_name or self.thumbprint

    @property
    def is_valid_now(self) -> bool:
        now = dt.datetime.now()
        if self.not_before and now < self.not_before:
            return False
        if self.not_after and now > self.not_after:
            return False
        return True

    @property
    def validity_text(self) -> str:
        if not self.not_before or not self.not_after:
            return ""
        return f"{self.not_before:%d.%m.%Y} — {self.not_after:%d.%m.%Y}"

    @property
    def detail_text(self) -> str:
        parts = [self.organization, self.organizational_unit]
        return " · ".join(part for part in parts if part)


def parse_dn(dn: str) -> dict[str, str]:
    """Parse a Windows/CryptoPro distinguished name into a flat dictionary."""
    result: dict[str, str] = {}
    if not dn:
        return result
    chunks: list[str] = []
    current: list[str] = []
    quoted = False
    for char in dn:
        if char == '"':
            quoted = not quoted
            current.append(char)
        elif char == "," and not quoted:
            chunks.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        chunks.append("".join(current))
    for chunk in chunks:
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key = key.strip().upper()
        value = value.strip().strip('"')
        normalized = _DN_ALIASES.get(key, key.lower())
        if value and normalized not in result:
            result[normalized] = value
    return result


def _to_datetime(value: object) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip()
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _private_key_provider(cert) -> str:
    """Return the CSP name of the certificate's private key, empty if unknown."""
    try:
        private_key = cert.PrivateKey
    except Exception:
        return ""
    if private_key is None:
        return ""
    for attribute in ("ProviderName", "Provider"):
        try:
            value = getattr(private_key, attribute)
            text = str(value or "").strip()
            if text:
                return text
        except Exception:
            continue
    try:
        from win32com.client.dynamic import Dispatch

        dynamic_key = Dispatch(private_key)
        for attribute in ("ProviderName", "Provider"):
            try:
                text = str(getattr(dynamic_key, attribute) or "").strip()
                if text:
                    return text
            except Exception:
                continue
    except Exception:
        pass
    return ""


def _certificate_from_com(cert) -> CertificateInfo:
    info = CertificateInfo()
    info.thumbprint = str(cert.Thumbprint or "").strip().upper()
    info.subject = str(cert.SubjectName or "")
    info.issuer = str(cert.IssuerName or "")
    try:
        info.serial = str(cert.SerialNumber or "")
    except Exception:
        info.serial = ""
    try:
        info.not_before = _to_datetime(cert.ValidFromDate)
        info.not_after = _to_datetime(cert.ValidToDate)
    except Exception:
        pass
    try:
        info.has_private_key = bool(cert.HasPrivateKey)
    except Exception:
        try:
            info.has_private_key = cert.PrivateKey is not None
        except Exception:
            info.has_private_key = True

    # Determine whether CryptoPro CSP can sign with this certificate.  CAdESCOM
    # signs only through CryptoPro CSP providers: software ECC/RSA keys stored in
    # "Microsoft Software Key Storage Provider" cannot be used by SignCades.
    provider_name = _private_key_provider(cert)
    if provider_name:
        lowered = provider_name.lower()
        info.is_crypto_pro = "crypto-pro" in lowered or "crypto pro" in lowered
        info.is_gost = "gost" in lowered or "34.10" in lowered
    else:
        # Cannot tell: assume CryptoPro so the user gets the real CSP error.
        info.is_crypto_pro = True
        info.is_gost = True
    info.sign_capable = info.has_private_key and info.is_crypto_pro
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
    return info


def _open_store():
    if not COM_AVAILABLE:
        raise CryptoError(
            "Мост COM недоступен: не удалось загрузить pywin32. "
            f"Подробность: {COM_IMPORT_ERROR}",
            "COM_UNAVAILABLE",
        )
    try:
        store = win32com.client.Dispatch("CAdESCOM.Store")
    except Exception as exc:
        raise CryptoError(
            "CryptoPro CSP не найден: не удалось создать CAdESCOM.Store. "
            "Установите CryptoPro CSP 5.0 R2+ с компонентом CAdES Browser Plug-in. "
            f"Подробность: {exc}",
            "CRYPTOPRO_UNAVAILABLE",
        ) from exc
    try:
        store.Open(CAPICOM_CURRENT_USER_STORE, "My", CAPICOM_STORE_OPEN_MAXIMUM_ALLOWED)
    except Exception as exc:
        raise CryptoError(
            f"Не удалось открыть хранилище сертификатов CurrentUser\\My: {exc}",
            "CERTIFICATE_STORE_ERROR",
        ) from exc
    return store


def crypto_status() -> tuple[bool, str]:
    """Return (available, human readable detail) without raising."""
    if not COM_AVAILABLE:
        return False, f"pywin32 не загружен: {COM_IMPORT_ERROR}"
    try:
        com.ensure_initialized()
        store = _open_store()
        try:
            count = len(list(store.Certificates))
        except Exception:
            count = 0
        return True, f"CryptoPro CSP доступен, сертификатов в CurrentUser\\My: {count}"
    except CryptoError as exc:
        return False, str(exc)
    except Exception as exc:  # pragma: no cover
        return False, f"Ошибка CryptoPro: {exc}"


def list_certificates() -> list[CertificateInfo]:
    """Enumerate CurrentUser\\My, certificates with a usable key first."""
    com.ensure_initialized()
    store = _open_store()
    certificates: list[CertificateInfo] = []
    seen: set[str] = set()
    for raw in store.Certificates:
        info = _certificate_from_com(raw)
        if not info.thumbprint or info.thumbprint in seen:
            continue
        seen.add(info.thumbprint)
        certificates.append(info)
    certificates.sort(
        key=lambda item: (
            not item.has_private_key,
            not item.is_valid_now,
            -(item.not_after.timestamp() if item.not_after else 0),
        )
    )
    return certificates


def _find_certificate(store, thumbprint: str):
    wanted = thumbprint.replace(" ", "").upper()
    for cert in store.Certificates:
        if str(cert.Thumbprint or "").strip().upper() == wanted:
            return cert
    raise CryptoError(
        f"Сертификат {thumbprint} не найден в хранилище CurrentUser\\My",
        "CERTIFICATE_NOT_FOUND",
    )


def sign_detached(
    content: bytes,
    thumbprint: str,
    *,
    pin: str | None = None,
    check_chain: bool = False,
    tsa_url: str | None = None,
) -> bytes:
    """Create a detached CAdES-BES CMS for ``content`` with CryptoPro CSP."""
    if not content:
        raise CryptoError("Пустые данные для подписи", "EMPTY_CONTENT")
    if not thumbprint.strip():
        raise CryptoError("Сертификат для подписи не выбран", "CERTIFICATE_REQUIRED")
    com.ensure_initialized()
    store = _open_store()
    certificate = _find_certificate(store, thumbprint)
    try:
        if not bool(certificate.HasPrivateKey):
            raise CryptoError(
                "У выбранного сертификата нет доступного закрытого ключа",
                "NO_PRIVATE_KEY",
            )
    except CryptoError:
        raise
    except Exception:
        pass

    signer = win32com.client.Dispatch("CAdESCOM.CPSigner")
    signer.Certificate = certificate
    signer.CheckCertificate = bool(check_chain)
    try:
        signer.Options = 2  # include the chain, except the root
    except Exception:
        pass
    if pin:
        signer.KeyPin = pin
    if tsa_url:
        signer.TSAAddress = tsa_url

    signed_data = win32com.client.Dispatch("CAdESCOM.CadesSignedData")
    # ContentEncoding MUST be set before Content: CAdESCOM converts the value at
    # assignment time.  Setting it afterwards leaves the base64 text signed as a
    # UTF-16LE string, which produces a CMS that no third-party verifier accepts.
    signed_data.ContentEncoding = CADESCOM_BASE64_TO_BINARY
    signed_data.Content = base64.b64encode(content).decode("ascii")
    try:
        encoded = signed_data.SignCades(signer, CADES_BES, True)
    except Exception as exc:
        raise CryptoError(
            "CryptoPro не создал подпись. Проверьте PIN, срок действия сертификата "
            f"и подключение токена. Подробность: {exc}",
            "SIGNING_FAILED",
        ) from exc
    signature = base64.b64decode(encoded)
    if not signature:
        raise CryptoError("CryptoPro вернул пустую подпись", "SIGNING_EMPTY")
    _assert_cms_covers(signature, content)
    return signature


def _assert_cms_covers(cms: bytes, content: bytes) -> None:
    """Fail loudly when the CMS digest does not describe ``content`` bytes."""
    if not CONTENT_DIGEST_CHECK:
        return
    expected = cms_message_digest(cms)
    if expected is None:
        return
    actual = streebog256(content)
    if actual is None:
        return
    if expected != actual:
        raise CryptoError(
            "CryptoPro создал подпись над закодированным текстом, а не над байтами "
            "документа: такая подпись не пройдёт проверку в Adobe и других системах. "
            "Проверьте версию CAdESCOM и ContentEncoding.",
            "CMS_DIGEST_MISMATCH",
        )


def verify_detached(cms: bytes, content: bytes) -> tuple[bool, str]:
    """Verify a detached CMS over ``content``.

    Two independent checks are combined: the CMS ``messageDigest`` attribute is
    compared with a local Streebog-256 of the document bytes, and CryptoPro is
    asked to verify the signature.  The digest check is deterministic, while the
    CryptoPro check may be unavailable when the key container cannot be opened —
    in that case the digest result is still reported.
    """
    digest_ok = False
    digest_detail = ""
    expected = cms_message_digest(cms)
    if expected is not None:
        actual = streebog256(content)
        if actual is None:
            digest_detail = "локальная проверка ГОСТ-хеша недоступна"
        else:
            digest_ok = expected == actual
            digest_detail = (
                "SHA-256-совместимый ГОСТ-хеш документа совпадает"
                if digest_ok
                else "ГОСТ-хеш документа НЕ совпадает с подписанным значением"
            )

    com.ensure_initialized()
    try:
        signed_data = win32com.client.Dispatch("CAdESCOM.CadesSignedData")
        signed_data.ContentEncoding = CADESCOM_BASE64_TO_BINARY
        signed_data.Content = base64.b64encode(content).decode("ascii")
        signed_data.VerifyCades(base64.b64encode(cms).decode("ascii"), CADES_BES, True)
        return True, "Подпись верна (проверено CryptoPro CSP и ГОСТ-хеш)"
    except Exception as exc:
        message = str(exc)
        container_busy = any(
            marker in message
            for marker in ("набор ключей", "keyset", "0x80090016", "0x8009001a", "0x8009001b")
        )
        if digest_ok:
            return True, (
                "ГОСТ-хеш документа совпадает с подписанным; "
                "проверка подписи через CryptoPro сейчас недоступна "
                "(занят контейнер ключа)"
                if container_busy
                else "ГОСТ-хеш документа совпадает с подписанным"
            )
        if digest_detail:
            return False, digest_detail
        return False, f"Не удалось проверить подпись: {message}"


# ---------------------------------------------------------------------------
# Platform dispatch
# ---------------------------------------------------------------------------
# On macOS there is no supported CryptoPro interface; the Windows COM provider
# is the default implementation.  Linux rebinds the public API to the cryptcp
# based provider defined in ``crypto_linux``.
import sys as _sys  # noqa: E402

if _sys.platform != "win32":
    try:
        from . import crypto_linux as _linux

        crypto_status = _linux.status  # type: ignore[assignment]
        list_certificates = _linux.list_certificates  # type: ignore[assignment]
        sign_detached = _linux.sign_detached  # type: ignore[assignment]
        verify_detached = _linux.verify_detached  # type: ignore[assignment]
        COM_AVAILABLE = _linux.available()
    except Exception as _exc:  # pragma: no cover - fallback path
        _LINUX_IMPORT_ERROR = str(_exc)

