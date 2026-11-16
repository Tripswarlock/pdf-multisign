"""LAN signing sessions: several computers sign one document with live statuses.

Two signing schemes are supported, matching the terminology from GOST R 34.10
practice and CryptoPro products:

* ``sequential`` (последовательная подпись) — every participant signs the
  current revision of the document, so each new signature covers all previous
  ones.  Signing is two-phase (``prepare``/``commit``): the host prepares a
  revision with a zeroed ``/Contents`` placeholder and hands the participant
  the exact ``/ByteRange`` bytes to sign; only the finished CMS travels back,
  and the host embeds it as a new incremental revision after checking the
  Streebog-256 ``messageDigest``.
* ``parallel`` (соподпись) — every participant independently signs the
  original document bytes; the host collects detached ``.sig`` files (CAdES),
  one per participant, to be stored next to the original file.

Transport is plain HTTP over the LAN (no TLS — the model is a trusted local
network).  Joining requires a session code; every participant gets a random
token for further calls.  Private keys never leave the participant's machine.
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import json
import secrets
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, crypto, pdf as pdfmod, service

DEFAULT_PORT = 8443
PORT_ATTEMPTS = 20
JOIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
MAX_BODY = 32 * 1024 * 1024
HTTP_TIMEOUT = 15

MODE_SEQUENTIAL = "sequential"
MODE_PARALLEL = "parallel"

STATUS_PENDING = "pending"
STATUS_SIGNING = "signing"
STATUS_SIGNED = "signed"
STATUS_DECLINED = "declined"
STATUS_ERROR = "error"

STATUS_LABELS = {
    STATUS_PENDING: "Ожидает подписи",
    STATUS_SIGNING: "Подписывает…",
    STATUS_SIGNED: "Подписан",
    STATUS_DECLINED: "Отказался от подписи",
    STATUS_ERROR: "Ошибка подписания",
}

STAMP_TITLE = "ДОКУМЕНТ ПОДПИСАН ЭЛЕКТРОННОЙ ПОДПИСЬЮ"


class NetworkError(RuntimeError):
    """A human readable network/session failure."""


@dataclass
class SessionConfig:
    mode: str = MODE_SEQUENTIAL
    strict_order: bool = False
    visible: bool = True
    page_index: int = -1  # -1 = last page
    reason: str = "Подписание документа"
    location: str = ""
    doc_name: str = "document.pdf"


@dataclass
class Participant:
    token: str
    pid: str
    name: str
    organization: str = ""
    position: str = ""
    cert_name: str = ""
    validity_text: str = ""
    serial: str = ""
    thumbprint: str = ""
    status: str = STATUS_PENDING
    detail: str = ""
    order: int = 0
    joined_at: str = ""
    signed_at: str = ""
    sig_b64: str = ""  # parallel mode only: detached CMS

    @property
    def cert_info(self) -> "RemoteCertificate":
        return RemoteCertificate(
            thumbprint=self.thumbprint,
            display_name=self.name,
            organization=self.organization,
            position=self.position,
            validity_text=self.validity_text,
            serial=self.serial,
        )

    def public(self) -> dict:
        return {
            "id": self.pid,
            "name": self.name,
            "organization": self.organization,
            "position": self.position,
            "cert_name": self.cert_name,
            "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status),
            "detail": self.detail,
            "order": self.order,
            "joined_at": self.joined_at,
            "signed_at": self.signed_at,
        }


@dataclass
class RemoteCertificate:
    """Duck-typed stand-in for crypto.CertificateInfo used by the appearance."""

    thumbprint: str = ""
    display_name: str = ""
    organization: str = ""
    position: str = ""
    validity_text: str = ""
    serial: str = ""


def _now() -> str:
    return dt.datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def _new_code(length: int = 6) -> str:
    return "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(length))


def lan_addresses() -> list[str]:
    """IPv4 addresses of this machine on the local network (best guess)."""
    found: set[str] = set()

    def _add(value: str | None) -> None:
        if value and not value.startswith("127.") and value.count(".") == 3:
            found.add(value)

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 80))  # RFC 5737 TEST-NET: nothing is sent
        _add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            _add(info[4][0])
    except OSError:
        pass
    return sorted(found)


def computer_name() -> str:
    return socket.gethostname()


def verify_cms_digest(cms: bytes, data: bytes) -> tuple[bool, str]:
    """Check that the CMS messageDigest matches the given bytes."""
    expected = crypto.cms_message_digest(cms)
    if expected is None:
        return True, "CMS без атрибута messageDigest — дайджест не проверялся"
    actual = crypto.streebog256(data)
    if actual is None:
        return True, "локальный ГОСТ-хеш недоступен — дайджест не проверялся"
    if expected == actual:
        return True, "ГОСТ-хеш совпадает с подписанным значением"
    return False, "подпись создана над другими данными (дайджест не совпадает)"


class SignSession:
    """In-memory state of one signing session.  Thread-safe."""

    def __init__(self, doc_data: bytes, config: SessionConfig):
        if config.mode not in (MODE_SEQUENTIAL, MODE_PARALLEL):
            raise ValueError(f"Неизвестный режим сессии: {config.mode}")
        self.config = config
        self._lock = threading.Lock()
        self._original = doc_data
        self._doc = doc_data
        self.version = 1
        self.code = _new_code()
        self.started_at = _now()
        self.finished = False
        self.finish_note = ""
        self._participants: list[Participant] = []
        # sequential mode: (token, prepared revision) reserved for one signer
        self._pending: tuple[str, object] | None = None

    # ------------------------------------------------------------------ state
    def _by_token(self, token: str) -> Participant | None:
        for item in self._participants:
            if secrets.compare_digest(item.token, token):
                return item
        return None

    def _by_pid(self, pid: str) -> Participant | None:
        for item in self._participants:
            if item.pid == pid:
                return item
        return None

    def participant_cert_info(self, pid: str) -> RemoteCertificate | None:
        with self._lock:
            participant = self._by_pid(pid)
            return participant.cert_info if participant else None

    def document_bytes(self) -> bytes:
        with self._lock:
            return self._doc if self.config.mode == MODE_SEQUENTIAL else self._original

    def document_for(self, token: str) -> tuple[bytes, int]:
        """Document bytes + version for an authenticated participant."""
        with self._lock:
            if self._by_token(token) is None:
                raise NetworkError("Участник не найден — сначала присоединитесь к сессии")
            data = self._doc if self.config.mode == MODE_SEQUENTIAL else self._original
            return data, self.version

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "app": "PDF Мультиподпись",
                "version": __version__,
                "code": self.code,
                "doc_name": self.config.doc_name,
                "mode": self.config.mode,
                "mode_label": (
                    "Последовательная подпись" if self.config.mode == MODE_SEQUENTIAL
                    else "Параллельная подпись (соподпись)"
                ),
                "strict_order": self.config.strict_order,
                "doc_version": self.version,
                "started_at": self.started_at,
                "finished": self.finished,
                "finish_note": self.finish_note,
                "participants": [item.public() for item in sorted(
                    self._participants, key=lambda p: p.order
                )],
            }

    # ----------------------------------------------------------- operations
    def join(self, info: dict) -> tuple[Participant, int]:
        name = str(info.get("name") or "").strip()
        if not name:
            raise NetworkError("Не указано имя участника")
        with self._lock:
            if self.finished:
                raise NetworkError("Сессия завершена")
            if len(self._participants) >= 50:
                raise NetworkError("В сессии уже максимум участников")
            thumb = str(info.get("thumbprint") or "").strip().upper()
            if thumb and any(
                p.thumbprint == thumb and p.status != STATUS_DECLINED
                for p in self._participants
            ):
                raise NetworkError("Сертификат уже участвует в сессии")
            participant = Participant(
                token=secrets.token_hex(12),
                pid=secrets.token_hex(4),
                name=name,
                organization=str(info.get("organization") or "").strip(),
                position=str(info.get("position") or "").strip(),
                cert_name=str(info.get("cert_name") or "").strip(),
                validity_text=str(info.get("validity_text") or "").strip(),
                serial=str(info.get("serial") or "").strip(),
                thumbprint=thumb,
                order=len(self._participants) + 1,
                joined_at=_now(),
            )
            self._participants.append(participant)
            return participant, self.version

    def set_status(self, token: str, status: str, detail: str = "") -> None:
        if status not in (STATUS_PENDING, STATUS_SIGNING, STATUS_ERROR):
            raise NetworkError(f"Статус «{status}» нельзя установить участником")
        with self._lock:
            participant = self._by_token(token)
            if participant is None:
                raise NetworkError("Участник не найден — возможно, сессию перезапустили")
            if participant.status in (STATUS_SIGNED, STATUS_DECLINED):
                raise NetworkError("Подпись уже завершена")
            participant.status = status
            participant.detail = detail.strip()

    def decline(self, token: str, reason: str = "") -> None:
        with self._lock:
            participant = self._by_token(token)
            if participant is None:
                raise NetworkError("Участник не найден")
            participant.status = STATUS_DECLINED
            participant.detail = reason.strip()
            participant.signed_at = _now()
            if self._pending is not None and self._pending[0] == token:
                self._pending = None

    def _check_turn(self, participant: Participant) -> str:
        """Return an error message when strict order blocks this participant."""
        if not self.config.strict_order or self.config.mode != MODE_SEQUENTIAL:
            return ""
        pending = [p for p in self._participants if p.status == STATUS_PENDING]
        if not pending:
            return "Все участники уже подписали"
        if pending[0] is not participant:
            return (
                f"Сейчас подписывает «{pending[0].name}». "
                "Строгая очередь: дождитесь своей очереди."
            )
        return ""

    # ------------------------------------------------- sequential: two-phase
    def prepare_signature(self, token: str) -> tuple[bytes, int]:
        """Reserve a revision; returns the exact bytes the client must sign."""
        with self._lock:
            participant = self._by_token(token)
            if participant is None:
                raise NetworkError("Участник не найден — сначала присоединитесь к сессии")
            if self.finished:
                raise NetworkError("Сессия завершена")
            if participant.status in (STATUS_SIGNED, STATUS_DECLINED):
                raise NetworkError("Вы уже завершили подписание этого документа")
            turn_error = self._check_turn(participant)
            if turn_error:
                raise NetworkError(turn_error)
            if self._pending is not None:
                owner = self._by_token(self._pending[0])
                owner_name = owner.name if owner else "другой участник"
                raise NetworkError(
                    f"Сейчас подписывает «{owner_name}». "
                    "Подождите несколько секунд и повторите попытку."
                )
            data = self._doc
            existing = len(pdfmod.list_signatures(data))
            sizes = service.page_sizes(data)
            page_index = self.config.page_index
            page_index = page_index if 0 <= page_index < len(sizes) else len(sizes) - 1
            page_width, page_height = sizes[page_index]
            rect = None
            if self.config.visible:
                rects = service.stacked_rects(existing + 1, page_width, page_height)
                rect = rects[existing] if existing < len(rects) else rects[-1]
            request = pdfmod.SignRequest(
                thumbprint=participant.thumbprint,
                reason=self.config.reason,
                location=self.config.location,
                signer_name=participant.name,
                page_index=page_index,
                visible=self.config.visible and rect is not None,
                rect=rect,
                certificate_info=participant.cert_info,
                stamp_title=STAMP_TITLE,
            )
            try:
                pending = pdfmod.prepare_signature(
                    data, request, field_name=f"Signature{existing + 1}"
                )
            except pdfmod.PdfSignError as exc:
                raise NetworkError(f"Не удалось подготовить документ: {exc}")
            self._pending = (token, pending)
            participant.status = STATUS_SIGNING
            participant.detail = ""
            return pending.signed_bytes, self.version

    def commit_signature(self, token: str, cms: bytes) -> str:
        """Verify the CMS against the prepared bytes and embed the revision."""
        if not cms:
            raise NetworkError("Пустая подпись")
        with self._lock:
            participant = self._by_token(token)
            if participant is None:
                raise NetworkError("Участник не найден — возможно, сессию перезапустили")
            if self._pending is None or self._pending[0] != token:
                raise NetworkError(
                    "Подготовленная подпись не найдена — начните подписание заново"
                )
            _, pending = self._pending
            digest_ok, digest_note = verify_cms_digest(cms, pending.signed_bytes)
            if not digest_ok:
                self._pending = None
                participant.status = STATUS_ERROR
                participant.detail = digest_note
                participant.signed_at = _now()
                raise NetworkError(digest_note)
            try:
                self._doc = pending.commit(cms)
            except pdfmod.PdfSignError as exc:
                self._pending = None
                participant.status = STATUS_ERROR
                participant.detail = f"не удалось встроить подпись: {exc}"
                participant.signed_at = _now()
                raise NetworkError(participant.detail)
            self._pending = None
            self.version += 1
            participant.status = STATUS_SIGNED
            participant.detail = digest_note
            participant.signed_at = _now()
            return digest_note

    def cancel_pending(self, token: str) -> None:
        with self._lock:
            if self._pending is not None and self._pending[0] == token:
                self._pending = None
                participant = self._by_token(token)
                if participant is not None and participant.status == STATUS_SIGNING:
                    participant.status = STATUS_PENDING
                    participant.detail = ""

    # ------------------------------------------------------ parallel: submit
    def submit_signature(self, token: str, doc_version: int, cms: bytes) -> str:
        """Accept a detached signature for the parallel (соподпись) scheme."""
        if not cms:
            raise NetworkError("Пустая подпись")
        with self._lock:
            participant = self._by_token(token)
            if participant is None:
                raise NetworkError("Участник не найден — возможно, сессию перезапустили")
            if participant.status in (STATUS_SIGNED, STATUS_DECLINED):
                raise NetworkError("Вы уже завершили подписание этого документа")
            if self.finished:
                raise NetworkError("Сессия завершена")
            if self.config.mode != MODE_PARALLEL:
                raise NetworkError("Этот режим сессии не принимает готовые подписи")
            if int(doc_version) != self.version:
                raise NetworkError(
                    f"Документ обновился (версия {self.version}). "
                    "Скачайте актуальную версию и подпишите её."
                )
            digest_ok, digest_note = verify_cms_digest(cms, self._original)
            if not digest_ok:
                participant.status = STATUS_ERROR
                participant.detail = digest_note
                participant.signed_at = _now()
                raise NetworkError(digest_note)
            participant.sig_b64 = base64.b64encode(cms).decode("ascii")
            participant.status = STATUS_SIGNED
            participant.detail = digest_note
            participant.signed_at = _now()
            return digest_note

    def finalize(self, note: str = "") -> None:
        with self._lock:
            self.finished = True
            self.finish_note = note

    def results(self) -> tuple[bytes, list[tuple[str, bytes]], str]:
        """(final_document, detached_signature_files, note) for saving."""
        with self._lock:
            signed = [
                p for p in self._participants if p.status == STATUS_SIGNED and p.sig_b64
            ]
            if self.config.mode == MODE_PARALLEL:
                stem = self.config.doc_name.rsplit(".", 1)[0] or "document"
                files = [
                    (f"{stem}-{p.order}.sig", base64.b64decode(p.sig_b64))
                    for p in sorted(signed, key=lambda p: p.order)
                ]
                return self._original, files, f"Соподпись: подписей собрано {len(files)}"
            signed_count = len(
                [p for p in self._participants if p.status == STATUS_SIGNED]
            )
            return self._doc, [], f"Подписей в документе: {signed_count}"


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

_STATUS_HTML = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>{title}</title>
<style>
 body {{ font-family: 'Segoe UI', sans-serif; margin: 40px auto; max-width: 720px; color: #0F172A; }}
 h1 {{ font-size: 22px; }} .muted {{ color: #475569; font-size: 13px; }}
 table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
 td, th {{ border: 1px solid #C3D0E4; padding: 8px 10px; text-align: left; font-size: 14px; }}
 .st-pending {{ color: #B45309; }} .st-signing {{ color: #1D4ED8; font-weight: 600; }}
 .st-signed {{ color: #146C2E; font-weight: 600; }} .st-declined, .st-error {{ color: #B3261E; }}
 code {{ background: #EFF4FC; padding: 2px 6px; border-radius: 6px; }}
 .hint {{ background: #EFF4FC; border-radius: 12px; padding: 14px 16px; margin-top: 20px; }}
</style></head><body>
<h1>PDF Мультиподпись — сетевая сессия</h1>
<p class="muted">Документ: <b>{doc}</b> · {mode} · версия {version} · код подключения <code>{code}</code></p>
{finished}
<table><tr><th>№</th><th>Участник</th><th>Организация</th><th>Статус</th><th>Время</th></tr>
{rows}
</table>
<div class="hint"><b>Как подписать:</b> на компьютере участника откройте программу
«PDF Мультиподпись» → «Сетевая подпись» → «Присоединиться к сессии», укажите адрес
<code>{host}</code> и код <code>{code}</code>, выберите сертификат и нажмите «Подписать».
Закрытый ключ не покидает компьютер участника.</div>
</body></html>"""


