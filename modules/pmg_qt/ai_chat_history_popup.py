from __future__ import annotations

from datetime import datetime
import re
from typing import Callable, Dict, List

from pymol.Qt import QtCore, QtWidgets

Qt = QtCore.Qt


_TITLE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s*-\s*(.+)$")


def _display_title(raw_title: str) -> str:
    text = str(raw_title or "").strip()
    if not text:
        return "Untitled Chat"
    match = _TITLE_PREFIX_RE.match(text)
    if match:
        trimmed = match.group(1).strip()
        if trimmed:
            return trimmed
    return text


def _format_local_time(raw_time: str) -> str:
    raw = str(raw_time or "").strip()
    if not raw:
        return "Unknown time"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%b %d, %Y %I:%M %p").replace(" 0", " ")
    except Exception:
        return raw


class ChatHistoryPopup(QtWidgets.QFrame):
    chatSelected = QtCore.Signal(str)
    managerRequested = QtCore.Signal()

    PAGE_SIZE = 30

    def __init__(self, list_callback: Callable[[str, int, int], List[Dict]], parent=None):
        super().__init__(parent, Qt.Popup)
        self._list_callback = list_callback
        self._query = ""
        self._offset = 0
        self._has_more = True

        self.setObjectName("chatHistoryPopup")
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setMinimumWidth(420)
        self.setMinimumHeight(420)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self.search = QtWidgets.QLineEdit(self)
        self.search.setPlaceholderText("Search chats...")
        root.addWidget(self.search)

        self.list_widget = QtWidgets.QListWidget(self)
        self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.list_widget.setSpacing(4)
        root.addWidget(self.list_widget, 1)

        footer = QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        self.refresh_btn = QtWidgets.QPushButton("Refresh", self)
        self.manager_btn = QtWidgets.QPushButton("Open History Manager...", self)
        footer.addWidget(self.refresh_btn)
        footer.addWidget(self.manager_btn)
        root.addLayout(footer)

        self._search_timer = QtCore.QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(self.reload)

        self.search.textChanged.connect(self._on_search_text_changed)
        self.refresh_btn.clicked.connect(self.reload)
        self.manager_btn.clicked.connect(self.managerRequested.emit)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.verticalScrollBar().valueChanged.connect(self._on_scroll)

        self.setStyleSheet(
            """
            QFrame#chatHistoryPopup {
                background: #2c2c2c;
                border: 1px solid #5d5d5d;
                border-radius: 8px;
            }
            QListWidget {
                background: #262626;
                color: #e6e6e6;
                border: 1px solid #4f4f4f;
                border-radius: 6px;
            }
            QListWidget::item {
                border: none;
                margin: 0px;
            }
            QListWidget::item:selected {
                background: #3b3b3b;
            }
            QLineEdit {
                background: #212121;
                color: #e6e6e6;
                border: 1px solid #4f4f4f;
                border-radius: 6px;
                padding: 5px 7px;
            }
            QWidget#historyRow {
                background: #313131;
                border: 1px solid #595959;
                border-radius: 6px;
            }
            QWidget#historyRow:hover {
                background: #373737;
            }
            """
        )

    def _build_row_widget(self, row: Dict) -> QtWidgets.QWidget:
        title = _display_title(str(row.get("title") or ""))
        updated = _format_local_time(str(row.get("updated_at") or ""))

        host = QtWidgets.QWidget(self.list_widget)
        host.setObjectName("historyRow")
        layout = QtWidgets.QVBoxLayout(host)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        title_label = QtWidgets.QLabel(host)
        title_label.setText(title)
        title_label.setStyleSheet("color:#ebebeb;font-size:16px;font-weight:700;border:none;background:transparent;")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        meta = QtWidgets.QLabel("Updated %s" % (updated,), host)
        meta.setStyleSheet("color:#b0b0b0;font-size:12px;border:none;background:transparent;")
        layout.addWidget(meta)

        return host

    def _on_search_text_changed(self, _text: str):
        self._search_timer.start()

    def _on_item_clicked(self, item: QtWidgets.QListWidgetItem):
        chat_id = str(item.data(Qt.UserRole) or "")
        if chat_id:
            self.chatSelected.emit(chat_id)
            self.hide()

    def _on_scroll(self, value: int):
        if not self._has_more:
            return
        bar = self.list_widget.verticalScrollBar()
        if value >= (bar.maximum() - 20):
            self._load_more()

    def reload(self):
        self._query = self.search.text().strip()
        self._offset = 0
        self._has_more = True
        self.list_widget.clear()
        self._load_more()

    def _load_more(self):
        if not self._has_more:
            return
        rows = self._list_callback(self._query, self._offset, self.PAGE_SIZE) or []
        for row in rows:
            item = QtWidgets.QListWidgetItem()
            item.setData(Qt.UserRole, str(row.get("chat_id") or ""))
            item.setSizeHint(QtCore.QSize(0, 58))
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, self._build_row_widget(row))
        self._offset += len(rows)
        self._has_more = len(rows) >= self.PAGE_SIZE

    def open_at(self, anchor_widget: QtWidgets.QWidget):
        self.reload()
        pos = anchor_widget.mapToGlobal(QtCore.QPoint(0, anchor_widget.height() + 4))
        self.move(pos)
        self.show()
        self.raise_()
        self.search.setFocus()


