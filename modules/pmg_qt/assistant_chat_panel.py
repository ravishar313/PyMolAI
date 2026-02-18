from __future__ import annotations

import html
import json
from typing import Optional

from pymol.Qt import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt


def _markdown_to_html(text: str) -> str:
    doc = QtGui.QTextDocument()
    if hasattr(doc, "setMarkdown"):
        doc.setMarkdown(str(text or ""))
    else:
        doc.setPlainText(str(text or ""))
    return doc.toHtml()


def _plain_to_html(text: str, *, monospace: bool = False) -> str:
    escaped = html.escape(str(text or "")).replace("\n", "<br>")
    if monospace:
        return '<span style="font-family: Menlo, Consolas, monospace;">%s</span>' % (escaped,)
    return escaped


class AutoHeightTextBrowser(QtWidgets.QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setOpenExternalLinks(True)
        self.document().setDocumentMargin(0)
        self.document().contentsChanged.connect(self._sync_height)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_height()

    def _sync_height(self):
        width = max(100, self.viewport().width())
        doc = self.document()
        doc.setTextWidth(width)
        height = int(doc.size().height()) + 6
        self.setFixedHeight(max(26, height))


class MessageBubble(QtWidgets.QFrame):
    def __init__(self, title: str, *, kind: str = "assistant", markdown: bool = False, monospace: bool = False, parent=None):
        super().__init__(parent)
        self._markdown = bool(markdown)
        self._monospace = bool(monospace)
        self._raw_text = ""

        self.setObjectName("chatBubble")
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        self.title = QtWidgets.QLabel(title)
        self.title.setObjectName("chatBubbleTitle")
        layout.addWidget(self.title)

        self.body = AutoHeightTextBrowser()
        self.body.setObjectName("chatBubbleBody")
        self.body.setSizeAdjustPolicy(QtWidgets.QAbstractScrollArea.AdjustToContents)
        layout.addWidget(self.body)

        palette = {
            "assistant": ("#1b2330", "#33435b", "#e6edf3"),
            "user": ("#14345c", "#2f6cb3", "#eaf2ff"),
            "system": ("#2b2b2b", "#4a4a4a", "#f0f0f0"),
            "reasoning": ("#262a31", "#3b4048", "#b4bdc8"),
            "error": ("#3a1f25", "#7b313f", "#ffd7de"),
        }
        bg, border, text = palette.get(kind, palette["assistant"])
        self.setStyleSheet(
            """
            QFrame#chatBubble {
                background: %s;
                border: 1px solid %s;
                border-radius: 8px;
            }
            QLabel#chatBubbleTitle {
                color: #9da7b3;
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 0.5px;
                text-transform: uppercase;
            }
            QTextBrowser#chatBubbleBody {
                color: %s;
                background: transparent;
                font-size: 13px;
            }
            """
            % (bg, border, text)
        )

    def set_text(self, text: str):
        self._raw_text = str(text or "")
        if self._markdown:
            self.body.setHtml(_markdown_to_html(self._raw_text))
        else:
            self.body.setHtml(_plain_to_html(self._raw_text, monospace=self._monospace))
        self.body._sync_height()

    def append_text(self, text: str):
        chunk = str(text or "")
        if not chunk:
            return
        if self._raw_text:
            self._raw_text = self._raw_text + "\n" + chunk
        else:
            self._raw_text = chunk
        self.set_text(self._raw_text)


class ToolResultCard(QtWidgets.QFrame):
    def __init__(
        self,
        text: str,
        ok: bool,
        metadata: Optional[dict] = None,
        tool_label: str = "",
        parent=None,
    ):
        super().__init__(parent)
        metadata = dict(metadata or {})
        self._expanded = False
        self._tool_name = self._resolve_tool_name(text, metadata, tool_label)
        args_text = self._json_block(metadata.get("tool_args"), fallback="{}")
        result_source = metadata.get("tool_result_json")
        if result_source is None:
            result_source = text
        result_text = self._json_block(result_source, fallback=str(text or "").strip())
        details_payload = "Arguments\n%s\n\nResult\n%s" % (args_text, result_text)

        self.setObjectName("toolResultCard")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(6)

        summary = QtWidgets.QLabel("Ran tool: %s" % (self._tool_name,))
        summary.setWordWrap(True)
        summary.setObjectName("toolResultSummary")
        header.addWidget(summary, 1)

        self.details_button = QtWidgets.QToolButton()
        self.details_button.setObjectName("toolResultDetailsButton")
        self.details_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.details_button.setArrowType(Qt.RightArrow)
        self.details_button.setCheckable(True)
        self.details_button.setCursor(Qt.PointingHandCursor)
        self.details_button.setToolTip("Show details")
        self.details_button.toggled.connect(self._toggle_details)
        header.addWidget(self.details_button, 0)

        layout.addLayout(header)

        self.details = AutoHeightTextBrowser()
        self.details.setObjectName("toolResultDetails")
        self.details.setHtml(_plain_to_html(details_payload, monospace=True))
        self.details.hide()
        layout.addWidget(self.details)

        self.setStyleSheet(
            """
            QFrame#toolResultCard {
                background: #121b27;
                border: 1px solid #2a3c52;
                border-radius: 8px;
            }
            QLabel#toolResultSummary {
                color: #d9e4f2;
                font-size: 12px;
                font-weight: 500;
            }
            QToolButton#toolResultDetailsButton {
                color: #a8c7ea;
                padding: 0px;
            }
            QTextBrowser#toolResultDetails {
                color: #c2d8ef;
                background: #0d131d;
                border: 1px solid #22344b;
                border-radius: 4px;
                padding: 6px;
            }
            """
        )

    @staticmethod
    def _resolve_tool_name(text: str, metadata: dict, tool_label: str) -> str:
        command = str(metadata.get("tool_command") or "").strip()
        if command:
            return command
        tool_name = str(metadata.get("tool_name") or "").strip()
        if tool_name:
            return tool_name
        if tool_label:
            return str(tool_label).strip()
        raw = str(text or "").strip()
        if raw.lower().startswith("ran tool:"):
            raw = raw[9:].strip()
        return raw or "tool"

    @staticmethod
    def _json_block(value, *, fallback: str = "") -> str:
        if value is None:
            return fallback or "null"
        parsed = value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return fallback or ""
            try:
                parsed = json.loads(stripped)
            except Exception:
                return stripped
        try:
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            return fallback or str(value)

    def _toggle_details(self, visible: bool):
        self._expanded = bool(visible)
        self.details.setVisible(self._expanded)
        self.details_button.setArrowType(Qt.DownArrow if self._expanded else Qt.RightArrow)


class ChatInputEdit(QtWidgets.QPlainTextEdit):
    submitRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabChangesFocus(False)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.textChanged.connect(self._sync_height)
        self._sync_height()

    def keyPressEvent(self, event):
        enter = event.key() in (Qt.Key_Return, Qt.Key_Enter)
        modifiers = event.modifiers()
        wants_newline = bool(modifiers & Qt.ShiftModifier)
        blocked = bool(modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
        if enter and not wants_newline and not blocked:
            self.submitRequested.emit()
            return
        super().keyPressEvent(event)

    def _sync_height(self):
        doc_h = int(self.document().size().height()) + 12
        self.setFixedHeight(max(52, min(130, doc_h)))


class AssistantChatPanel(QtWidgets.QWidget):
    sendCommand = QtCore.Signal(str)
    clearRequested = QtCore.Signal()
    stopRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active_ai_bubble = None
        self._pending_tool_start = ""
        self._last_ai_text = ""
        self._mode = "ai"

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        header.addStretch(1)

        self.clear_button = QtWidgets.QPushButton("Clear")
        self.clear_button.clicked.connect(self.clearRequested.emit)
        header.addWidget(self.clear_button)

        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.clicked.connect(self.stopRequested.emit)
        header.addWidget(self.stop_button)

        root.addLayout(header)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.feed_host = QtWidgets.QWidget()
        self.feed_layout = QtWidgets.QVBoxLayout(self.feed_host)
        self.feed_layout.setContentsMargins(0, 0, 0, 0)
        self.feed_layout.setSpacing(8)
        self.feed_layout.addStretch(1)

        self.scroll.setWidget(self.feed_host)
        root.addWidget(self.scroll, 1)

        composer_row = QtWidgets.QHBoxLayout()
        composer_row.setSpacing(6)

        self.input_edit = ChatInputEdit()
        self.input_edit.setObjectName("chatInput")
        self.input_edit.submitRequested.connect(self._submit_from_input)
        composer_row.addWidget(self.input_edit, 1)

        self.send_button = QtWidgets.QPushButton("Send")
        self.send_button.clicked.connect(self._submit_from_input)
        composer_row.addWidget(self.send_button)

        root.addLayout(composer_row)

        self.set_mode("ai")
        self.setStyleSheet(
            """
            AssistantChatPanel {
                background: #1f1f1f;
            }
            QPlainTextEdit#chatInput {
                color: #e8eef7;
                background: #14171d;
                border: 1px solid #3a4453;
                border-radius: 8px;
                padding: 6px;
                font-size: 13px;
            }
            QPushButton {
                padding: 4px 10px;
            }
            """
        )

    def sizeHint(self):
        return QtCore.QSize(340, 640)

    def _submit_from_input(self):
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        self.input_edit.clear()
        self.sendCommand.emit(text)

    def input_text(self) -> str:
        return self.input_edit.toPlainText()

    def set_input_text(self, text: str):
        self.input_edit.setPlainText(str(text or ""))

    def set_input_cursor(self, pos: int):
        cursor = self.input_edit.textCursor()
        cursor.setPosition(max(0, min(int(pos), len(self.input_edit.toPlainText()))))
        self.input_edit.setTextCursor(cursor)

    def focus_input(self):
        self.input_edit.setFocus()

    def set_mode(self, mode: str):
        normalized = "cli" if str(mode or "").lower() == "cli" else "ai"
        self._mode = normalized
        if normalized == "cli":
            self.input_edit.setPlaceholderText("CLI mode: type a PyMOL command (Enter to run, Shift+Enter newline)")
        else:
            self.input_edit.setPlaceholderText("Ask PyMolAI... (Enter to send, Shift+Enter newline)")

    def clear_transcript(self):
        self._active_ai_bubble = None
        self._pending_tool_start = ""
        self._last_ai_text = ""
        while self.feed_layout.count() > 1:
            item = self.feed_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def append_feedback_block(self, text: str):
        if not text:
            return
        self._active_ai_bubble = None
        bubble = MessageBubble("PyMOL", kind="system", markdown=False, monospace=True)
        bubble.set_text(text)
        self._append_widget(bubble)

    def append_ai_events(self, events):
        for event in events:
            raw_role = getattr(event, "role", "ai")
            role = getattr(raw_role, "value", raw_role)
            role = str(role)
            text = str(getattr(event, "text", "") or "")

            if role == "ai":
                self._append_ai_text(text)
                continue

            self._active_ai_bubble = None

            if role == "user":
                self._last_ai_text = ""
                bubble = MessageBubble("You", kind="user", markdown=False)
                bubble.set_text(text)
                self._append_widget(bubble)
            elif role == "tool_start":
                self._pending_tool_start = text
            elif role == "tool_result":
                metadata = dict(getattr(event, "metadata", None) or {})
                if self._pending_tool_start:
                    metadata.setdefault("tool_command", self._pending_tool_start)
                self._append_widget(
                    ToolResultCard(
                        text,
                        bool(getattr(event, "ok", False)),
                        metadata,
                        tool_label=self._pending_tool_start,
                    )
                )
                self._pending_tool_start = ""
            elif role == "reasoning":
                bubble = MessageBubble("Reasoning", kind="reasoning", markdown=False)
                bubble.set_text(text)
                self._append_widget(bubble)
            elif role == "error":
                bubble = MessageBubble("Error", kind="error", markdown=False)
                bubble.set_text(text)
                self._append_widget(bubble)
            else:
                if text.strip().lower() == "planning...":
                    continue
                bubble = MessageBubble("System", kind="system", markdown=False)
                bubble.set_text(text)
                self._append_widget(bubble)

    def _append_ai_text(self, text: str):
        clean = str(text or "").strip()
        if not clean:
            return
        if not self._active_ai_bubble and clean == self._last_ai_text:
            return
        if self._active_ai_bubble and self._active_ai_bubble._raw_text:
            last_line = self._active_ai_bubble._raw_text.splitlines()[-1].strip()
            if last_line == clean:
                return
        if not self._active_ai_bubble:
            self._active_ai_bubble = MessageBubble("PyMolAI", kind="assistant", markdown=True)
            self._append_widget(self._active_ai_bubble)
        self._active_ai_bubble.append_text(clean)
        self._last_ai_text = clean
        self._scroll_to_bottom()

    def _append_widget(self, widget: QtWidgets.QWidget):
        self.feed_layout.insertWidget(self.feed_layout.count() - 1, widget)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        bar = self.scroll.verticalScrollBar()
        QtCore.QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))
