"""UI for LAN signing sessions: host (organizer) and participant dialogs."""
from __future__ import annotations

import html
import pathlib
import time

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import crypto, network, pdf as pdfmod, service, theme

STATUS_COLORS = {
    network.STATUS_PENDING: "#B45309",
    network.STATUS_SIGNING: theme.PRIMARY,
    network.STATUS_SIGNED: theme.SUCCESS,
    network.STATUS_DECLINED: theme.ERROR,
    network.STATUS_ERROR: theme.ERROR,
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


def _shadow(widget: QWidget) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(28)
    effect.setOffset(0, 4)
    effect.setColor(QColor(0, 0, 0, 26))
    widget.setGraphicsEffect(effect)


def _confirm(parent, title: str, text: str) -> bool:
    """Yes/No confirmation with a fixed button order on every platform.

    Native ``QMessageBox`` reorders Yes/No depending on the platform style, so
    the buttons are laid out explicitly (Да слева, Нет справа) — identical on
    Windows and Linux and always in Russian.
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setStyleSheet(theme.stylesheet())
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 20, 22, 16)
    layout.setSpacing(14)
    label = QLabel(text)
    label.setWordWrap(True)
    layout.addWidget(label)
    buttons = QHBoxLayout()
    buttons.addStretch(1)
    yes = QPushButton("Да")
    yes.setObjectName("Filled")
    no = QPushButton("Нет")
    no.setObjectName("Text")
    yes.clicked.connect(dialog.accept)
    no.clicked.connect(dialog.reject)
    buttons.addWidget(yes)
    buttons.addWidget(no)
    layout.addLayout(buttons)
    yes.setDefault(True)
    return dialog.exec() == QDialog.DialogCode.Accepted


def _status_table() -> QTreeWidget:
    table = QTreeWidget()
    table.setColumnCount(4)
    table.setHeaderLabels(["Участник", "Организация", "Статус", "Время"])
    table.setRootIsDecorated(False)
    table.setAlternatingRowColors(False)
    table.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    header = table.header()
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
    table.setMinimumHeight(170)
    table.setMinimumWidth(300)
    return table


def _fill_status_table(
    table: QTreeWidget, snapshot: dict, highlight_id: str = "", show_handle: bool = False
) -> None:
    table.clear()
    for item in snapshot.get("participants", []):
        name = f'{item["order"]}. {item["name"]}'
        if item.get("is_host"):
            name = f'★ {name}'
        if highlight_id and item["id"] == highlight_id:
            name += " · вы"
        if show_handle and item["status"] not in (network.STATUS_SIGNED, network.STATUS_DECLINED):
            name = f"⋮⋮   {name}"
        status_text = item["status_label"] + (
            f' — {item["detail"]}' if item["detail"] else ""
        )
        row = QTreeWidgetItem([
            name,
            item["organization"] or "—",
            item["status_label"],
            item["signed_at"] or item["joined_at"] or "",
        ])
        row.setData(0, Qt.ItemDataRole.UserRole, item["id"])
        row.setToolTip(2, status_text)
        tooltip = item["name"]
        if item.get("is_host"):
            tooltip += " · организатор сессии"
        if item["organization"]:
            tooltip += f" · {item['organization']}"
        row.setToolTip(0, tooltip)
        color = STATUS_COLORS.get(item["status"], theme.ON_SURFACE_VARIANT)
        for column in range(4):
            row.setForeground(column, QColor(color))
        if item["id"] == highlight_id:
            font = row.font(0)
            font.setBold(True)
            for column in range(4):
                row.setFont(column, font)
        table.addTopLevelItem(row)


# ---------------------------------------------------------------------------
# Shared helpers: settings, clickable labels, certificate list
# ---------------------------------------------------------------------------

_SETTINGS_ORG = "Tripswarlock"
_SETTINGS_APP = "pdf-multisign"


def _read_last_address() -> str:
    """Last address the participant connected to (prefilled on the next run)."""
    try:
        value = QSettings(_SETTINGS_ORG, _SETTINGS_APP).value("network/last_address", "")
        return str(value or "").strip()
    except Exception:
        return ""


def _remember_address(value: str) -> None:
    value = (value or "").strip()
    if not value:
        return
    try:
        settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        settings.setValue("network/last_address", value)
        settings.sync()
    except Exception:
        pass


class ClickableLabel(QLabel):
    """Label that emits ``clicked`` on a left mouse button release."""

    clicked = Signal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class CertificateChooser(QWidget):
    """Vertical list of certificates with single selection."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._layout = layout
        self._rows: list = []
        self._updating = False
        self.reload()

    def reload(self) -> None:
        from .window import CertificateRow

        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._rows.clear()

        available, detail = crypto.crypto_status()
        if not available:
            self._message(detail)
            return
        try:
            certificates = crypto.list_certificates()
        except crypto.CryptoError as exc:
            self._message(str(exc))
            return
        if not certificates:
            self._message(
                service.store_label() + " не содержит сертификатов\n" + crypto.store_hint(),
                error=False,
            )
            return
        for info in certificates:
            row = CertificateRow(info)
            row._selector = self._on_row_selected
            self._layout.addWidget(row)
            self._rows.append(row)
        self._layout.addStretch(1)

    def _message(self, text: str, error: bool = True) -> None:
        label = QLabel(text)
        label.setObjectName("Error" if error else "Caption")
        label.setWordWrap(True)
        self._layout.addWidget(label)
        self._layout.addStretch(1)
        self._rows.append(label)

    def _on_row_selected(self, selected_row) -> None:
        from .window import CertificateRow

        if self._updating:
            return
        self._updating = True
        try:
            for other in self._rows:
                if isinstance(other, CertificateRow) and other is not selected_row:
                    other.set_checked(False)
        finally:
            self._updating = False
        self.changed.emit()

    def selected(self):
        from .window import CertificateRow

        for row in self._rows:
            if isinstance(row, CertificateRow) and row.is_checked:
                return row.info
        return None


class HostCertificateDialog(QDialog):
    """The organizer picks its own certificate to sign with."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Сертификат организатора")
        self.resize(600, 520)
        self.setStyleSheet(theme.stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)

        title = QLabel("Подпись организатора")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        hint = QLabel(
            "Документ будет подписан вашим CryptoPro CSP — так же, как у участников. "
            "Подпись войдёт в итоговый файл (последовательная схема) или в архив "
            "отдельным .sig (соподпись)."
        )
        hint.setObjectName("Caption")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._chooser = CertificateChooser(self)
        self._chooser.changed.connect(self._refresh)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self._chooser)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.setObjectName("Text")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self._ok = QPushButton("Подписать")
        self._ok.setObjectName("Filled")
        self._ok.clicked.connect(self.accept)
        buttons.addWidget(self._ok)
        layout.addLayout(buttons)

        self._refresh()

    def _refresh(self) -> None:
        cert = self._chooser.selected()
        usable = cert is not None and cert.sign_capable and cert.is_valid_now
        self._ok.setEnabled(usable)
        self._ok.setToolTip(
            "" if usable else "Выберите действующий сертификат с ключом CryptoPro"
        )

    def selected(self):
        return self._chooser.selected()


# ---------------------------------------------------------------------------
# Menu: choose the role
# ---------------------------------------------------------------------------

class RoleDialog(QDialog):
    """Pick «organizer» or «participant»."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Сетевая подпись")
        self.setMinimumWidth(560)
        self.setStyleSheet(theme.stylesheet())
        self.choice = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        title = QLabel("Подписание по локальной сети")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        hint = QLabel(
            "Несколько компьютеров подписывают один документ: организатор создаёт сессию "
            "и показывает адрес и код, участники присоединяются со своих машин. "
            "Статусы видны всем в реальном времени. Закрытые ключи не покидают "
            "компьютеры участников — по сети передаётся только готовая подпись."
        )
        hint.setObjectName("Caption")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        host_card, host_layout = _card("Этот компьютер — организатор")
        _shadow(host_card)
        host_text = QLabel(
            "Загруженный PDF станет документом сессии. Вы получите адрес и код для участников "
            "и сохраните итоговый файл после подписания."
        )
        host_text.setObjectName("Caption")
        host_text.setWordWrap(True)
        host_layout.addWidget(host_text)
        self._host_button = QPushButton("Создать сессию")
        self._host_button.setObjectName("Filled")
        self._host_button.clicked.connect(self._pick_host)
        host_layout.addWidget(self._host_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(host_card)

        join_card, join_layout = _card("Присоединиться к сессии")
        _shadow(join_card)
        join_text = QLabel(
            "Введите адрес и код, которые сообщил организатор, выберите свой сертификат "
            "и подпишите документ своим CryptoPro CSP."
        )
        join_text.setObjectName("Caption")
        join_text.setWordWrap(True)
        join_layout.addWidget(join_text)
        self._join_button = QPushButton("Присоединиться")
        self._join_button.setObjectName("Tonal")
        self._join_button.clicked.connect(self._pick_join)
        join_layout.addWidget(self._join_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(join_card)

        close = QPushButton("Отмена")
        close.setObjectName("Text")
        close.clicked.connect(self.reject)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

    def _pick_host(self) -> None:
        self.choice = "host"
        self.accept()

    def _pick_join(self) -> None:
        self.choice = "join"
        self.accept()


# ---------------------------------------------------------------------------
# Host: setup
# ---------------------------------------------------------------------------

class HostSetupDialog(QDialog):
    """Session parameters before the server starts."""

    def __init__(self, doc_name: str, page_count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Параметры сессии")
        self.setMinimumWidth(620)
        self.setStyleSheet(theme.stylesheet())
        self.config: network.SessionConfig | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        doc = QLabel(f"Документ сессии: {doc_name} · {page_count} стр.")
        doc.setObjectName("Caption")
        layout.addWidget(doc)

        card, card_layout = _card("Схема подписания")
        _shadow(card)

        self._parallel = QRadioButton("Параллельная подпись")
        self._parallel.setChecked(True)
        card_layout.addWidget(self._parallel)
        par_hint = QLabel(
            "Каждый участник независимо подписывает исходный файл. Итог — "
            "исходный PDF и отдельные файлы .sig для каждого подписанта."
        )
        par_hint.setObjectName("Caption")
        par_hint.setWordWrap(True)
        card_layout.addWidget(par_hint)

        par_sub = QWidget()
        par_sub_layout = QVBoxLayout(par_sub)
        par_sub_layout.setContentsMargins(38, 0, 0, 0)
        par_sub_layout.setSpacing(8)
        self._protocol = QCheckBox("Протокол подписания (Protokol.pdf в архиве)")
        self._protocol.setChecked(True)
        par_sub_layout.addWidget(self._protocol)
        card_layout.addWidget(par_sub)

        self._sequential = QRadioButton("Последовательная подпись")
        card_layout.addWidget(self._sequential)
        seq_hint = QLabel(
            "Каждый участник подписывает актуальную версию документа, встраивая "
            "свою подпись в PDF. Итог — один файл с цепочкой подписей."
        )
        seq_hint.setObjectName("Caption")
        seq_hint.setWordWrap(True)
        card_layout.addWidget(seq_hint)

        # sub-options nested under the sequential scheme
        sub = QWidget()
        sub_layout = QVBoxLayout(sub)
        sub_layout.setContentsMargins(38, 0, 0, 0)
        sub_layout.setSpacing(8)
        self._strict = QCheckBox(
            "Строгая очередь — подписывать можно только строго по очереди "
            "из списка участников"
        )
        sub_layout.addWidget(self._strict)
        self._visible = QCheckBox("Видимый штамп подписи в документе")
        sub_layout.addWidget(self._visible)
        page_row = QHBoxLayout()
        page_row.setContentsMargins(24, 0, 0, 0)
        page_caption = QLabel("Страница штампа")
        page_caption.setObjectName("Caption")
        self._page_combo = QComboBox()
        self._page_combo.addItem("Последняя", -1)
        for index in range(page_count):
            self._page_combo.addItem(f"Страница {index + 1}", index)
        self._page_combo.addItem("Все страницы", pdfmod.ALL_PAGES)
        page_row.addWidget(page_caption)
        page_row.addWidget(self._page_combo, 1)
        sub_layout.addLayout(page_row)
        card_layout.addWidget(sub)

        layout.addWidget(card)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.setObjectName("Text")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        start = QPushButton("Запустить сессию")
        start.setObjectName("Filled")
        start.clicked.connect(self._start)
        buttons.addWidget(start)
        layout.addLayout(buttons)

        self._sequential.toggled.connect(self._toggle_scheme)
        self._parallel.toggled.connect(self._toggle_scheme)
        self._visible.toggled.connect(self._toggle_scheme)
        self._toggle_scheme()

    def _toggle_scheme(self) -> None:
        sequential = self._sequential.isChecked()
        self._strict.setEnabled(sequential)
        self._visible.setEnabled(sequential)
        self._page_combo.setEnabled(sequential and self._visible.isChecked())
        self._protocol.setEnabled(not sequential)

    def _start(self) -> None:
        self.config = network.SessionConfig(
            mode=(network.MODE_SEQUENTIAL if self._sequential.isChecked()
                  else network.MODE_PARALLEL),
            strict_order=self._strict.isChecked() and self._sequential.isChecked(),
            visible=self._visible.isChecked() and self._sequential.isChecked(),
            page_index=self._page_combo.currentData(),
            protocol=self._protocol.isChecked() and not self._sequential.isChecked(),
        )
        self.accept()


# ---------------------------------------------------------------------------
# Host: signing with the organizer's own certificate
# ---------------------------------------------------------------------------

class HostSignWorker(QThread):
    """Signs the session document with a local certificate (in-process)."""

    progress = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, session: network.SignSession, token: str, thumbprint: str,
                 mode: str):
        super().__init__()
        self._session = session
        self._token = token
        self._thumbprint = thumbprint
        self._mode = mode

    def run(self) -> None:
        try:
            if self._mode == network.MODE_SEQUENTIAL:
                note = self._sign_sequential()
            else:
                note = self._sign_parallel()
            self.done.emit(note)
        except crypto.CryptoError as exc:
            self._session.cancel_pending(self._token)
            self.failed.emit(str(exc))
        except network.NetworkError as exc:
            self._session.cancel_pending(self._token)
            self.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self._session.cancel_pending(self._token)
            self.failed.emit(f"Непредвиденная ошибка: {exc}")

    def _sign_sequential(self) -> str:
        attempts = 6
        for attempt in range(attempts):
            try:
                signed_bytes, _version = self._session.prepare_signature(self._token)
            except network.NetworkError as exc:
                text = str(exc)
                # strict-order violations are final; a busy reservation is retried
                retry = attempt < attempts - 1 and "Подождите" in text
                if not retry:
                    raise
                self.progress.emit(f"{text} (повтор через 3 с)…")
                time.sleep(3)
                continue
            self.progress.emit(
                "CryptoPro создаёт отсоединённую подпись CAdES-BES…"
                if attempt == 0 else "Документ обновился, подписываем заново…"
            )
            cms = crypto.sign_detached(signed_bytes, self._thumbprint)
            self.progress.emit("Подпись встраивается в документ…")
            try:
                return self._session.commit_signature(self._token, cms)
            except network.NetworkError as exc:
                self._session.cancel_pending(self._token)
                if attempt < attempts - 1 and any(
                    marker in str(exc).lower() for marker in ("верси", "заново", "найдена")
                ):
                    continue
                raise
        raise network.NetworkError("Не удалось подписать документ за несколько попыток")

    def _sign_parallel(self) -> str:
        self.progress.emit("CryptoPro создаёт отсоединённую подпись CAdES-BES…")
        cms = crypto.sign_detached(self._session.document_bytes(), self._thumbprint)
        self.progress.emit("Подпись добавляется в пакет…")
        return self._session.submit_signature(
            self._token, self._session.version, cms
        )


# ---------------------------------------------------------------------------
# Host: live session
# ---------------------------------------------------------------------------

class HostSessionDialog(QDialog):
    """Shows the join code/addresses and live participant statuses."""

    def __init__(self, session: network.SignSession, server: network.SessionServer,
                 parent=None):
        super().__init__(parent)
        self._session = session
        self._server = server
        self.setWindowTitle("Сетевая сессия — организатор")
        self.resize(700, 660)
        self.setStyleSheet(theme.stylesheet())
        self._saved = False
        self._host_participant: network.Participant | None = None
        self._host_cert = None
        self._host_worker: HostSignWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        info_card, info_layout = _card("Подключение участников")
        _shadow(info_card)
        snap = session.snapshot()
        doc_line = QLabel(f"Документ: {snap['doc_name']} · {snap['mode_label']}")
        doc_line.setObjectName("Caption")
        doc_line.setWordWrap(True)
        info_layout.addWidget(doc_line)

        code_row = QHBoxLayout()
        code_caption = QLabel("Код сессии — нажмите, чтобы скопировать")
        code_caption.setObjectName("Caption")
        self._code_label = ClickableLabel(session.code)
        self._code_label.setStyleSheet(
            f"font-size: 26px; font-weight: 700; letter-spacing: 6px; color: {theme.PRIMARY};"
        )
        self._code_label.setToolTip("Скопировать код сессии в буфер обмена")
        self._code_label.clicked.connect(self._copy_code)
        self._copied_note = QLabel("✓ скопировано")
        self._copied_note.setStyleSheet(
            f"color: {theme.SUCCESS}; font-size: 12px; font-weight: 600;"
        )
        self._copied_note.setVisible(False)
        code_row.addWidget(code_caption)
        code_row.addWidget(self._code_label)
        code_row.addWidget(self._copied_note)
        code_row.addStretch(1)
        info_layout.addLayout(code_row)

        self._links = QLabel()
        self._links.setObjectName("Caption")
        self._links.setWordWrap(True)
        self._links.setVisible(False)
        info_layout.addWidget(self._links)
        self._links_edit = QLineEdit()
        self._links_edit.setReadOnly(True)
        self._links_edit.setMinimumWidth(150)
        self._links_edit.setToolTip("Адреса для подключения участников")
        info_layout.addWidget(self._links_edit)

        copy = QPushButton("Скопировать приглашение")
        copy.setObjectName("Tonal")
        copy.clicked.connect(self._copy_invite)
        info_layout.addWidget(copy, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(info_card)

        status_card, status_layout = _card("Статусы участников")
        _shadow(status_card)
        self._summary = QLabel()
        self._summary.setObjectName("Caption")
        self._summary.setWordWrap(True)
        status_layout.addWidget(self._summary)
        self._table = _status_table()
        self._table.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._table.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        self._table.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._table.setDragEnabled(True)
        self._table.setAcceptDrops(True)
        self._table.setDropIndicatorShown(True)
        self._table.model().rowsMoved.connect(self._on_rows_moved)
        status_layout.addWidget(self._table)

        order_row = QHBoxLayout()
        self._order_hint = QLabel(
            "Порядок подписания: перетащите строку за «⋮⋮» или используйте кнопки"
        )
        self._order_hint.setObjectName("Caption")
        self._order_hint.setWordWrap(True)
        order_row.addWidget(self._order_hint, 1)
        self._up_button = QPushButton("Выше")
        self._up_button.setObjectName("Text")
        self._up_button.clicked.connect(lambda: self._move_selected(-1))
        self._down_button = QPushButton("Ниже")
        self._down_button.setObjectName("Text")
        self._down_button.clicked.connect(lambda: self._move_selected(1))
        order_row.addWidget(self._up_button)
        order_row.addWidget(self._down_button)
        status_layout.addLayout(order_row)

        self._hint = QLabel(
            "Страница статусов также открывается в браузере по адресу сессии — "
            "удобно показывать на общем экране."
        )
        self._hint.setObjectName("Caption")
        self._hint.setWordWrap(True)
        status_layout.addWidget(self._hint)
        layout.addWidget(status_card, 1)

        self._order_enabled = session.config.mode == network.MODE_SEQUENTIAL
        for widget in (self._table, self._up_button, self._down_button, self._order_hint):
            widget.setEnabled(self._order_enabled)
        if not self._order_enabled:
            self._order_hint.setText(
                "При соподписи участники подписывают независимо — порядок не важен"
            )
            self._table.setDragDropMode(QTreeWidget.DragDropMode.NoDragDrop)
        elif session.config.strict_order:
            self._order_hint.setText(
                "Строгая очередь: перетащите строку за «⋮⋮» или используйте кнопки — "
                "подписывать сможет только первый в списке"
            )
        self._table.setToolTip("Порядок подписания можно менять перетаскиванием")

        self._log = QLabel()
        self._log.setObjectName("Caption")
        self._log.setWordWrap(False)
        self._log.setMinimumHeight(18)
        self._log.setVisible(False)
        layout.addWidget(self._log)

        buttons = QHBoxLayout()
        self._abort = QPushButton("Прервать")
        self._abort.setObjectName("Text")
        self._abort.setStyleSheet(f"QPushButton {{ color: {theme.ERROR}; }}")
        self._abort.clicked.connect(self._abort_session)
        buttons.addWidget(self._abort)
        buttons.addStretch(1)
        self._sign = QPushButton("Подписать документ")
        self._sign.setObjectName("Tonal")
        self._sign.setToolTip(
            "Подписать документ своим сертификатом как организатор"
        )
        self._sign.clicked.connect(self._sign_as_host)
        buttons.addWidget(self._sign)
        self._finish = QPushButton("Завершить и сохранить")
        self._finish.setObjectName("Filled")
        self._finish.clicked.connect(self._finish_session)
        buttons.addWidget(self._finish)
        layout.addLayout(buttons)

        self._copy_timer = QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.timeout.connect(lambda: self._copied_note.setVisible(False))

        self._timer = QTimer(self)
        self._timer.setInterval(800)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._tick()

    # ------------------------------------------------------------------ logic
    def _tick(self) -> None:
        snap = self._session.snapshot()
        host_pid = self._host_participant.pid if self._host_participant else ""
        _fill_status_table(
            self._table, snap, highlight_id=host_pid, show_handle=self._order_enabled
        )
        self._update_links(self._server.addresses())
        signed = sum(1 for p in snap["participants"] if p["status"] == network.STATUS_SIGNED)
        declined = sum(1 for p in snap["participants"] if p["status"] == network.STATUS_DECLINED)
        signing = sum(1 for p in snap["participants"] if p["status"] == network.STATUS_SIGNING)
        self._summary.setText(
            f"Участников: {len(snap['participants'])} · подписало: {signed} · "
            f"отказалось: {declined} · подписывает: {signing} · "
            f"версия документа: {snap['doc_version']}"
        )
        self._update_sign_button(snap)

    def _update_sign_button(self, snap: dict) -> None:
        if self._host_worker is not None:
            return
        if self._host_participant is None:
            self._sign.setText("Подписать документ")
            self._sign.setEnabled(not snap["finished"])
            return
        for item in snap["participants"]:
            if item["id"] != self._host_participant.pid:
                continue
            if item["status"] == network.STATUS_SIGNED:
                self._sign.setText("Подписано ✓")
                self._sign.setEnabled(False)
            else:
                self._sign.setText("Подписать документ")
                self._sign.setEnabled(not snap["finished"])
            return

    def _log_message(self, text: str) -> None:
        if not text:
            return
        self._log.setText(text)
        self._log.setToolTip(text)
        self._log.setVisible(True)

    def _copy_code(self) -> None:
        QApplication.clipboard().setText(self._session.code)
        self._copied_note.setVisible(True)
        self._copy_timer.start(1800)
        self._log_message(f"Код {self._session.code} скопирован в буфер обмена")

    # ------------------------------------------------------- host signing
    def _sign_as_host(self) -> None:
        if self._host_worker is not None:
            return
        if self._session.snapshot()["finished"]:
            QMessageBox.information(self, "Подпись", "Сессия уже завершена.")
            return
        if self._host_participant is None or self._host_cert is None:
            dialog = HostCertificateDialog(self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            cert = dialog.selected()
            if cert is None:
                return
            if self._host_participant is None:
                try:
                    participant = self._session.join_host(network.certificate_payload(cert))
                except network.NetworkError as exc:
                    QMessageBox.warning(self, "Подпись", str(exc))
                    return
                self._host_participant = participant
                self._log_message(
                    f"Вы в сессии как {cert.display_name} (организатор)"
                )
            self._host_cert = cert
        self._sign.setEnabled(False)
        self._sign.setText("Подписание…")
        self._finish.setEnabled(False)
        self._host_worker = HostSignWorker(
            self._session,
            self._host_participant.token,
            self._host_cert.thumbprint,
            self._session.config.mode,
        )
        self._host_worker.progress.connect(self._on_host_progress)
        self._host_worker.done.connect(self._on_host_signed)
        self._host_worker.failed.connect(self._on_host_failed)
        self._host_worker.start()
        self._tick()

    def _on_host_progress(self, message: str) -> None:
        self._sign.setText("Подписание…")
        self._log_message(message)

    def _on_host_signed(self, note: str) -> None:
        self._host_worker = None
        self._finish.setEnabled(True)
        self._sign.setText("Подписано ✓")
        self._sign.setEnabled(False)
        self._log_message(
            "Подпись организатора добавлена" + (f" · {note}" if note else "")
        )
        self._tick()
        QMessageBox.information(
            self, "Готово",
            "Документ подписан вашим сертификатом. Подпись организатора "
            "войдёт в итоговый файл вместе с подписями участников.",
        )

    def _on_host_failed(self, message: str) -> None:
        self._host_worker = None
        self._finish.setEnabled(True)
        self._sign.setText("Подписать документ")
        self._sign.setEnabled(True)
        self._log_message(f"✗ {message}")
        if "очеред" in message.lower():
            QMessageBox.information(self, "Очередь", message)
        else:
            QMessageBox.critical(self, "Подпись", message)

    # ------------------------------------------------------------ ordering
    def _table_pids(self) -> list[str]:
        pids: list[str] = []
        for index in range(self._table.topLevelItemCount()):
            item = self._table.topLevelItem(index)
            pid = item.data(0, Qt.ItemDataRole.UserRole)
            if pid:
                pids.append(str(pid))
        return pids

    def _on_rows_moved(self, *_args) -> None:
        if not self._order_enabled:
            return
        pids = self._table_pids()
        try:
            self._session.reorder_participants(pids)
        except network.NetworkError as exc:
            self._log_message(f"✗ Порядок не изменён: {exc}")
        else:
            self._log_message("Порядок подписания обновлён")
        self._tick()

    def _move_selected(self, delta: int) -> None:
        if not self._order_enabled:
            return
        item = self._table.currentItem()
        if item is None:
            self._log_message("Выберите участника в таблице, чтобы изменить порядок")
            return
        pid = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        try:
            moved = self._session.move_participant(pid, delta)
        except network.NetworkError as exc:
            self._log_message(f"✗ Порядок не изменён: {exc}")
            return
        if moved:
            self._log_message("Порядок подписания обновлён")
            self._tick()
            self._select_pid(pid)

    def _select_pid(self, pid: str) -> None:
        for index in range(self._table.topLevelItemCount()):
            item = self._table.topLevelItem(index)
            if str(item.data(0, Qt.ItemDataRole.UserRole) or "") == pid:
                self._table.setCurrentItem(item)
                return

    def _update_links(self, links: list[str]) -> None:
        text = " · ".join(links)
        if self._links_edit.text() != text:
            self._links_edit.setText(text)
            self._links_edit.setCursorPosition(0)

    def _copy_invite(self) -> None:
        snap = self._session.snapshot()
        links = self._server.addresses()
        text = (
            f"Сетевая подпись PDF\n"
            f"Документ: {snap['doc_name']}\n"
            f"Схема: {snap['mode_label']}\n"
            f"Адрес: {links[0]}\n"
            f"Код: {snap['code']}\n\n"
            "В программе «PDF Мультиподпись»: Сетевая подпись → Присоединиться к сессии."
        )
        QApplication.clipboard().setText(text)
        self._log_message("Приглашение скопировано в буфер обмена")

    def _abort_session(self) -> None:
        if not _confirm(
            self,
            "Прервать сессию",
            "Участники потеряют доступ к сессии. Итоговый файл не будет сохранён.\n"
            "Прервать?",
        ):
            return
        self._session.finalize("Сессия прервана организатором")
        self.reject()

    def _finish_session(self) -> None:
        snap = self._session.snapshot()
        signed = sum(1 for p in snap["participants"] if p["status"] == network.STATUS_SIGNED)
        if signed == 0:
            QMessageBox.warning(self, "Сессия", "Пока никто не подписал документ.")
            return
        document, sig_files, note = self._session.results()
        stem = pathlib.Path(snap["doc_name"]).stem or "document"
        if sig_files:
            default = str(pathlib.Path(snap["doc_name"]).with_name(self._session.archive_name()))
            path, _ = QFileDialog.getSaveFileName(
                self, "Сохранить архив с подписями", default, "ZIP (*.zip)"
            )
            if not path:
                return
            if not path.lower().endswith(".zip"):
                path += ".zip"
            try:
                pathlib.Path(path).write_bytes(self._session.package_zip())
            except OSError as exc:
                QMessageBox.critical(self, "Сохранение", f"Не удалось сохранить архив: {exc}")
                return
            QMessageBox.information(
                self, "Готово",
                f"Архив сохранён:\n{path}\n\n"
                f"Внутри: {snap['doc_name']}, {len(sig_files)} файл(а) подписей .sig"
                + (", Protokol.pdf" if self._session.config.protocol else "")
                + ".\nФайлы .sig проверяются штатно через CryptoPro (cryptcp -vsign).",
            )
        else:
            default = str(pathlib.Path(snap["doc_name"]).with_name(stem + "-network-signed.pdf"))
            path, _ = QFileDialog.getSaveFileName(
                self, "Сохранить итоговый PDF", default, "PDF (*.pdf)"
            )
            if not path:
                return
            try:
                pathlib.Path(path).write_bytes(document)
            except OSError as exc:
                QMessageBox.critical(self, "Сохранение", f"Не удалось сохранить файл: {exc}")
                return
            QMessageBox.information(
                self, "Готово",
                "Итоговый PDF сохранён.\n\nПодписи видны в Adobe Reader "
                "и проверяются штатными средствами CryptoPro.",
            )
        self._session.finalize(note)
        self._saved = True
        self.accept()

    def reject(self) -> None:
        self._timer.stop()
        worker = self._host_worker
        self._host_worker = None
        if worker is not None and worker.isRunning():
            for signal in (worker.progress, worker.done, worker.failed):
                try:
                    signal.disconnect()
                except Exception:
                    pass
            worker.terminate()
            worker.wait(2000)
        self._server.shutdown()
        if not self._saved and not self._session.snapshot()["finished"]:
            self._session.finalize("Окно организатора закрыто")
        super().reject()


# ---------------------------------------------------------------------------
# Participant: join & sign
# ---------------------------------------------------------------------------

class ClientSignWorker(QThread):
    progress = Signal(str)
    done = Signal()
    failed = Signal(str)

    def __init__(self, client: network.SessionClient, thumbprint: str, mode: str):
        super().__init__()
        self._client = client
        self._thumbprint = thumbprint
        self._mode = mode

    def run(self) -> None:
        try:
            self._client.set_status(network.STATUS_SIGNING)
        except network.NetworkError:
            pass
        try:
            if self._mode == network.MODE_SEQUENTIAL:
                self._sign_sequential()
            else:
                self._sign_parallel()
            self.progress.emit("Ожидаем подтверждения от организатора…")
            self._wait_confirmed()
            self.done.emit()
        except crypto.CryptoError as exc:
            self._mark_error(str(exc))
            self.failed.emit(str(exc))
        except network.NetworkError as exc:
            self._mark_error(str(exc))
            self.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self._mark_error(f"Непредвиденная ошибка: {exc}")
            self.failed.emit(f"Непредвиденная ошибка: {exc}")

    def _sign_sequential(self) -> None:
        """Two-phase signing: the host pins the revision and gives exact bytes."""
        attempts = 6
        for attempt in range(attempts):
            try:
                signed_bytes = self._client.prepare()
            except network.NetworkError as exc:
                # another participant is signing right now — wait and retry
                if attempt < attempts - 1:
                    self.progress.emit(f"{exc} (повтор через 3 с)…")
                    time.sleep(3)
                    continue
                raise
            self.progress.emit(
                "CryptoPro создаёт отсоединённую подпись CAdES-BES…"
                if attempt == 0 else "Документ обновился, подписываем заново…"
            )
            cms = crypto.sign_detached(signed_bytes, self._thumbprint)
            self.progress.emit("Подпись отправляется организатору…")
            try:
                self._client.commit(cms)
                return
            except network.NetworkError as exc:
                try:
                    self._client.cancel_pending()
                except network.NetworkError:
                    pass
                if attempt < attempts - 1 and any(
                    marker in str(exc).lower() for marker in ("верси", "заново", "найдена")
                ):
                    continue
                raise
        raise network.NetworkError("Не удалось подписать документ за несколько попыток")

    def _sign_parallel(self) -> None:
        data, version = self._client.document()
        self.progress.emit("CryptoPro создаёт отсоединённую подпись CAdES-BES…")
        cms = crypto.sign_detached(data, self._thumbprint)
        self.progress.emit("Подпись отправляется организатору…")
        self._client.submit(version, cms)

    def _mark_error(self, message: str) -> None:
        try:
            self._client.set_status(network.STATUS_ERROR, message[:400])
        except Exception:
            pass
        try:
            self._client.cancel_pending()
        except Exception:
            pass

    def _wait_confirmed(self, timeout_s: int = 90) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            state = self._client.state()
            for item in state.get("participants", []):
                if item["id"] != self._client.participant_id:
                    continue
                if item["status"] == network.STATUS_SIGNED:
                    return
                if item["status"] in (network.STATUS_ERROR, network.STATUS_DECLINED):
                    raise network.NetworkError(item["detail"] or item["status_label"])
            time.sleep(1.0)
        raise network.NetworkError("Организатор не подтвердил подпись вовремя")


class JoinSessionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Присоединиться к сессии")
        self.resize(720, 720)
        self.setStyleSheet(theme.stylesheet())
        self._client: network.SessionClient | None = None
        self._worker: ClientSignWorker | None = None
        self._joined = False
        self._signed = False
        self._declined = False
        self._mode = network.MODE_SEQUENTIAL
        self._protocol = True
        self._connection_state = "idle"
        self._poll_failures = 0
        self._session_summary = ""
        self._download_available = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        conn_card, conn_layout = _card("Адрес сессии")
        _shadow(conn_card)
        row = QHBoxLayout()
        addr_caption = QLabel("Адрес")
        addr_caption.setObjectName("Caption")
        self._address = QLineEdit()
        self._address.setText(_read_last_address())
        self._address.setPlaceholderText("192.168.1.50 — порт 8443 подставится сам")
        port_caption = QLabel("Код")
        port_caption.setObjectName("Caption")
        self._code = QLineEdit()
        self._code.setMaxLength(8)
        self._code.setFixedWidth(110)
        self._code.setPlaceholderText("6 знаков")
        self._connect = QPushButton("Подключиться")
        self._connect.setObjectName("Filled")
        self._connect.setEnabled(False)
        self._connect.setToolTip(
            "Сначала выберите сертификат — без него подключиться нельзя"
        )
        self._connect.clicked.connect(self._connect_session)
        row.addWidget(addr_caption)
        row.addWidget(self._address, 1)
        row.addWidget(port_caption)
        row.addWidget(self._code)
        row.addWidget(self._connect)
        conn_layout.addLayout(row)
        self._conn_status = QLabel()
        self._conn_status.setTextFormat(Qt.TextFormat.RichText)
        conn_layout.addWidget(self._conn_status)
        self._session_info = QLabel(
            "Выберите сертификат ниже — после этого кнопка «Подключиться» "
            "станет доступной."
        )
        self._session_info.setObjectName("Caption")
        self._session_info.setWordWrap(True)
        conn_layout.addWidget(self._session_info)
        layout.addWidget(conn_card)

        cert_card, cert_layout = _card("Ваш сертификат")
        _shadow(cert_card)
        hint = QLabel("Выберите сертификат для подписания — документ подпишет CryptoPro CSP "
                      "на этом компьютере, закрытый ключ по сети не передаётся.")
        hint.setObjectName("Caption")
        hint.setWordWrap(True)
        cert_layout.addWidget(hint)
        self._certs = CertificateChooser()
        self._certs.changed.connect(self._on_cert_changed)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(150)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self._certs)
        cert_layout.addWidget(scroll, 1)
        layout.addWidget(cert_card, 1)

        status_card, status_layout = _card("Статусы участников")
        _shadow(status_card)
        self._table = _status_table()
        self._table.setMinimumHeight(120)
        status_layout.addWidget(self._table)
        layout.addWidget(status_card)

        actions = QHBoxLayout()
        self._decline = QPushButton("Отказаться от подписи")
        self._decline.setObjectName("Text")
        self._decline.setStyleSheet(f"QPushButton {{ color: {theme.ERROR}; }}")
        self._decline.setEnabled(False)
        self._decline.clicked.connect(self._decline_signing)
        actions.addWidget(self._decline)
        self._download = QPushButton("Скачать архив")
        self._download.setObjectName("Text")
        self._download.setEnabled(False)
        self._download.setToolTip("Пока никто не подписал — скачивать нечего")
        self._download.clicked.connect(self._download_package)
        actions.addWidget(self._download)
        actions.addStretch(1)
        self._close = QPushButton("Закрыть")
        self._close.setObjectName("Text")
        self._close.clicked.connect(self.reject)
        actions.addWidget(self._close)
        self._sign = QPushButton("Подписать документ")
        self._sign.setObjectName("Filled")
        self._sign.setEnabled(False)
        self._sign.clicked.connect(self._start_signing)
        actions.addWidget(self._sign)
        layout.addLayout(actions)

        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._poll)
        self._set_connection_state("idle")
        self._on_cert_changed()

    # ------------------------------------------------------------ connection
    def _connect_session(self) -> None:
        self._poll_failures = 0
        try:
            candidate = network.SessionClient(self._address.text(), self._code.text())
        except network.NetworkError as exc:
            QMessageBox.warning(self, "Подключение", str(exc))
            return
        if self._client is None or (candidate.base_url, candidate.code) != (
            self._client.base_url,
            self._client.code,
        ):
            self._client = candidate
            self._joined = False
        try:
            info = self._client.info()
        except network.NetworkError as exc:
            QMessageBox.warning(self, "Подключение", str(exc))
            self._reset_connection()
            return
        if info.get("finished"):
            QMessageBox.information(self, "Сессия", "Эта сессия уже завершена.")
            self._reset_connection()
            return
        self._mode = str(info.get("mode") or network.MODE_SEQUENTIAL)
        self._protocol = bool(info.get("protocol", True))
        _remember_address(self._address.text())
        self._session_summary = (
            f"Документ: {info['doc_name']} · {info['mode_label']}"
            + (" · строгая очередь" if info.get("strict_order") else "")
        )
        self._session_info.setText(self._session_summary)
        if self._try_join():
            self._connect.setText("Переподключиться")
            self._set_connection_state("connected")
            self._timer.start()

    def _reset_connection(self) -> None:
        self._client = None
        self._joined = False
        self._signed = False
        self._declined = False
        self._session_summary = ""
        self._connect.setText("Подключиться")
        self._set_connection_state("idle")
        self._apply_download_style(False)
        self._download.setToolTip("Пока никто не подписал — скачивать нечего")
        self._on_cert_changed()

    def _try_join(self) -> bool:
        cert = self._current_cert()
        if cert is None or self._client is None:
            return False
        if self._joined:
            return True
        try:
            self._client.join(cert)
        except network.NetworkError as exc:
            QMessageBox.warning(self, "Сессия", str(exc))
            self._reset_connection()
            return False
        self._joined = True
        self._decline.setEnabled(True)
        self._sign.setEnabled(cert.sign_capable and cert.is_valid_now)
        self._session_info.setText(
            self._session_summary
            + f"\nВы в сессии как {cert.display_name}. Осталось нажать «Подписать документ»."
        )
        return True

    def _set_connection_state(self, state: str) -> None:
        self._connection_state = state
        if state == "connected" and self._client is not None:
            address = self._client.base_url.split("://", 1)[-1]
            suffix = " · вы в сессии" if self._joined else ""
            color = theme.SUCCESS
            text = f"Подключено: {html.escape(address)}{suffix}"
            self._conn_status.setToolTip(self._client.base_url)
        elif state == "lost":
            color = theme.ERROR
            text = "Нет связи с организатором — проверьте сеть"
            self._conn_status.setToolTip("")
        else:
            color = theme.OUTLINE
            text = "Не подключено"
            self._conn_status.setToolTip("")
        self._conn_status.setText(
            f'<span style="color:{color}; font-size:15px;">●</span> '
            f'<span style="color:{color};">{text}</span>'
        )

    def _current_cert(self):
        return self._certs.selected()

    def _on_cert_changed(self) -> None:
        cert = self._current_cert()
        usable = cert is not None and cert.sign_capable and cert.is_valid_now
        self._sign.setEnabled(
            self._joined and usable and not (self._signed or self._declined)
        )
        self._connect.setEnabled(cert is not None)
        self._connect.setToolTip(
            "Подключиться к сессии организатора"
            if cert is not None
            else "Сначала выберите сертификат — без него подключиться нельзя"
        )
        if self._connection_state == "idle":
            self._session_info.setText(
                "Введите адрес и код, которые сообщил организатор."
                if cert is not None
                else "Выберите сертификат ниже — после этого кнопка "
                "«Подключиться» станет доступной."
            )

    # ---------------------------------------------------------------- signing
    def _start_signing(self) -> None:
        cert = self._current_cert()
        if cert is None or self._client is None or not self._joined or self._worker is not None:
            return
        if not cert.sign_capable or not cert.is_valid_now:
            QMessageBox.warning(self, "Подпись", "Выбранный сертификат недоступен для подписи")
            return
        self._sign.setEnabled(False)
        self._sign.setText("Подписание…")
        self._worker = ClientSignWorker(self._client, cert.thumbprint, self._mode)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_signed)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, message: str) -> None:
        self._sign.setText(message)

    def _on_signed(self) -> None:
        self._worker = None
        self._signed = True
        self._sign.setText("Подписано ✓")
        self._sign.setEnabled(False)
        self._decline.setEnabled(False)
        QMessageBox.information(
            self, "Готово",
            "Документ подписан. Организатор увидит статус «Подписан», "
            "а после завершения сессии пришлёт итоговый файл.",
        )

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self._sign.setText("Подписать документ")
        self._on_cert_changed()
        QMessageBox.critical(self, "Подпись", message)

    def _decline_signing(self) -> None:
        if self._client is None or not self._joined:
            return
        if not _confirm(
            self,
            "Отказ от подписи",
            "Отказаться от подписания документа?\nОрганизатор увидит ваш отказ.",
        ):
            return
        try:
            self._client.decline("")
        except network.NetworkError as exc:
            QMessageBox.warning(self, "Сессия", str(exc))
            return
        self._declined = True
        self._sign.setEnabled(False)
        self._decline.setEnabled(False)
        self._sign.setText("Отказ от подписи отправлен")

    # ------------------------------------------------------------ package
    def _download_package(self) -> None:
        if self._client is None:
            return
        try:
            name, archive = self._client.package()
        except network.NetworkError as exc:
            QMessageBox.critical(self, "Архив", str(exc))
            return
        default = str(pathlib.Path.home() / name)
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить архив с подписями", default, "ZIP (*.zip)"
        )
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        try:
            pathlib.Path(path).write_bytes(archive)
        except OSError as exc:
            QMessageBox.critical(self, "Сохранение", f"Не удалось сохранить архив: {exc}")
            return
        QMessageBox.information(
            self, "Готово",
            f"Архив сохранён:\n{path}\n\n"
            "Внутри — исходный документ и файлы подписей .sig всех участников"
            + (", а также протокол подписания." if self._protocol else "."),
        )

    def _update_download_state(self, state: dict) -> None:
        signed = [
            p for p in state.get("participants", []) if p["status"] == network.STATUS_SIGNED
        ]
        if self._mode != network.MODE_PARALLEL:
            self._apply_download_style(False)
            self._download.setToolTip(
                "В последовательной схеме подписи встраиваются в PDF — "
                "итоговый файл сохраняет организатор"
            )
            return
        available = self._joined and bool(signed)
        self._apply_download_style(available)
        if not available:
            self._download.setToolTip("Пока никто не подписал — скачивать нечего")
            return
        included = "исходный документ и файлы .sig подписавших"
        if self._protocol:
            included += ", протокол подписания"
        self._download.setToolTip(
            f"В архиве {len(signed)} подпись(ей): {included}"
        )

    def _apply_download_style(self, available: bool) -> None:
        """Active archive button gets a visible outline; inactive is flat."""
        if self._download_available == available:
            return
        self._download_available = available
        self._download.setObjectName("Outline" if available else "Text")
        self._download.setEnabled(available)
        style = self._download.style()
        style.unpolish(self._download)
        style.polish(self._download)
        self._download.update()

    # ----------------------------------------------------------------- polling
    def _poll(self) -> None:
        if self._client is None:
            return
        try:
            state = self._client.state()
        except network.NetworkError:
            self._poll_failures += 1
            if self._poll_failures >= 2 and self._connection_state == "connected":
                self._set_connection_state("lost")
            return
        self._poll_failures = 0
        if self._joined and self._connection_state != "connected":
            self._set_connection_state("connected")
        _fill_status_table(self._table, state, highlight_id=self._client.participant_id)
        self._update_download_state(state)
        if self._joined and self._worker is None:
            for item in state.get("participants", []):
                if item["id"] != self._client.participant_id:
                    continue
                if item["status"] == network.STATUS_SIGNED:
                    self._signed = True
                    self._sign.setText("Подписано ✓")
                    self._sign.setEnabled(False)
                    self._decline.setEnabled(False)
                elif item["status"] == network.STATUS_DECLINED:
                    self._declined = True
                    self._sign.setEnabled(False)
                    self._decline.setEnabled(False)
                elif item["status"] == network.STATUS_ERROR:
                    self._sign.setText("Подписать документ")
                    self._on_cert_changed()
                break

    def reject(self) -> None:
        self._timer.stop()
        worker = self._worker
        self._worker = None
        if worker is not None and worker.isRunning():
            for signal in (worker.progress, worker.done, worker.failed):
                try:
                    signal.disconnect()
                except Exception:
                    pass
            worker.terminate()
            worker.wait(2000)
        super().reject()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_network_menu(window) -> None:
    """Called from the main window's «Сетевая подпись» button."""
    menu = RoleDialog(window)
    if menu.exec() != QDialog.DialogCode.Accepted:
        return
    if menu.choice == "host":
        _run_host(window)
    else:
        dialog = JoinSessionDialog(window)
        dialog.exec()


def _run_host(window) -> None:
    if window._pdf_data is None:
        QMessageBox.information(
            window, "Сетевая подпись",
            "Сначала выберите PDF, который будут подписывать участники.",
        )
        window._choose_pdf()
        if window._pdf_data is None:
            return
    doc_name = window._pdf_path.name if window._pdf_path else "document.pdf"
    setup = HostSetupDialog(doc_name, window._page_count or 1, window)
    if setup.exec() != QDialog.DialogCode.Accepted or setup.config is None:
        return
    config = setup.config
    config.doc_name = doc_name
    session = network.SignSession(window._pdf_data, config)
    try:
        server = network.SessionServer(session)
    except network.NetworkError as exc:
        QMessageBox.critical(window, "Сетевая сессия", str(exc))
        return
    server.start()
    dialog = HostSessionDialog(session, server, window)
    dialog.exec()
    # adopt the signed revision so the organizer can continue locally
    if session.config.mode == network.MODE_SEQUENTIAL and session.version > 1:
        final_doc = session.document_bytes()
        if final_doc != window._pdf_data:
            window._adopt_result(final_doc, source="сетевая сессия")