class ChatHistoryManagerDialog(QtWidgets.QDialog):
    def __init__(
        self,
        list_callback: Callable[[str, int, int], List[Dict]],
        delete_callback: Callable[[str], bool],
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("PyMolAI History Manager")
        self.resize(680, 480)

        self._list_callback = list_callback
        self._delete_callback = delete_callback

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.search = QtWidgets.QLineEdit(self)
        self.search.setPlaceholderText("Search chats...")
        root.addWidget(self.search)

        self.list_widget = QtWidgets.QListWidget(self)
        self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        root.addWidget(self.list_widget, 1)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.refresh_btn = QtWidgets.QPushButton("Refresh", self)
        self.delete_btn = QtWidgets.QPushButton("Delete Selected", self)
        self.close_btn = QtWidgets.QPushButton("Close", self)
        buttons.addWidget(self.refresh_btn)
        buttons.addWidget(self.delete_btn)
        buttons.addWidget(self.close_btn)
        root.addLayout(buttons)

        self.refresh_btn.clicked.connect(self.reload)
        self.delete_btn.clicked.connect(self._delete_selected)
        self.close_btn.clicked.connect(self.accept)
        self.search.textChanged.connect(lambda _v: self.reload())

        self.reload()

    @staticmethod
    def _format_row(row: Dict) -> str:
        title = _display_title(str(row.get("title") or ""))
        updated = _format_local_time(str(row.get("updated_at") or ""))
        return "%s\nUpdated %s" % (title, updated)

    def reload(self):
        query = self.search.text().strip()
        self.list_widget.clear()

        offset = 0
        page = 100
        while True:
            rows = self._list_callback(query, offset, page) or []
            if not rows:
                break
            for row in rows:
                item = QtWidgets.QListWidgetItem(self._format_row(row))
                item.setData(Qt.UserRole, str(row.get("chat_id") or ""))
                self.list_widget.addItem(item)
            if len(rows) < page:
                break
            offset += len(rows)

    def _delete_selected(self):
        item = self.list_widget.currentItem()
        if item is None:
            return
        chat_id = str(item.data(Qt.UserRole) or "")
        if not chat_id:
            return

        ans = QtWidgets.QMessageBox.question(
            self,
            "Delete Chat",
            "Delete this chat and its saved session files?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if ans != QtWidgets.QMessageBox.Yes:
            return

        self._delete_callback(chat_id)
        self.reload()
