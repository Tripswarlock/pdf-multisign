"""Dialogs for LAN signing sessions: host side and participant side."""
from __future__ import annotations

import pathlib

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import crypto, netsession, service, theme
from .window import CertificateRow

STATUS_TEXT = {
    netsession.STATUS_WAITING: "оформляет подпись…",
    netsession.STATUS_SIGNED: "подписал(а)",
    netsession.STATUS_ERROR: "ошибка",
}

STATUS_COLOR = {
    netsession.STATUS_WAITING: theme.ON_SURFACE_VARIANT,
    netsession.STATUS_SIGNED: theme.SUCCESS,
    netsession.STATUS_ERROR: theme.ERROR,
}


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 14, 18, 14)
    layout.setSpacing(9)
    heading = QLabel(title)
    heading.setObjectName("SectionTitle")
    layout.addWidget(heading)
    return frame, layout


def _copyable(text: str) -> QLineEdit:
    edit = QLineEdit(text)
    edit.setReadOnly(True)
    return edit


class PdfPreview(QWidget):
    """Compact page preview with navigation, shared by the join dialog."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._data: bytes | None = None
        self._pages = 0
        self._index = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.label = QLabel("Предпросмотр появится после загрузки PDF")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setMinimumHeight(260)
        self.label.setStyleSheet(f"color: {theme.ON_SURFACE_VARIANT};")
        layout.addWidget(self.label, 1)
        nav = QHBoxLayout()
        self.prev_button = QPushButton("‹")
        self.prev_button.setObjectName("Text")
        self.prev_button.setFixedWidth(38)
        self.prev_button.clicked.connect(lambda: self.show_page(self._index - 1))
        self.next_button = QPushButton("›")
        self.next_button.setObjectName("Text")
        self.next_button.setFixedWidth(38)
        self.next_button.clicked.connect(lambda: self.show_page(self._index + 1))
        self.counter = QLabel("—")
        self.counter.setObjectName("Caption")
        self.counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.prev_button)
        nav.addWidget(self.counter, 1)
        nav.addWidget(self.next_button)
        layout.addLayout(nav)

    def set_document(self, data: bytes) -> bool:
        try:
            self._pages = service.page_count(data)
        except Exception as exc:
            self.label.setText(f"Не удалось прочитать PDF: {exc}")
            self.label.setPixmap(QPixmap())
            return False
        self._data = data
        self.show_page(self._pages - 1)
        return True

    def show_page(self, index: int) -> None:
        if self._data is None:
            return
        index = max(0, min(index, self._pages - 1))
        self._index = index
        self.counter.setText(f"{index + 1} / {self._pages}")
        self.prev_button.setEnabled(index > 0)
        self.next_button.setEnabled(index < self._pages - 1)
        try:
            import pymupdf

            doc = pymupdf.open(stream=self._data, filetype="pdf")
            pix = doc[index].get_pixmap(dpi=100)
            image = QImage(pix.samples, pix.width, pix.height, pix.stride,
                           QImage.Format.Format_RGB888).copy()
            doc.close()
            self.label.setPixmap(
                QPixmap.fromImage(image).scaled(
                    self.label.size(), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        except Exception as exc:
            self.label.setText(f"Не удалось отрисовать страницу: {exc}")


class ParticipantRow(QFrame):
    def __init__(self, info: dict, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CertRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)
        self._name = QLabel(info.get("name") or "Участник")
        self._name.setStyleSheet("font-weight: 600; background: transparent;")
        self._status = QLabel()
        self._status.setStyleSheet("background: transparent;")
        layout.addWidget(self._name, 1)
        layout.addWidget(self._status, 0, Qt.AlignmentFlag.AlignRight)
        self.update_info(info)

    def update_info(self, info: dict) -> None:
        status = info.get("status") or netsession.STATUS_WAITING
        parts = [STATUS_TEXT.get(status, status)]
        if status == netsession.STATUS_SIGNED:
            parts = [f"✓ подписал(а) · {info.get('at', '')}"]
            if info.get("detail"):
                parts.append(str(info["detail"]))
        elif status == netsession.STATUS_ERROR and info.get("detail"):
            parts.append(str(info["detail"]))
        self._status.setText(" · ".join(parts))
        self._status.setStyleSheet(
            f"color: {STATUS_COLOR.get(status, theme.ON_SURFACE_VARIANT)}; background: transparent;"
        )


class _ParticipantsView(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._rows: dict[str, ParticipantRow] = {}
        self._placeholder = QLabel("Участников пока нет")
        self._placeholder.setObjectName("Caption")
        layout.addWidget(self._placeholder)
        layout.addStretch(1)
        self._layout = layout

    def update_participants(self, participants: list[dict]) -> None:
        self._placeholder.setVisible(not participants)
        seen = set()
        for info in participants:
            pid = str(info.get("pid") or "")
            if not pid:
                continue
            seen.add(pid)
            row = self._rows.get(pid)
            if row is None:
                row = ParticipantRow(info)
                self._rows[pid] = row
                self._layout.insertWidget(self._layout.count() - 1, row)
            else:
                row.update_info(info)
        for pid in list(self._rows):
            if pid not in seen:
                self._rows.pop(pid).deleteLater()


# ---------------------------------------------------------------------------
# Host dialog
# ---------------------------------------------------------------------------

class HostSessionDialog(QDialog):
    """Host a document: show the code, watch participants, save the result."""

    def __init__(self, pdf_bytes: bytes, file_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Сетевая сессия подписания")
        self.resize(640, 640)
        self.setStyleSheet(theme.stylesheet())
        self._source_bytes = pdf_bytes
        self._file_name = file_name
        self.result_bytes: bytes | None = None
        self._server: netsession.SessionServer | None = None
        self._state: netsession.SessionState | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        params_card, params_layout = _card("Параметры сессии")
        code_row = QHBoxLayout()
        code_caption = QLabel("Код сессии")
        code_caption.setObjectName("Caption")
        self.code_edit = _copyable(netsession.make_code())
        self.code_edit.setStyleSheet(
            f"font-size: 20px; font-weight: 700; letter-spacing: 3px; color: {theme.PRIMARY};"
        )
        code_row.addWidget(code_caption)
        code_row.addWidget(self.code_edit, 1)
        params_layout.addLayout(code_row)

        port_row = QHBoxLayout()
        port_caption = QLabel("Порт")
        port_caption.setObjectName("Caption")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(netsession.DEFAULT_PORT)
        port_row.addWidget(port_caption)
        port_row.addWidget(self.port_spin)
        port_row.addStretch(1)
        self.stamp_check = QCheckBox("Видимый блок подписи у участников")
        port_row.addWidget(self.stamp_check)
        params_layout.addLayout(port_row)
        root.addWidget(params_card)

        addr_card, addr_layout = _card("Адрес для участников")
        self._addr_box = QVBoxLayout()
        self._addr_box.setSpacing(4)
        addr_layout.addLayout(self._addr_box)
        self._fill_addresses(netsession.DEFAULT_PORT)
        root.addWidget(addr_card)

        part_card, part_layout = _card("Участники и статусы")
        self._counter = QLabel("Подписей в документе: 0")
        self._counter.setObjectName("Caption")
        part_layout.addWidget(self._counter)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        self._participants = _ParticipantsView()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addWidget(self._participants)
        scroll.setWidget(holder)
        part_layout.addWidget(scroll, 1)
        root.addWidget(part_card, 1)

        buttons = QHBoxLayout()
        self.start_button = QPushButton("Начать сессию")
        self.start_button.setObjectName("Filled")
        self.start_button.clicked.connect(self._start)
        self.save_button = QPushButton("Сохранить PDF…")
        self.save_button.setObjectName("Tonal")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._save)
        self.close_button = QPushButton("Завершить")
        self.close_button.setObjectName("Text")
        self.close_button.clicked.connect(self._finish)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.save_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        root.addLayout(buttons)

        self._poll = QTimer(self)
        self._poll.setInterval(1500)
        self._poll.timeout.connect(self._refresh)

    def _fill_addresses(self, port: int) -> None:
        while self._addr_box.count():
            item = self._addr_box.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        addresses = netsession.local_addresses()
        if not addresses:
            label = QLabel("Не удалось определить адрес этой машины")
            label.setObjectName("Caption")
            self._addr_box.addWidget(label)
            return
        for address in addresses:
            self._addr_box.addWidget(_copyable(f"http://{address}:{port}"))

    def _start(self) -> None:
        if self._server is not None:
            return
        state = netsession.SessionState(
            self._source_bytes,
            file_name=self._file_name,
            code=self.code_edit.text().strip(),
            visible=self.stamp_check.isChecked(),
        )
        try:
            server = netsession.SessionServer(state, self.port_spin.value())
            server.start()
        except OSError as exc:
            QMessageBox.critical(self, "Сетевая сессия", f"Не удалось запустить сервер: {exc}")
            return
        self._state = state
        self._server = server
        self.result_bytes = self._source_bytes
        self.start_button.setEnabled(False)
        self.port_spin.setEnabled(False)
        self.code_edit.setEnabled(False)
        self._fill_addresses(server.port)
        self._poll.start()
        self._refresh()

    def _refresh(self) -> None:
        if self._state is None:
            return
        info = self._state.info()
        self._participants.update_participants(info.get("participants") or [])
        self._counter.setText(
            f"Подписей в документе: {info.get('signatures', 0)} · участников: "
            f"{len(info.get('participants') or [])}"
        )
        if info.get("signatures", 0) > 0:
            self.save_button.setEnabled(True)
            self.result_bytes = self._state.pdf_bytes

    def _save(self) -> None:
        if self._state is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить PDF", "signed-network.pdf", "PDF (*.pdf)")
        if not path:
            return
        try:
            pathlib.Path(path).write_bytes(self._state.pdf_bytes)
        except OSError as exc:
            QMessageBox.critical(self, "Сохранение", f"Не удалось сохранить файл: {exc}")
            return
        QMessageBox.information(self, "Готово", f"Файл сохранён:\n{path}")

    def _finish(self) -> None:
        self._stop()
        self.accept()

    def _stop(self) -> None:
        if self._server is not None:
            self._server.stop()
            self._server = None
        self._poll.stop()
        if self._state is not None:
            self.result_bytes = self._state.pdf_bytes

    def reject(self) -> None:  # closing the window stops the session too
        self._stop()
        super().reject()


# ---------------------------------------------------------------------------
# Join dialog
# ---------------------------------------------------------------------------

class _SignWorker(QThread):
    ok = Signal(str, str)
    busy = Signal(str)
    failed = Signal(str)

    def __init__(self, base: str, code: str, pid: str, cert, parent=None):
        super().__init__()
        self._base = base
        self._code = code
        self._pid = pid
        self._cert = cert

    def run(self) -> None:
        try:
            signed_bytes = netsession.prepare_signature(
                self._base, self._code, self._pid, netsession.cert_to_dict(self._cert)
            )
            cms = crypto.sign_detached(signed_bytes, self._cert.thumbprint)
            field, detail = netsession.commit_signature(self._base, self._code, self._pid, cms)
        except netsession.SessionError as exc:
            if exc.code == "BUSY":
                self.busy.emit(str(exc))
            elif exc.code in ("NO_PENDING", "DIGEST_MISMATCH"):
                # the reservation is gone; offer a clean retry
                try:
                    netsession.cancel_signature(self._base, self._code, self._pid)
                except Exception:
                    pass
                self.failed.emit(str(exc))
            else:
                try:
                    netsession.cancel_signature(self._base, self._code, self._pid)
                except Exception:
                    pass
                self.failed.emit(str(exc))
        except crypto.CryptoError as exc:
            try:
                netsession.cancel_signature(self._base, self._code, self._pid)
            except Exception:
                pass
            self.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self.failed.emit(f"Непредвиденная ошибка: {exc}")
        else:
            self.ok.emit(field, detail)


class _ConnectWorker(QThread):
    ok = Signal(str, bytes, dict)   # pid, document, info
    failed = Signal(str)

    def __init__(self, base: str, code: str, name: str):
        super().__init__()
        self._base = base
        self._code = code
        self._name = name

    def run(self) -> None:
        try:
            pid, info = netsession.join_session(self._base, self._code, self._name)
            document = netsession.fetch_document(self._base, self._code)
        except netsession.SessionError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            self.failed.emit(f"Непредвиденная ошибка: {exc}")
            return
        self.ok.emit(pid, document, info)


class JoinSessionDialog(QDialog):
    """Join a session hosted on another machine and sign with a local key."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Присоединиться к сессии")
        self.resize(960, 720)
        self.setStyleSheet(theme.stylesheet())
        self._base = ""
        self._code = ""
        self._pid = ""
        self._document: bytes | None = None
        self._signatures_seen = 0
        self._rows: list[CertificateRow] = []
        self._certificates: list[crypto.CertificateInfo] = []
        self._connect_worker: _ConnectWorker | None = None
        self._sign_worker: _SignWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        conn_card, conn_layout = _card("Подключение")
        addr_row = QHBoxLayout()
        addr_caption = QLabel("Адрес")
        addr_caption.setObjectName("Caption")
        self.address_edit = QLineEdit()
        self.address_edit.setPlaceholderText("http://192.168.1.10:8471")
        addr_row.addWidget(addr_caption)
        addr_row.addWidget(self.address_edit, 1)
        code_caption = QLabel("Код")
        code_caption.setObjectName("Caption")
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("6 цифр")
        self.code_edit.setMaxLength(6)
        self.code_edit.setFixedWidth(90)
        addr_row.addWidget(code_caption)
        addr_row.addWidget(self.code_edit)
        self.connect_button = QPushButton("Подключиться")
        self.connect_button.setObjectName("Filled")
        self.connect_button.clicked.connect(self._connect)
        addr_row.addWidget(self.connect_button)
        conn_layout.addLayout(addr_row)
        self.session_label = QLabel("Введите адрес и код, которые показывает ведущий сессии")
        self.session_label.setObjectName("Caption")
        self.session_label.setWordWrap(True)
        conn_layout.addWidget(self.session_label)
        root.addWidget(conn_card)

        body = QHBoxLayout()
        body.setSpacing(14)
        root.addLayout(body, 1)

        doc_card, doc_layout = _card("Документ")
        self.preview = PdfPreview()
        doc_layout.addWidget(self.preview, 1)
        self.update_button = QPushButton("Обновить документ")
        self.update_button.setObjectName("Text")
        self.update_button.setVisible(False)
        self.update_button.clicked.connect(self._refresh_document)
        doc_layout.addWidget(self.update_button, 0, Qt.AlignmentFlag.AlignLeft)
        body.addWidget(doc_card, 3)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)

        cert_card, cert_layout = _card("Ваш сертификат")
        cert_scroll = QScrollArea()
        cert_scroll.setWidgetResizable(True)
        cert_scroll.setFrameShape(QFrame.Shape.NoFrame)
        cert_scroll.setMinimumHeight(140)
        holder = QWidget()
        self._cert_box = QVBoxLayout(holder)
        self._cert_box.setContentsMargins(0, 0, 0, 0)
        self._cert_box.setSpacing(6)
        self._cert_box.addStretch(1)
        cert_scroll.setWidget(holder)
        cert_layout.addWidget(cert_scroll, 1)
        right_layout.addWidget(cert_card, 1)

        self._status_view = _ParticipantsView()
        stat_card, stat_layout = _card("Статусы участников")
        stat_layout.addWidget(self._status_view)
        right_layout.addWidget(stat_card)

        self.sign_button = QPushButton("Подписать")
        self.sign_button.setObjectName("Filled")
        self.sign_button.setEnabled(False)
        self.sign_button.clicked.connect(self._sign)
        right_layout.addWidget(self.sign_button)
        body.addWidget(right, 2)

        self._poll = QTimer(self)
        self._poll.setInterval(3000)
        self._poll.timeout.connect(self._poll_status)

        self._load_certificates()

    # ------------------------------------------------------------- connection
    def _connect(self) -> None:
        base = self.address_edit.text().strip().rstrip("/")
        if base and not base.startswith("http://") and not base.startswith("https://"):
            base = "http://" + base
        code = self.code_edit.text().strip()
        if not base or len(code) != 6:
            QMessageBox.warning(self, "Подключение", "Введите адрес сервера и 6-значный код сессии")
            return
        self._set_busy(True, "Подключение…")
        self._base = base
        self._code = code
        self._connect_worker = _ConnectWorker(base, code, netsession.computer_name())
        self._connect_worker.ok.connect(self._on_connected)
        self._connect_worker.failed.connect(self._on_connect_failed)
        self._connect_worker.start()

    def _on_connected(self, pid: str, document: bytes, info: dict) -> None:
        self._set_busy(False, "")
        self._pid = pid
        if not document.startswith(b"%PDF-"):
            QMessageBox.warning(self, "Документ", "Ведущий прислал не PDF")
            return
        self._document = document
        self.preview.set_document(document)
        self._signatures_seen = int(info.get("signatures") or 0)
        self._update_session_label(info)
        self._status_view.update_participants(info.get("participants") or [])
        self.update_button.setVisible(True)
        self.connect_button.setText("Переподключиться")
        self._poll.start()
        self._refresh_sign_state()

    def _on_connect_failed(self, message: str) -> None:
        self._set_busy(False, "")
        QMessageBox.critical(self, "Подключение", message)

    def _set_busy(self, busy: bool, text: str) -> None:
        self.connect_button.setEnabled(not busy)
        if busy:
            self.session_label.setText(text)
        self.sign_button.setEnabled(
            not busy and self._document is not None and bool(self._selected())
        )

    # ------------------------------------------------------------- documents
    def _refresh_document(self) -> None:
        if not self._base or not self._code:
            return
        try:
            document = netsession.fetch_document(self._base, self._code)
            info = netsession.session_info(self._base, self._code)
        except netsession.SessionError as exc:
            QMessageBox.critical(self, "Документ", str(exc))
            return
        self._document = document
        self.preview.set_document(document)
        self._signatures_seen = int(info.get("signatures") or 0)
        self._update_session_label(info)
        self._refresh_sign_state()

    def _update_session_label(self, info: dict) -> None:
        self.session_label.setText(
            f"{info.get('file', 'документ')} · {info.get('page_count', 0)} стр. · "
            f"подписей: {info.get('signatures', 0)} · "
            f"{'видимый блок' if info.get('visible_stamp') else 'невидимая подпись'}"
        )

    # ------------------------------------------------------------- statuses
    def _poll_status(self) -> None:
        if not self._base or not self._code:
            return
        try:
            info = netsession.session_info(self._base, self._code)
        except netsession.SessionError:
            return
        self._status_view.update_participants(info.get("participants") or [])
        signatures = int(info.get("signatures") or 0)
        if signatures != self._signatures_seen:
            self._signatures_seen = signatures
            try:
                document = netsession.fetch_document(self._base, self._code)
            except netsession.SessionError:
                return
            self._document = document
            self.preview.set_document(document)
        self._update_session_label(info)
        self._refresh_sign_state()

    # ------------------------------------------------------------- signing
    def _load_certificates(self) -> None:
        available, detail = crypto.crypto_status()
        if not available:
            label = QLabel(detail)
            label.setObjectName("Error")
            label.setWordWrap(True)
            self._cert_box.insertWidget(0, label)
            self._rows.append(label)  # type: ignore[arg-type]
            return
        try:
            self._certificates = crypto.list_certificates()
        except crypto.CryptoError as exc:
            label = QLabel(str(exc))
            label.setObjectName("Error")
            label.setWordWrap(True)
            self._cert_box.insertWidget(0, label)
            return
        for info in self._certificates:
            row = CertificateRow(info)
            row._selector = lambda _row: self._on_row_checked(_row)
            self._cert_box.insertWidget(self._cert_box.count() - 1, row)
            self._rows.append(row)
        self._refresh_sign_state()

    def _on_row_checked(self, row: CertificateRow) -> None:
        if not row.is_checked:
            self._refresh_sign_state()
            return
        # single selection: one signature per participant
        for other in self._rows:
            if isinstance(other, CertificateRow) and other is not row and other.is_checked:
                other.set_checked(False)
        self._refresh_sign_state()

    def _selected(self) -> list[crypto.CertificateInfo]:
        return [
            row.info for row in self._rows
            if isinstance(row, CertificateRow) and row.is_checked
            and row.info.sign_capable and row.info.is_valid_now
        ]

    def _refresh_sign_state(self) -> None:
        self.sign_button.setEnabled(self._document is not None and bool(self._selected()))

    def _sign(self) -> None:
        selected = self._selected()
        if not selected or self._document is None:
            return
        self.sign_button.setEnabled(False)
        self._sign_worker = _SignWorker(self._base, self._code, self._pid, selected[0])
        self._sign_worker.ok.connect(self._on_signed)
        self._sign_worker.busy.connect(self._on_busy)
        self._sign_worker.failed.connect(self._on_failed)
        self._sign_worker.start()

    def _on_signed(self, field: str, detail: str) -> None:
        self.session_label.setText(f"{self.session_label.text()} · ✓ {detail}")
        try:
            info = netsession.session_info(self._base, self._code)
            self._status_view.update_participants(info.get("participants") or [])
        except netsession.SessionError:
            pass
        QMessageBox.information(self, "Подпись", "Ваша подпись принята ведущим сессии.")

    def _on_busy(self, message: str) -> None:
        self._refresh_sign_state()
        QMessageBox.information(
            self,
            "Очередь подписания",
            message + "\n\nНажмите «Подписать» ещё раз через несколько секунд.",
        )

    def _on_failed(self, message: str) -> None:
        self._refresh_sign_state()
        QMessageBox.critical(self, "Подпись", message)

    def reject(self) -> None:
        self._poll.stop()
        super().reject()