def render_status_page(snapshot: dict, host_hint: str) -> str:
    rows = []
    for item in snapshot["participants"]:
        status_class = f"st-{item['status']}"
        rows.append(
            "<tr><td>{order}</td><td>{name}</td><td>{org}</td>"
            '<td class="{cls}">{label}</td><td>{time}</td></tr>'.format(
                order=item["order"],
                name=html.escape(item["name"]),
                org=html.escape(item["organization"] or "—"),
                cls=status_class,
                label=html.escape(item["status_label"]),
                time=html.escape(item["signed_at"] or item["joined_at"] or ""),
            )
        )
    if not rows:
        rows.append('<tr><td colspan="5" class="muted">Пока никто не присоединился</td></tr>')
    finished = ""
    if snapshot["finished"]:
        finished = f'<p><b>Сессия завершена.</b> {html.escape(snapshot["finish_note"])}</p>'
    return _STATUS_HTML.format(
        title="PDF Мультиподпись — сессия " + snapshot["code"],
        doc=html.escape(snapshot["doc_name"]),
        mode=html.escape(snapshot["mode_label"]),
        version=snapshot["doc_version"],
        code=snapshot["code"],
        host=html.escape(host_hint),
        finished=finished,
        rows="".join(rows),
    )


def _make_handler(session: SignSession) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"PDFMultisign/{__version__}"

        # -- helpers ------------------------------------------------------
        def log_message(self, fmt: str, *args) -> None:  # keep the console clean
            return

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, message: str, status: int = 400) -> None:
            self._json({"ok": False, "error": message}, status)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            if length > MAX_BODY:
                raise NetworkError("Слишком большой запрос")
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise NetworkError(f"Неверный формат запроса: {exc}") from exc
            if not isinstance(data, dict):
                raise NetworkError("Неверный формат запроса")
            return data

        def _check_code(self, params) -> None:
            code = (params.get("code") or [""])[0].upper()
            if not secrets.compare_digest(code, session.code):
                raise NetworkError("Неверный код сессии")

        def _token(self) -> str:
            token = (self.headers.get("X-Token") or "").strip()
            if not token:
                raise NetworkError("Отсутствует токен участника")
            return token

        # -- routing ------------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802
            try:
                path, _, query = self.path.partition("?")
                params = urllib.parse.parse_qs(query)
                if path == "/":
                    host_hint = self.headers.get("Host") or "адрес этого компьютера"
                    page = render_status_page(session.snapshot(), host_hint).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(page)))
                    self.end_headers()
                    self.wfile.write(page)
                    return
                if path == "/api/info":
                    self._check_code(params)
                    snap = session.snapshot()
                    self._json({
                        "ok": True,
                        "version": snap["version"],
                        "doc_name": snap["doc_name"],
                        "mode": snap["mode"],
                        "mode_label": snap["mode_label"],
                        "strict_order": snap["strict_order"],
                        "doc_version": snap["doc_version"],
                        "started_at": snap["started_at"],
                        "finished": snap["finished"],
                    })
                    return
                if path == "/api/state":
                    self._check_code(params)
                    self._json({"ok": True, **session.snapshot()})
                    return
                if path == "/api/document":
                    self._check_code(params)
                    token = self._token()
                    data, version = session.document_for(token)
                    digest = crypto.streebog256(data)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/pdf")
                    self.send_header("Content-Disposition",
                                     f'attachment; filename="{session.config.doc_name}"')
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("X-Doc-Version", str(version))
                    if digest:
                        self.send_header("X-Doc-Hash", digest.hex())
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self._error("Не найдено", 404)
            except NetworkError as exc:
                self._error(str(exc), 403)
            except BrokenPipeError:
                pass
            except Exception as exc:  # pragma: no cover - defensive
                self._error(f"Внутренняя ошибка сервера: {exc}", 500)

        def do_POST(self) -> None:  # noqa: N802
            try:
                path = self.path.partition("?")[0]
                if path == "/api/join":
                    info = self._body()
                    self._check_code(urllib.parse.parse_qs(self.path.partition("?")[2]))
                    participant, version = session.join(info)
                    self._json({
                        "ok": True,
                        "token": participant.token,
                        "participant_id": participant.pid,
                        "order": participant.order,
                        "doc_version": version,
                    })
                    return
                body = self._body()
                token = self._token()
                if path == "/api/status":
                    status = str(body.get("status") or "")
                    detail = str(body.get("detail") or "")
                    session.set_status(token, status, detail)
                    self._json({"ok": True})
                    return
                if path == "/api/decline":
                    session.decline(token, str(body.get("reason") or ""))
                    self._json({"ok": True})
                    return
                if path == "/api/prepare":
                    signed_bytes, version = session.prepare_signature(token)
                    self._json({
                        "ok": True,
                        "signed_bytes": base64.b64encode(signed_bytes).decode("ascii"),
                        "doc_version": version,
                    })
                    return
                if path == "/api/commit":
                    try:
                        cms = base64.b64decode(str(body.get("cms_b64") or ""), validate=True)
                    except Exception:
                        self._error("Подпись не в формате base64", 400)
                        return
                    message = session.commit_signature(token, cms)
                    self._json({"ok": True, "message": message})
                    return
                if path == "/api/cancel":
                    session.cancel_pending(token)
                    self._json({"ok": True})
                    return
                if path == "/api/sign":
                    try:
                        cms = base64.b64decode(str(body.get("cms_b64") or ""), validate=True)
                    except Exception:
                        self._error("Подпись не в формате base64", 400)
                        return
                    message = session.submit_signature(
                        token, int(body.get("doc_version") or 0), cms
                    )
                    self._json({"ok": True, "message": message})
                    return
                self._error("Не найдено", 404)
            except NetworkError as exc:
                self._error(str(exc), 403)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:  # pragma: no cover - defensive
                self._error(f"Внутренняя ошибка сервера: {exc}", 500)

    return Handler


