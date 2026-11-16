"""UI for LAN signing sessions: host (organizer) and participant dialogs."""
from __future__ import annotations

import pathlib
import time

from PySide6.QtCore import Qt, QThread, QTimer, Signal
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
    QTextEdit,
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
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
    table.setMinimumHeight(170)
    return table


def _fill_status_table(table: QTreeWidget, snapshot: dict, highlight_id: str = "") -> None:
    table.clear()
    for item in snapshot.get("participants", []):
        row = QTreeWidgetItem([
            f'{item["order"]}. {item["name"]}',
            item["organization"] or "—",
            item["status_label"] + (f' — {item["detail"]}' if item["detail"] else ""),
            item["signed_at"] or item["joined_at"] or "",
        ])
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
        card_layout.addWidget(self._parallel)
        par_hint = QLabel(
            "Каждый участник независимо подписывает исходный файл. Итог — "
            "исходный PDF и отдельные файлы .sig для каждого подписанта."
        )
        par_hint.setObjectName("Caption")
        par_hint.setWordWrap(True)
        card_layout.addWidget(par_hint)

        self._sequential = QRadioButton("Последовательная подпись")
        self._sequential.setChecked(True)
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

    def _start(self) -> None:
        self.config = network.SessionConfig(
            mode=(network.MODE_SEQUENTIAL if self._sequential.isChecked()
                  else network.MODE_PARALLEL),
            strict_order=self._strict.isChecked() and self._sequential.isChecked(),
            visible=self._visible.isChecked() and self._sequential.isChecked(),
            page_index=self._page_combo.currentData(),
        )
        self.accept()


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
        self.resize(760, 640)
        self.setStyleSheet(theme.stylesheet())
        self._saved = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        info_card, info_layout = _card("Подключение участников")
        _shadow(info_card)
        snap = session.snapshot()
        doc_line = QLabel(f"Документ: {snap['doc_name']} · {snap['mode_label']}")
        doc_line.setObjectName("Caption")
        info_layout.addWidget(doc_line)

        code_row = QHBoxLayout()
        code_caption = QLabel("Код сессии")
        code_caption.setObjectName("Caption")
        self._code_label = QLabel(session.code)
        self._code_label.setStyleSheet(
            f"font-size: 26px; font-weight: 700; letter-spacing: 6px; color: {theme.PRIMARY};"
        )
        code_row.addWidget(code_caption)
        code_row.addWidget(self._code_label)
        code_row.addStretch(1)
        info_layout.addLayout(code_row)

        self._links = QLabel()
        self._links.setObjectName("Caption")
        self._links.setWordWrap(True)
        self._links.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_layout.addWidget(self._links)

        copy = QPushButton("Скопировать приглашение")
        copy.setObjectName("Tonal")
        copy.clicked.connect(self._copy_invite)
        info_layout.addWidget(copy, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(info_card)

        status_card, status_layout = _card("Статусы участников")
        _shadow(status_card)
        self._summary = QLabel()
        self._summary.setObjectName("Caption")
        status_layout.addWidget(self._summary)
        self._table = _status_table()
        status_layout.addWidget(self._table)
        self._hint = QLabel(
            "Страница статусов также открывается в браузере по адресу сессии — "
            "удобно показывать на общем экране."
        )
        self._hint.setObjectName("Caption")
        self._hint.setWordWrap(True)
        status_layout.addWidget(self._hint)
        layout.addWidget(status_card, 1)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFixedHeight(90)
        self._log.setObjectName("Caption")
        self._log.setFrameShape(QTextEdit.Shape.NoFrame)
        self._log.setStyleSheet(
            f"background: {theme.SURFACE_CONTAINER}; border: none; border-radius: 10px;"
            f" padding: 8px; color: {theme.ON_SURFACE_VARIANT};"
        )
        layout.addWidget(self._log)

        buttons = QHBoxLayout()
        self._abort = QPushButton("Прервать сессию")
        self._abort.setObjectName("Text")
        self._abort.setStyleSheet(f"QPushButton {{ color: {theme.ERROR}; }}")
        self._abort.clicked.connect(self._abort_session)
        buttons.addWidget(self._abort)
        buttons.addStretch(1)
        self._finish = QPushButton("Завершить и сохранить итог")
        self._finish.setObjectName("Filled")
        self._finish.clicked.connect(self._finish_session)
        buttons.addWidget(self._finish)
        layout.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(800)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._tick()

    # ------------------------------------------------------------------ logic
    def _tick(self) -> None:
        snap = self._session.snapshot()
        _fill_status_table(self._table, snap)
        links = self._server.addresses()
        self._links.setText("Адреса для подключения: " + " · ".join(links))
        signed = sum(1 for p in snap["participants"] if p["status"] == network.STATUS_SIGNED)
        declined = sum(1 for p in snap["participants"] if p["status"] == network.STATUS_DECLINED)
        signing = sum(1 for p in snap["participants"] if p["status"] == network.STATUS_SIGNING)
        self._summary.setText(
            f"Участников: {len(snap['participants'])} · подписало: {signed} · "
            f"отказалось: {declined} · подписывает: {signing} · "
            f"версия документа: {snap['doc_version']}"
        )

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
        self._log.append("Приглашение скопировано в буфер обмена")

    def _abort_session(self) -> None:
        answer = QMessageBox.question(
            self, "Прервать сессию",
            "Участники потеряют доступ к сессии. Итоговый файл не будет сохранён.\n"
            "Прервать?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._session.finalize("Сессия прервана организатором")
        self.reject()

    def _finish_session(self) -> None:
        snap = self._session.snapshot()
        signed = sum(1 for p in snap["participants"] if p["status"] == network.STATUS_SIGNED)
        if signed == 0:
            QMessageBox.warning(self, "Сессия", "Ни один участник ещё не подписал документ.")
            return
        document, sig_files, note = self._session.results()
        stem = pathlib.Path(snap["doc_name"]).stem or "document"
        if sig_files:
            directory = QFileDialog.getExistingDirectory(
                self, "Выберите папку для документа и подписей"
            )
            if not directory:
                return
            target = pathlib.Path(directory)
            try:
                (target / snap["doc_name"]).write_bytes(document)
                for name, content in sig_files:
                    (target / name).write_bytes(content)
            except OSError as exc:
                QMessageBox.critical(self, "Сохранение", f"Не удалось сохранить файлы: {exc}")
                return
            QMessageBox.information(
                self, "Готово",
                f"Сохранено: {snap['doc_name']} и {len(sig_files)} файл(а) подписей .sig.\n"
                "Файлы .sig проверяются штатно через CryptoPro (cryptcp -vsig).",
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
        self.resize(720, 700)
        self.setStyleSheet(theme.stylesheet())
        self._client: network.SessionClient | None = None
        self._worker: ClientSignWorker | None = None
        self._rows: list = []
        self._joined = False
        self._mode = network.MODE_SEQUENTIAL

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        conn_card, conn_layout = _card("Адрес сессии")
        _shadow(conn_card)
        row = QHBoxLayout()
        addr_caption = QLabel("Адрес")
        addr_caption.setObjectName("Caption")
        self._address = QLineEdit()
        self._address.setPlaceholderText("например 192.168.1.50 или 192.168.1.50:8443")
        port_caption = QLabel("Код")
        port_caption.setObjectName("Caption")
        self._code = QLineEdit()
        self._code.setMaxLength(8)
        self._code.setFixedWidth(110)
        self._code.setPlaceholderText("6 знаков")
        self._connect = QPushButton("Подключиться")
        self._connect.setObjectName("Filled")
        self._connect.clicked.connect(self._connect_session)
        row.addWidget(addr_caption)
        row.addWidget(self._address, 1)
        row.addWidget(port_caption)
        row.addWidget(self._code)
        row.addWidget(self._connect)
        conn_layout.addLayout(row)
        self._session_info = QLabel("Введите адрес и код, которые сообщил организатор.")
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
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(140)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        holder = QWidget()
        self._cert_box = QVBoxLayout(holder)
        self._cert_box.setContentsMargins(0, 0, 0, 0)
        self._cert_box.setSpacing(6)
        self._cert_box.addStretch(1)
        scroll.setWidget(holder)
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
        self._load_certificates()

    # ------------------------------------------------------------ connection
    def _connect_session(self) -> None:
        if not self._client:
            try:
                self._client = network.SessionClient(self._address.text(), self._code.text())
            except network.NetworkError as exc:
                QMessageBox.warning(self, "Подключение", str(exc))
                return
        try:
            info = self._client.info()
        except network.NetworkError as exc:
            QMessageBox.warning(self, "Подключение", str(exc))
            self._client = None
            return
        if info.get("finished"):
            QMessageBox.information(self, "Сессия", "Эта сессия уже завершена.")
            self._client = None
            return
        self._mode = str(info.get("mode") or network.MODE_SEQUENTIAL)
        self._session_info.setText(
            f"Документ: {info['doc_name']} · {info['mode_label']}"
            + (" · строгая очередь" if info.get("strict_order") else "")
        )
        self._connect.setText("Переподключиться")
        self._try_join()
        self._timer.start()

    def _try_join(self) -> None:
        cert = self._current_cert()
        if cert is None or self._joined or self._client is None:
            return
        try:
            self._client.join(cert)
        except network.NetworkError as exc:
            QMessageBox.warning(self, "Сессия", str(exc))
            self._client = None
            self._connect.setText("Подключиться")
            return
        self._joined = True
        self._decline.setEnabled(True)
        self._sign.setEnabled(cert.sign_capable and cert.is_valid_now)
        self._session_info.setText(
            self._session_info.text()
            + f"\nВы в сессии как {cert.display_name}. Осталось нажать «Подписать документ»."
        )

    def _current_cert(self):
        from .window import CertificateRow

        for row in self._rows:
            if isinstance(row, CertificateRow) and row.is_checked:
                return row.info
        return None

    # ------------------------------------------------------------ certificates
    def _load_certificates(self) -> None:
        from .window import CertificateRow

        while self._cert_box.count() > 1:
            item = self._cert_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._rows.clear()

        available, detail = crypto.crypto_status()
        if not available:
            label = QLabel(detail)
            label.setObjectName("Error")
            label.setWordWrap(True)
            self._cert_box.insertWidget(0, label)
            self._rows.append(label)
            return
        try:
            certificates = crypto.list_certificates()
        except crypto.CryptoError as exc:
            label = QLabel(str(exc))
            label.setObjectName("Error")
            label.setWordWrap(True)
            self._cert_box.insertWidget(0, label)
            self._rows.append(label)
            return
        if not certificates:
            label = QLabel(service.store_label() + " не содержит сертификатов")
            label.setObjectName("Caption")
            self._cert_box.insertWidget(0, label)
            self._rows.append(label)
            return
        for info in certificates:
            row = CertificateRow(info)

            def _on_select(selected_row, _rows=self._rows, _row=row):
                for other in _rows:
                    if isinstance(other, CertificateRow) and other is not selected_row:
                        other.set_checked(False)
                self._on_cert_changed()

            row._selector = _on_select
            self._cert_box.insertWidget(self._cert_box.count() - 1, row)
            self._rows.append(row)

    def _on_cert_changed(self) -> None:
        cert = self._current_cert()
        self._sign.setEnabled(
            self._joined and cert is not None and cert.sign_capable and cert.is_valid_now
        )

    # ---------------------------------------------------------------- signing
    def _start_signing(self) -> None:
        cert = self._current_cert()
        if cert is None or self._client is None or self._worker is not None:
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
        self._sign.setEnabled(True)
        QMessageBox.critical(self, "Подпись", message)

    def _decline_signing(self) -> None:
        if self._client is None or not self._joined:
            return
        answer = QMessageBox.question(
            self, "Отказ от подписи", "Отказаться от подписания документа?\nОрганизатор увидит ваш отказ."
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._client.decline("")
        except network.NetworkError as exc:
            QMessageBox.warning(self, "Сессия", str(exc))
            return
        self._sign.setEnabled(False)
        self._decline.setEnabled(False)
        self._sign.setText("Отказ от подписи отправлен")

    # ----------------------------------------------------------------- polling
    def _poll(self) -> None:
        if self._client is None:
            return
        try:
            state = self._client.state()
        except network.NetworkError:
            return
        _fill_status_table(self._table, state, highlight_id=self._client.participant_id)
        if self._joined and self._worker is None:
            for item in state.get("participants", []):
                if item["id"] == self._client.participant_id:
                    if item["status"] == network.STATUS_ERROR:
                        self._sign.setText("Подписать документ")
                        self._sign.setEnabled(True)
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
