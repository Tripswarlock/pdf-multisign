"""Material Design main window."""
from __future__ import annotations

import datetime as dt
import pathlib

from PySide6.QtCore import QEvent, QSize, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QPixmap, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import about, crypto, service, theme


CARD_STYLE = theme.stylesheet()


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


class CertificateRow(QFrame):
    """Selectable certificate card with a Material-style checkbox indicator."""

    def __init__(self, info: crypto.CertificateInfo, parent=None):
        super().__init__(parent)
        self.info = info
        self._checked = False
        self.setObjectName("CertRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(74)
        self.setMaximumHeight(86)
        self.setStyleSheet(self._style(False))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        self._mark = QLabel("")
        self._mark.setFixedSize(22, 22)
        self._mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mark.setStyleSheet(
            f"border: 2px solid {theme.OUTLINE}; border-radius: 4px; background: transparent;"
        )
        layout.addWidget(self._mark, 0, Qt.AlignmentFlag.AlignTop)

        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        name = QLabel(info.display_name or info.thumbprint[:20])
        name.setStyleSheet("font-weight: 600; font-size: 13px; background: transparent;")
        detail_text = info.organization or info.detail_text or info.issuer
        if len(detail_text) > 46:
            detail_text = detail_text[:45].rstrip() + "…"
        detail = QLabel(detail_text)
        detail.setObjectName("Caption")
        detail.setStyleSheet("background: transparent;")
        detail.setWordWrap(False)
        if not info.sign_capable:
            caption = info.signing_note or "Подпись недоступна"
            validity = QLabel(caption)
            validity.setStyleSheet(
                f"color: {theme.ERROR}; background: transparent; font-size: 11px;"
            )
        elif not info.is_valid_now and info.not_after:
            validity = QLabel(f"срок истёк {info.not_after:%d.%m.%Y}")
            validity.setStyleSheet(
                f"color: {theme.ERROR}; background: transparent; font-size: 11px;"
            )
        else:
            validity = QLabel(f"действителен до {info.not_after:%d.%m.%Y}")
            validity.setObjectName("Caption")
            validity.setStyleSheet("background: transparent;")
        text_box.addWidget(name)
        text_box.addWidget(detail)
        text_box.addWidget(validity)
        layout.addLayout(text_box, 1)

        if not info.sign_capable or not info.is_valid_now:
            self.setEnabled(False)
            if not info.sign_capable:
                note = info.signing_note or "Подпись недоступна"
            elif info.not_after:
                note = f"Срок действия истёк {info.not_after:%d.%m.%Y} — подпись не будет создана"
            else:
                note = "Подпись недоступна"
            tooltip = f"{note}\n\n{info.subject}" if info.subject else note
            self.setToolTip(tooltip)
        else:
            self.setToolTip(info.subject)

    @staticmethod
    def _style(checked: bool) -> str:
        background = theme.PRIMARY_CONTAINER if checked else "transparent"
        border = theme.PRIMARY if checked else "transparent"
        return f"""
        QFrame#CertRow {{
            background: {background};
            border: 1px solid {border};
            border-radius: 12px;
        }}
        QFrame#CertRow:hover {{ background: {theme.SURFACE_CONTAINER_HIGH}; }}
        """

    @property
    def is_checked(self) -> bool:
        return self._checked

    def set_checked(self, checked: bool) -> None:
        if self._checked == checked:
            return
        self._checked = checked
        self.setStyleSheet(self._style(checked))
        if checked:
            self._mark.setText("✓")
            self._mark.setStyleSheet(
                f"background: {theme.PRIMARY}; color: white; border: 2px solid {theme.PRIMARY};"
                " border-radius: 4px; font-size: 13px; font-weight: 700;"
            )
        else:
            self._mark.setText("")
            self._mark.setStyleSheet(
                f"border: 2px solid {theme.OUTLINE}; border-radius: 4px; background: transparent;"
            )
        if self._selector is not None:
            self._selector(self)

    _selector = None

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.set_checked(not self._checked)
            event.accept()
            return
        super().mousePressEvent(event)


class SignWorker(QThread):
    progress = Signal(int, int, str, str)
    finished_ok = Signal(bytes, list)
    failed = Signal(str)

    def __init__(self, data: bytes, certificates, options: service.SignOptions):
        super().__init__()
        self._data = data
        self._certificates = certificates
        self._options = options

    def run(self) -> None:
        def on_progress(event: service.ProgressEvent) -> None:
            self.progress.emit(event.index, event.total, event.title, event.detail)

        try:
            outcome = service.sign_document(
                self._data, self._certificates, self._options, on_progress
            )
        except Exception as exc:  # pragma: no cover - defensive
            self.failed.emit(f"Непредвиденная ошибка: {exc}")
            return
        if outcome.error:
            self.failed.emit(outcome.error)
            return
        self.finished_ok.emit(outcome.output, outcome.reports)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PDF Мультиподпись")
        self.resize(1200, 860)
        self.setMinimumSize(1000, 700)
        self.setStyleSheet(CARD_STYLE)

        self._pdf_path: pathlib.Path | None = None
        self._pdf_data: bytes | None = None
        self._page_count = 0
        self._preview_page = 0
        self._worker: SignWorker | None = None
        self._output: bytes | None = None
        self._rows: list[CertificateRow] = []
        self._certificates: list[crypto.CertificateInfo] = []

        self._build_ui()
        self._load_certificates()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(18)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("PDF Мультиподпись")
        title.setObjectName("AppTitle")
        subtitle = QLabel("Несколько подписей CryptoPro в одном PDF за один проход")
        subtitle.setObjectName("AppSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch(1)
        self._crypto_badge = QLabel()
        self._crypto_badge.setObjectName("Caption")
        header.addWidget(self._crypto_badge)
        self._network_button = QPushButton("Сетевая подпись")
        self._network_button.setObjectName("Tonal")
        self._network_button.setToolTip(
            "Несколько компьютеров в локальной сети подписывают один документ "
            "с отображением статусов"
        )
        self._network_button.clicked.connect(self._open_network)
        header.addWidget(self._network_button)
        self._about_button = QPushButton("О программе")
        self._about_button.setObjectName("Text")
        self._about_button.setToolTip("Автор, компоненты и приватность")
        self._about_button.clicked.connect(self._show_about)
        header.addWidget(self._about_button)
        root.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(18)
        root.addLayout(body, 1)

        body.addWidget(self._build_left(), 3)
        body.addWidget(self._build_right(), 2)

        self._status = QLabel("Добавьте PDF, чтобы начать")
        self._status.setObjectName("Caption")
        root.addWidget(self._status)

    def _build_left(self) -> QWidget:
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        doc_card, doc_layout = _card("Документ")
        _shadow(doc_card)

        self._drop = QFrame()
        self._drop.setObjectName("DropZone")
        self._drop.setAcceptDrops(True)
        self._drop.setMinimumHeight(96)
        drop_layout = QVBoxLayout(self._drop)
        drop_layout.setContentsMargins(16, 14, 16, 14)
        drop_layout.setSpacing(4)
        self._drop_title = QLabel("Перетащите PDF сюда")
        self._drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drop_title.setStyleSheet("font-weight: 600; background: transparent;")
        self._drop_hint = QLabel("или нажмите сюда, чтобы выбрать файл")
        self._drop_hint.setObjectName("Caption")
        self._drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drop_hint.setStyleSheet("background: transparent;")
        self._drop.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drop.installEventFilter(self)
        drop_layout.addStretch(1)
        drop_layout.addWidget(self._drop_title)
        drop_layout.addWidget(self._drop_hint)
        drop_layout.addStretch(1)
        doc_layout.addWidget(self._drop)

        self._file_label = QLabel("Файл не выбран")
        self._file_label.setObjectName("Caption")
        self._file_label.setWordWrap(True)
        doc_layout.addWidget(self._file_label)

        buttons = QHBoxLayout()
        self._choose = QPushButton("Выбрать PDF")
        self._choose.setObjectName("Tonal")
        self._choose.clicked.connect(self._choose_pdf)
        self._clear = QPushButton("Убрать")
        self._clear.setObjectName("Text")
        self._clear.clicked.connect(self._clear_pdf)
        buttons.addWidget(self._choose)
        buttons.addWidget(self._clear)
        buttons.addStretch(1)
        doc_layout.addLayout(buttons)

        preview_frame = QFrame()
        preview_frame.setObjectName("PreviewFrame")
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(10, 10, 10, 10)
        preview_layout.setSpacing(8)

        self._preview = QLabel("Предпросмотр появится после загрузки PDF")
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumHeight(300)
        self._preview.setStyleSheet("background: transparent; color: #79747E;")
        preview_layout.addWidget(self._preview, 1)

        nav = QHBoxLayout()
        self._prev_page = QPushButton("‹")
        self._prev_page.setObjectName("Text")
        self._prev_page.setFixedWidth(38)
        self._prev_page.setStyleSheet("background: transparent; border: none; font-size: 18px;")
        self._prev_page.clicked.connect(lambda: self._show_page(self._preview_page - 1))
        self._next_page = QPushButton("›")
        self._next_page.setObjectName("Text")
        self._next_page.setFixedWidth(38)
        self._next_page.setStyleSheet("background: transparent; border: none; font-size: 18px;")
        self._next_page.clicked.connect(lambda: self._show_page(self._preview_page + 1))
        self._page_label = QLabel("—")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_label.setObjectName("Caption")
        self._page_label.setStyleSheet("background: transparent;")
        nav.addWidget(self._prev_page)
        nav.addWidget(self._page_label, 1)
        nav.addWidget(self._next_page)
        preview_layout.addLayout(nav)
        doc_layout.addWidget(preview_frame, 1)
        layout.addWidget(doc_card, 1)
        return column

    def _build_right(self) -> QWidget:
        column = QWidget()
        column.setMinimumWidth(360)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        cert_card, cert_layout = _card("Подписанты")
        _shadow(cert_card)
        hint = QLabel("Отметьте один или несколько сертификатов — подписи добавятся по порядку")
        hint.setObjectName("Caption")
        hint.setWordWrap(True)
        cert_layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(150)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        holder = QWidget()
        self._cert_box = QVBoxLayout(holder)
        self._cert_box.setContentsMargins(0, 0, 0, 0)
        self._cert_box.setSpacing(6)
        self._cert_box.addStretch(1)
        scroll.setWidget(holder)
        cert_layout.addWidget(scroll, 1)

        self._reload = QPushButton("Обновить список")
        self._reload.setObjectName("Text")
        self._reload.clicked.connect(self._load_certificates)
        cert_layout.addWidget(self._reload, 0, Qt.AlignmentFlag.AlignLeft)

        options_card, opt_layout = _card("Параметры подписи")
        _shadow(options_card)
        self._visible = QCheckBox("Видимый блок подписи на странице")
        self._visible.setChecked(False)
        opt_layout.addWidget(self._visible)

        page_row = QHBoxLayout()
        page_caption = QLabel("Страница")
        page_caption.setObjectName("Caption")
        self._page_combo = QComboBox()
        self._page_combo.addItem("Последняя", -1)
        page_row.addWidget(page_caption)
        page_row.addWidget(self._page_combo, 1)
        opt_layout.addLayout(page_row)

        reason_row = QHBoxLayout()
        reason_caption = QLabel("Причина")
        reason_caption.setObjectName("Caption")
        self._reason = QLineEdit("Подписание документа")
        reason_row.addWidget(reason_caption)
        reason_row.addWidget(self._reason, 1)
        opt_layout.addLayout(reason_row)

        self._sign_button = QPushButton("Подписать")
        self._sign_button.setObjectName("Filled")
        self._sign_button.setEnabled(False)
        self._sign_button.clicked.connect(self._start_signing)
        opt_layout.addWidget(self._sign_button)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        opt_layout.addWidget(self._progress)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFixedHeight(74)
        self._log.setObjectName("Caption")
        self._log.setFrameShape(QFrame.Shape.NoFrame)
        self._log.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._log.setStyleSheet(
            f"background: {theme.SURFACE_CONTAINER}; border: none; border-radius: 10px;"
            f" padding: 8px; color: {theme.ON_SURFACE_VARIANT};"
        )
        self._log.setVisible(False)
        opt_layout.addWidget(self._log)

        self._save_button = QPushButton("Сохранить PDF")
        self._save_button.setObjectName("Tonal")
        self._save_button.setEnabled(False)
        self._save_button.clicked.connect(self._save_result)
        opt_layout.addWidget(self._save_button)

        layout.addWidget(cert_card, 1)
        layout.addWidget(options_card, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(column)
        return scroll

    # ------------------------------------------------------------- helpers
    def _load_certificates(self) -> None:
        for row in self._rows:
            row.deleteLater()
        self._rows.clear()

        available, detail = crypto.crypto_status()
        self._crypto_badge.setText(("CryptoPro доступен" if available else "CryptoPro недоступен"))
        self._crypto_badge.setStyleSheet(
            f"color: {theme.SUCCESS if available else theme.ERROR}; font-weight: 600;"
        )
        self._crypto_badge.setToolTip(detail)

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

        if not self._certificates:
            label = QLabel(service.store_label() + " не содержит сертификатов\n" + crypto.store_hint())
            label.setObjectName("Caption")
            label.setWordWrap(True)
            self._cert_box.insertWidget(0, label)
            return

        for info in self._certificates:
            row = CertificateRow(info)
            row._selector = lambda _row: self._refresh_state()
            self._cert_box.insertWidget(self._cert_box.count() - 1, row)
            self._rows.append(row)

    def _selected(self) -> list[crypto.CertificateInfo]:
        return [
            row.info
            for row in self._rows
            if isinstance(row, CertificateRow)
            and row.is_checked
            and row.info.sign_capable
            and row.info.is_valid_now
        ]

    def _refresh_state(self) -> None:
        selected = self._selected()
        ready = self._pdf_data is not None and bool(selected)
        self._sign_button.setEnabled(ready)
        if not selected:
            self._status.setText("Выберите хотя бы один сертификат")
        elif self._pdf_data is None:
            self._status.setText("Добавьте PDF")
        else:
            self._status.setText(f"Готово к подписи: {len(selected)} серт. · {self._page_count} стр.")

    def _choose_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Выберите PDF", "", "PDF (*.pdf)")
        if path:
            self._load_pdf(pathlib.Path(path))

    def _load_pdf(self, path: pathlib.Path) -> None:
        data = path.read_bytes()
        if not data.startswith(b"%PDF-"):
            QMessageBox.warning(self, "PDF", "Выбранный файл не является PDF")
            return
        if b"/Encrypt" in data[:4096] or b"/Encrypt" in data[-4096:]:
            QMessageBox.warning(
                self, "PDF", "PDF защищён паролем или шифрованием — подпись недоступна"
            )
            return
        self._pdf_path = path
        self._pdf_data = data
        try:
            self._page_count = service.page_count(data)
        except Exception as exc:
            QMessageBox.warning(self, "PDF", f"Не удалось прочитать PDF: {exc}")
            self._clear_pdf()
            return
        self._file_label.setText(f"{path.name} · {self._page_count} стр.")
        self._preview_page = self._page_count - 1
        self._page_combo.blockSignals(True)
        self._page_combo.clear()
        self._page_combo.addItem("Последняя", -1)
        for index in range(self._page_count):
            self._page_combo.addItem(f"Страница {index + 1}", index)
        self._page_combo.addItem("Все страницы", service.ALL_PAGES)
        self._page_combo.blockSignals(False)
        self._show_page(self._preview_page)
        self._log_clear()
        self._log_append("Документ загружен")
        existing = service.describe_signatures(data)
        if existing:
            self._log_append(f"Уже подписей в файле: {len(existing)}")
        self._save_button.setEnabled(False)
        self._output = None
        self._refresh_state()

    def _log_append(self, text: str) -> None:
        if not text:
            return
        if not self._log.isVisible():
            self._log.setVisible(True)
        self._log.append(text)

    def _log_clear(self) -> None:
        self._log.clear()
        self._log.setVisible(False)

    def _clear_pdf(self) -> None:
        self._pdf_path = None
        self._pdf_data = None
        self._page_count = 0
        self._output = None
        self._file_label.setText("Файл не выбран")
        self._preview.setPixmap(QPixmap())
        self._preview.setText("Предпросмотр появится после загрузки PDF")
        self._page_label.setText("—")
        self._save_button.setEnabled(False)
        self._refresh_state()

    def _show_page(self, index: int) -> None:
        if self._pdf_data is None:
            return
        index = max(0, min(index, self._page_count - 1))
        self._preview_page = index
        self._page_label.setText(f"{index + 1} / {self._page_count}")
        self._prev_page.setEnabled(index > 0)
        self._next_page.setEnabled(index < self._page_count - 1)
        try:
            import pymupdf

            doc = pymupdf.open(stream=self._pdf_data, filetype="pdf")
            page = doc[index]
            pix = page.get_pixmap(dpi=110)
            image = QImage(
                pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
            ).copy()
            doc.close()
            self._preview.setPixmap(
                QPixmap.fromImage(image).scaled(
                    self._preview.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        except Exception as exc:
            self._preview.setText(f"Не удалось отрисовать страницу: {exc}")

    def _start_signing(self) -> None:
        if self._pdf_data is None:
            return
        selected = self._selected()
        if not selected:
            return
        self._sign_button.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._log_clear()
        options = service.SignOptions(
            visible=self._visible.isChecked(),
            page_index=self._page_combo.currentData(),
            reason=self._reason.text().strip() or "Подписание документа",
        )
        self._worker = SignWorker(self._pdf_data, selected, options)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_signed)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, index: int, total: int, title: str, detail: str) -> None:
        percent = int(((index + (1 if "добавлена" in detail else 0)) / max(1, total)) * 100)
        self._progress.setValue(min(100, percent))
        self._status.setText(title)
        if detail:
            self._log_append(detail)

    def _on_signed(self, data: bytes, reports: list) -> None:
        self._output = data
        # Adopt the signed revision so a second run appends another signature
        # instead of re-signing the original bytes.
        self._pdf_data = data
        self._progress.setValue(100)
        self._progress.setVisible(False)
        self._log_append(f"Готово: подписей добавлено {len(reports)}")
        for report in reports:
            self._log_append(f"• {report.field_name} — {report.signer} ({report.cms_size} байт CMS)")
        total = len(service.describe_signatures(data))
        self._status.setText(
            f"Подписей в документе: {total}. Можно добавить ещё или сохранить."
        )
        self._save_button.setEnabled(True)
        self._sign_button.setEnabled(True)
        self._show_page(self._preview_page)

    def _on_failed(self, message: str) -> None:
        self._progress.setVisible(False)
        self._log_append(f"Ошибка: {message}")
        self._status.setText("Не удалось подписать")
        self._sign_button.setEnabled(True)
        QMessageBox.critical(self, "Подпись", message)

    def _save_result(self) -> None:
        if self._output is None:
            return
        default = ""
        if self._pdf_path:
            default = str(self._pdf_path.with_name(self._pdf_path.stem + "-signed.pdf"))
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить PDF", default, "PDF (*.pdf)")
        if not path:
            return
        try:
            pathlib.Path(path).write_bytes(self._output)
        except OSError as exc:
            QMessageBox.critical(self, "Сохранение", f"Не удалось сохранить файл: {exc}")
            return
        self._status.setText(f"Сохранено: {pathlib.Path(path).name}")
        self._log_append(f"Файл сохранён: {path}")
        QMessageBox.information(
            self,
            "Готово",
            "PDF сохранён с подписями.\n\nПроверить подписи можно в Adobe Reader "
            "или штатными средствами CryptoPro.",
        )

    # ------------------------------------------------------------- network
    def _open_network(self) -> None:
        from .network_ui import run_network_menu

        run_network_menu(self)

    def _adopt_result(self, data: bytes, source: str = "подписание") -> None:
        """Adopt a new revision produced outside the local signing flow."""
        self._pdf_data = data
        self._output = data
        self._save_button.setEnabled(True)
        self._log_append(f"Документ обновлён ({source})")
        total = len(service.describe_signatures(data))
        self._status.setText(f"Подписей в документе: {total}. Можно сохранить или подписать ещё.")
        self._show_page(self._preview_page)

    # ------------------------------------------------------------- events
    def _show_about(self) -> None:
        about.show_about(self)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._drop and event.type() == QEvent.Type.MouseButtonRelease:
            self._choose_pdf()
            return True
        return super().eventFilter(obj, event)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(".pdf"):
                    event.acceptProposedAction()
                    self._drop.setProperty("dragActive", True)
                    self._drop.style().unpolish(self._drop)
                    self._drop.style().polish(self._drop)
                    return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._drop.setProperty("dragActive", False)
        self._drop.style().unpolish(self._drop)
        self._drop.style().polish(self._drop)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._drop.setProperty("dragActive", False)
        self._drop.style().unpolish(self._drop)
        self._drop.style().polish(self._drop)
        for url in event.mimeData().urls():
            candidate = pathlib.Path(url.toLocalFile())
            if candidate.suffix.lower() == ".pdf":
                self._load_pdf(candidate)
                event.acceptProposedAction()
                return
        event.ignore()