class SessionServer:
    """Embedded HTTP server hosting one SignSession."""

    def __init__(self, session: SignSession, port: int = DEFAULT_PORT, host: str = "0.0.0.0"):
        self.session = session
        last_error: OSError | None = None
        candidates = [0] if port == 0 else [port] + list(
            range(DEFAULT_PORT, DEFAULT_PORT + PORT_ATTEMPTS)
        )
        for candidate in candidates:
            try:
                self._server = ThreadingHTTPServer(
                    (host, candidate), _make_handler(session)
                )
                break
            except OSError as exc:
                last_error = exc
        else:
            raise NetworkError(
                f"Не удалось открыть порт {port or DEFAULT_PORT}…{DEFAULT_PORT + PORT_ATTEMPTS}: "
                f"{last_error}. Проверьте, не занят ли порт другой программой "
                "и разрешён ли он в брандмауэре."
            )
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def addresses(self) -> list[str]:
        """Human readable http://host:port links for the LAN."""
        links = [f"http://{address}:{self.port}" for address in lan_addresses()]
        return links or [f"http://127.0.0.1:{self.port}"]

    def start(self) -> None:
        self._thread.start()

    def shutdown(self) -> None:
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class SessionClient:
    """Participant side: talks to the host's SessionServer."""

    def __init__(self, base_url: str, code: str):
        base_url = base_url.strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            base_url = "http://" + base_url
        self.base_url = base_url
        self.code = code.strip().upper()
        if not self.code:
            raise NetworkError("Введите код сессии")
        self.token = ""
        self.participant_id = ""

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request(self, path: str, payload: dict | None = None, headers: dict | None = None):
        data = None
        request_headers = dict(headers or {})
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(self._url(path), data=data, headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
                raw = response.read()
                status = response.status
                response_headers = response.headers
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error", "")
            except Exception:
                detail = ""
            raise NetworkError(detail or f"Сервер ответил ошибкой {exc.code}") from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise NetworkError(
                f"Не удалось подключиться к {self.base_url}: {reason}. "
                "Проверьте адрес, порт и что оба компьютера в одной сети."
            ) from exc
        return raw, status, response_headers

    def info(self) -> dict:
        raw, _, _ = self._request(f"/api/info?code={self.code}")
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise NetworkError("Сервер вернул неверный ответ") from exc

    def join(self, cert) -> None:
        raw, _, _ = self._request("/api/join?code=" + self.code, {
            "name": getattr(cert, "display_name", "") or "Участник",
            "organization": getattr(cert, "organization", "") or "",
            "position": getattr(cert, "position", "") or "",
            "cert_name": getattr(cert, "common_name", "") or "",
            "validity_text": getattr(cert, "validity_text", "") or "",
            "serial": getattr(cert, "serial", "") or "",
            "thumbprint": getattr(cert, "thumbprint", "") or "",
        })
        try:
            answer = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise NetworkError("Сервер вернул неверный ответ") from exc
        self.token = str(answer.get("token") or "")
        self.participant_id = str(answer.get("participant_id") or "")
        if not self.token:
            raise NetworkError("Сервер не выдал токен участника")

    def state(self) -> dict:
        raw, _, _ = self._request(f"/api/state?code={self.code}")
        return json.loads(raw.decode("utf-8"))

    def document(self) -> tuple[bytes, int]:
        raw, _, headers = self._request(
            f"/api/document?code={self.code}", headers={"X-Token": self.token}
        )
        return raw, int(headers.get("X-Doc-Version") or 0)

    def prepare(self) -> bytes:
        """Sequential mode: reserve a revision and get the exact bytes to sign."""
        raw, _, _ = self._request(
            "/api/prepare", {}, headers={"X-Token": self.token}
        )
        try:
            answer = json.loads(raw.decode("utf-8"))
            return base64.b64decode(str(answer.get("signed_bytes") or ""), validate=True)
        except Exception as exc:
            raise NetworkError(f"Сервер вернул неверный ответ: {exc}") from exc

    def commit(self, cms: bytes) -> None:
        self._request(
            "/api/commit",
            {"cms_b64": base64.b64encode(cms).decode("ascii")},
            headers={"X-Token": self.token},
        )

    def cancel_pending(self) -> None:
        try:
            self._request("/api/cancel", {}, headers={"X-Token": self.token})
        except NetworkError:
            pass

    def set_status(self, status: str, detail: str = "") -> None:
        self._request("/api/status", {"status": status, "detail": detail},
                      headers={"X-Token": self.token})

    def decline(self, reason: str = "") -> None:
        self._request("/api/decline", {"reason": reason}, headers={"X-Token": self.token})

    def submit(self, doc_version: int, cms: bytes) -> None:
        """Parallel mode: hand in a detached CMS for the .sig collection."""
        self._request(
            "/api/sign",
            {"doc_version": doc_version, "cms_b64": base64.b64encode(cms).decode("ascii")},
            headers={"X-Token": self.token},
        )
