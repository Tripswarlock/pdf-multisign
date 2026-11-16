"""LAN signing sessions.

One machine hosts the document; other participants join over HTTP, sign the
current revision with their local CryptoPro key and send the detached CMS back.
The host verifies the CMS ``messageDigest`` against the document bytes and
appends a new incremental revision, so signatures form a strict sequence
(each next participant signs the document together with the previous
signatures — the «последовательная подпись» model).

The protocol is plain JSON over HTTP on the local network; every request must
carry the ``X-Session-Code`` header shown on the host.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import socket
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import crypto, pdf as pdfmod, service

DEFAULT_PORT = 8471
APP_ID = "pdf-multisign"

STATUS_WAITING = "waiting"
STATUS_SIGNED = "signed"
STATUS_ERROR = "error"

# How long a prepared (not yet committed) signing request stays reserved.
PENDING_TTL_SECONDS = 300


class SessionError(RuntimeError):
    """Protocol level failure with an HTTP status for the response."""

    def __init__(self, message: str, code: str = "SESSION_ERROR", status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def make_code() -> str:
    """Six digit session code."""
    return f"{int.from_bytes(uuid.uuid4().bytes[:4], 'big') % 900000 + 100000:06d}"


def computer_name() -> str:
    return socket.gethostname()


def local_addresses() -> list[str]:
    """IPv4 addresses of this machine suitable for showing to participants."""
    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 80))  # TEST-NET-1: no packet is actually sent
        addresses.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    return sorted(addresses)


# ---------------------------------------------------------------------------
# Certificate (de)serialisation
# ---------------------------------------------------------------------------

def _iso(moment: dt.datetime | None) -> str:
    return moment.isoformat(timespec="seconds") if moment else ""


def _parse_iso(value: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def cert_to_dict(cert) -> dict:
    return {
        "thumbprint": cert.thumbprint,
        "subject": cert.subject,
        "issuer": cert.issuer,
        "serial": cert.serial,
        "common_name": cert.common_name,
        "surname": cert.surname,
        "given_name": cert.given_name,
        "organization": cert.organization,
        "organizational_unit": cert.organizational_unit,
        "position": cert.position,
        "inn": cert.inn,
        "snils": cert.snils,
        "email": cert.email,
        "not_before": _iso(cert.not_before),
        "not_after": _iso(cert.not_after),
    }


def cert_from_dict(data: dict) -> crypto.CertificateInfo:
    info = crypto.CertificateInfo()
    info.thumbprint = str(data.get("thumbprint") or "")
    info.subject = str(data.get("subject") or "")
    info.issuer = str(data.get("issuer") or "")
    info.serial = str(data.get("serial") or "")
    info.common_name = str(data.get("common_name") or "")
    info.surname = str(data.get("surname") or "")
    info.given_name = str(data.get("given_name") or "")
    info.organization = str(data.get("organization") or "")
    info.organizational_unit = str(data.get("organizational_unit") or "")
    info.position = str(data.get("position") or "")
    info.inn = str(data.get("inn") or "")
    info.snils = str(data.get("snils") or "")
    info.email = str(data.get("email") or "")
    info.not_before = _parse_iso(str(data.get("not_before") or ""))
    info.not_after = _parse_iso(str(data.get("not_after") or ""))
    info.is_gost = True
    info.is_crypto_pro = True
    info.sign_capable = True
    return info


# ---------------------------------------------------------------------------
# Session state (host side)
# ---------------------------------------------------------------------------

@dataclass
class Participant:
    pid: str
    name: str
    status: str = STATUS_WAITING
    detail: str = "подключился"
    at: dt.datetime = field(default_factory=dt.datetime.now)

    def as_dict(self) -> dict:
        return {
            "pid": self.pid,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "at": self.at.strftime("%H:%M:%S"),
        }


class SessionState:
    """Document and participants of one signing session; thread safe.

    Signing is two-phase: the participant calls :meth:`prepare_signature` and
    receives the exact bytes to sign (the two /ByteRange segments of a prepared
    revision), signs them with a local key and then calls
    :meth:`commit_signature`.  Only one prepared revision exists at a time,
    which keeps the signatures strictly sequential.
    """

    def __init__(
        self,
        pdf_bytes: bytes,
        file_name: str,
        code: str,
        *,
        visible: bool = False,
        reason: str = "Подписание документа",
    ) -> None:
        self._lock = threading.Lock()
        self._pdf = pdf_bytes
        self.file_name = file_name
        self.code = code
        self.visible = visible
        self.reason = reason or "Подписание документа"
        self.participants: dict[str, Participant] = {}
        self._pending: tuple[str, dt.datetime, object] | None = None

    @property
    def pdf_bytes(self) -> bytes:
        with self._lock:
            return self._pdf

    def document(self) -> bytes:
        return self.pdf_bytes

    def info(self) -> dict:
        with self._lock:
            pdf = self._pdf
            participants = [p.as_dict() for p in self.participants.values()]
        try:
            signatures = len(pdfmod.list_signatures(pdf))
        except Exception:
            signatures = 0
        return {
            "app": APP_ID,
            "file": self.file_name,
            "page_count": service.page_count(pdf),
            "signatures": signatures,
            "visible_stamp": self.visible,
            "reason": self.reason,
            "participants": participants,
        }

    def join(self, name: str) -> str:
        pid = uuid.uuid4().hex[:12]
        with self._lock:
            self.participants[pid] = Participant(pid=pid, name=name.strip() or "Участник")
        return pid

    # ------------------------------------------------------------- signing
    def _participant(self, pid: str) -> Participant:
        participant = self.participants.get(pid)
        if participant is None:
            raise SessionError("Участник сессии не найден", "UNKNOWN_PARTICIPANT", 403)
        return participant

    def _build_request(self, cert_dict: dict, existing: int, page_index: int,
                       page_width: float, page_height: float) -> "pdfmod.SignRequest":
        certificate = cert_from_dict(cert_dict)
        request = pdfmod.SignRequest(
            thumbprint=certificate.thumbprint,
            reason=self.reason,
            location="",
            signer_name=certificate.display_name,
            page_index=page_index,
            visible=self.visible,
            rect=None,
            certificate_info=certificate,
            stamp_title="ДОКУМЕНТ ПОДПИСАН ЭЛЕКТРОННОЙ ПОДПИСЬЮ",
        )
        if self.visible:
            request.rect = service.stacked_rects(existing + 1, page_width, page_height)[existing]
        return request

    def prepare_signature(self, pid: str, cert_dict: dict) -> bytes:
        """Reserve a revision and return the exact bytes the client must sign."""
        with self._lock:
            participant = self._participant(pid)
            if self._pending is not None:
                owner, created, _ = self._pending
                if (dt.datetime.now() - created).total_seconds() < PENDING_TTL_SECONDS:
                    raise SessionError(
                        "Другой участник сейчас оформляет подпись. "
                        "Подождите несколько секунд и повторите попытку.",
                        "BUSY",
                        409,
                    )
                self._pending = None
            current = self._pdf
            existing = len(pdfmod.list_signatures(current))
            page_index = service.page_count(current) - 1
            sizes = service.page_sizes(current)
            page_width, page_height = sizes[page_index]
            request = self._build_request(cert_dict, existing, page_index,
                                          page_width, page_height)
            try:
                pending = pdfmod.prepare_signature(
                    current, request, field_name=f"Signature{existing + 1}"
                )
            except pdfmod.PdfSignError as exc:
                raise SessionError(f"Не удалось подготовить документ: {exc}", "PREPARE_FAILED", 500)
            self._pending = (pid, dt.datetime.now(), pending)
            participant.status = STATUS_WAITING
            participant.detail = "подписывает…"
            participant.at = dt.datetime.now()
            return pending.signed_bytes

    def commit_signature(self, pid: str, cms: bytes) -> tuple[str, str]:
        """Verify the CMS against the prepared bytes and append the revision."""
        with self._lock:
            participant = self._participant(pid)
            if self._pending is None or self._pending[0] != pid:
                raise SessionError(
                    "Подготовленная подпись не найдена или сессия её уже закрыла. "
                    "Начните подписание заново.",
                    "NO_PENDING",
                    409,
                )
            _, _, pending = self._pending

            expected = crypto.cms_message_digest(cms)
            actual = crypto.streebog256(pending.signed_bytes)
            if expected is None or actual is None or expected != actual:
                self._pending = None
                participant.status = STATUS_ERROR
                participant.detail = "подпись не соответствует подготовленным данным"
                participant.at = dt.datetime.now()
                raise SessionError(
                    "Подпись не соответствует подготовленным данным. Начните подписание заново.",
                    "DIGEST_MISMATCH",
                    409,
                )
            try:
                self._pdf = pending.commit(cms)
            except pdfmod.PdfSignError as exc:
                self._pending = None
                participant.status = STATUS_ERROR
                participant.detail = f"не удалось встроить подпись: {exc}"
                participant.at = dt.datetime.now()
                raise SessionError(
                    f"Не удалось встроить подпись в документ: {exc}", "APPEND_FAILED", 500
                )
            self._pending = None
            participant.status = STATUS_SIGNED
            participant.detail = pending.field_name
            participant.at = dt.datetime.now()
            return pending.field_name, f"подпись принята ({pending.field_name})"

    def cancel_signature(self, pid: str) -> None:
        """Discard the prepared revision reserved for ``pid`` (client abort)."""
        with self._lock:
            if self._pending is not None and self._pending[0] == pid:
                self._pending = None
                participant = self.participants.get(pid)
                if participant is not None:
                    participant.detail = "отменил подписание"


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    server_version = "PDFMultisignSession/1.1"

    def log_message(self, fmt, *args):  # keep the GUI log clean
        return

    def _authorized(self) -> bool:
        if self.headers.get("X-Session-Code", "") != self.server.state.code:
            self._json(403, {"ok": False, "error": "Неверный код сессии", "code": "BAD_CODE"})
            return False
        return True

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > 32 * 1024 * 1024:
            raise SessionError("Пустой или слишком большой запрос", "BAD_REQUEST", 400)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:
            raise SessionError(f"Неверный формат запроса: {exc}", "BAD_REQUEST", 400)
        if not isinstance(payload, dict):
            raise SessionError("Неверный формат запроса", "BAD_REQUEST", 400)
        return payload

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        if not self._authorized():
            return
        if self.path.startswith("/api/info"):
            self._json(200, {"ok": True, "info": self.server.state.info()})
        elif self.path.startswith("/api/document"):
            data = self.server.state.document()
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._json(404, {"ok": False, "error": "Неизвестный запрос", "code": "NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802 (stdlib naming)
        if not self._authorized():
            return
        try:
            payload = self._read_json()
            if self.path.startswith("/api/join"):
                pid = self.server.state.join(str(payload.get("name") or "Участник"))
                self._json(200, {"ok": True, "pid": pid})
            elif self.path.startswith("/api/prepare"):
                signed_bytes = self.server.state.prepare_signature(
                    str(payload.get("pid") or ""),
                    payload.get("certificate") or {},
                )
                self._json(200, {
                    "ok": True,
                    "signed_bytes": base64.b64encode(signed_bytes).decode("ascii"),
                })
            elif self.path.startswith("/api/commit"):
                try:
                    cms = base64.b64decode(payload["cms"], validate=True)
                except Exception:
                    raise SessionError("Подпись передана в неверном формате", "BAD_CMS", 400)
                field, detail = self.server.state.commit_signature(
                    str(payload.get("pid") or ""), cms
                )
                self._json(200, {"ok": True, "field": field, "detail": detail})
            elif self.path.startswith("/api/cancel"):
                self.server.state.cancel_signature(str(payload.get("pid") or ""))
                self._json(200, {"ok": True})
            else:
                self._json(404, {"ok": False, "error": "Неизвестный запрос", "code": "NOT_FOUND"})
        except SessionError as exc:
            self._json(exc.status, {"ok": False, "error": str(exc), "code": exc.code})
        except Exception as exc:  # pragma: no cover - defensive
            self._json(500, {"ok": False, "error": f"Внутренняя ошибка сервера: {exc}"})


class SessionServer:
    """Thin wrapper around ThreadingHTTPServer running in a daemon thread."""

    def __init__(self, state: SessionState, port: int = DEFAULT_PORT) -> None:
        self.state = state
        self._httpd = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
        self._httpd.state = state
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="pdf-multisign-session", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


# ---------------------------------------------------------------------------
# Client helpers
# ---------------------------------------------------------------------------

def _request(base: str, code: str, path: str, payload: dict | None = None,
             timeout: float = 60.0) -> tuple[bytes, str]:
    url = base.rstrip("/") + path
    data = None
    headers = {"X-Session-Code": code, "User-Agent": f"{APP_ID}/1.1"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(url, data=data, headers=headers,
                      method="POST" if payload is not None else "GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read(), response.headers.get("Content-Type", "")
    except HTTPError as exc:
        message, code = "", ""
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            message = str(payload.get("error") or "")
            code = str(payload.get("code") or "")
        except Exception:
            pass
        raise SessionError(message or f"HTTP {exc.code}", code or "HTTP_ERROR", status=exc.code)
    except URLError as exc:
        reason = exc.reason if hasattr(exc, "reason") else exc
        raise SessionError(f"Не удалось подключиться к {url}: {reason}", "CONNECT_FAILED")
    except OSError as exc:
        raise SessionError(f"Сетевая ошибка: {exc}", "CONNECT_FAILED")


def _request_json(base: str, code: str, path: str, payload: dict | None = None) -> dict:
    body, _ = _request(base, code, path, payload)
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise SessionError(f"Неверный ответ сервера: {exc}", "BAD_RESPONSE")
    if not isinstance(data, dict) or not data.get("ok"):
        raise SessionError(str(data.get("error") or "Сервер вернул ошибку"), "SERVER_ERROR")
    return data


def join_session(base: str, code: str, name: str) -> tuple[str, dict]:
    data = _request_json(base, code, "/api/join", {"name": name})
    info = _request_json(base, code, "/api/info")["info"]
    return str(data["pid"]), info


def session_info(base: str, code: str) -> dict:
    return _request_json(base, code, "/api/info")["info"]


def fetch_document(base: str, code: str) -> bytes:
    body, content_type = _request(base, code, "/api/document", timeout=120.0)
    if "pdf" not in content_type and not body.startswith(b"%PDF-"):
        raise SessionError("Сервер вернул не PDF", "BAD_DOCUMENT")
    return body


def prepare_signature(base: str, code: str, pid: str, cert_dict: dict) -> bytes:
    """Ask the host to reserve a revision; returns the exact bytes to sign."""
    data = _request_json(base, code, "/api/prepare", {"pid": pid, "certificate": cert_dict})
    try:
        return base64.b64decode(data["signed_bytes"], validate=True)
    except Exception as exc:
        raise SessionError(f"Неверный ответ сервера: {exc}", "BAD_RESPONSE")


def commit_signature(base: str, code: str, pid: str, cms: bytes) -> tuple[str, str]:
    payload = {
        "pid": pid,
        "cms": base64.b64encode(cms).decode("ascii"),
    }
    data = _request_json(base, code, "/api/commit", payload)
    return str(data.get("field") or ""), str(data.get("detail") or "подпись принята")


def cancel_signature(base: str, code: str, pid: str) -> None:
    try:
        _request_json(base, code, "/api/cancel", {"pid": pid})
    except SessionError:
        pass
