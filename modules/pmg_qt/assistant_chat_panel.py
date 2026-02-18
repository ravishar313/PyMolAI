from __future__ import annotations

import html
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


class ToolStartChip(QtWidgets.QFrame):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("toolStartChip")

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        badge = QtWidgets.QLabel("TOOL")
        badge.setObjectName("toolStartBadge")
        layout.addWidget(badge)

        summary = QtWidgets.QLabel(str(text or ""))
        summary.setWordWrap(True)
        summary.setObjectName("toolStartSummary")
        layout.addWidget(summary, 1)

        self.setStyleSheet(
            """
            QFrame#toolStartChip {
                background: #2b2412;
                border: 1px solid #6c5a20;
                border-radius: 8px;
            }
            QLabel#toolStartBadge {
                color: #1a1a1a;
                background: #e3b341;
                border-radius: 4px;
                padding: 2px 6px;
                font-weight: 700;
                font-size: 10px;
            }
            QLabel#toolStartSummary {
                color: #f8d98c;
                font-size: 12px;
            }
            """
        )


class ToolResultCard(QtWidgets.QFrame):
    def __init__(self, text: str, ok: bool, metadata: Optional[dict] = None, parent=None):
        super().__init__(parent)
        metadata = metadata or {}
        self._expanded = False

        self.setObjectName("toolResultCard")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)
        status = QtWidgets.QLabel("TOOL OK" if ok else "TOOL ERROR")
        status.setObjectName("toolResultStatusOk" if ok else "toolResultStatusErr")
        header.addWidget(status, 0)

        lines = str(text or "").splitlines()
        summary_text = lines[0] if lines else ("ok" if ok else "error")
        summary = QtWidgets.QLabel(summary_text)
        summary.setWordWrap(True)
        summary.setObjectName("toolResultSummary")
        header.addWidget(summary, 1)

        self.details_button = QtWidgets.QPushButton("Details")
        self.details_button.setObjectName("toolResultDetailsButton")
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self._toggle_details)
        header.addWidget(self.details_button, 0)

        layout.addLayout(header)

        vv = metadata.get("visual_validation")
        if vv:
            note = QtWidgets.QLabel(str(vv))
            note.setWordWrap(True)
            note.setObjectName("toolResultMeta")
            layout.addWidget(note)

        self.details = AutoHeightTextBrowser()
        self.details.setObjectName("toolResultDetails")
        self.details.setHtml(_plain_to_html(str(text or ""), monospace=True))
        self.details.hide()
        layout.addWidget(self.details)

        self.setStyleSheet(
            """
            QFrame#toolResultCard {
                background: #121a24;
                border: 1px solid #2e445f;
                border-radius: 8px;
            }
            QLabel#toolResultStatusOk {
                color: #0f2918;
                background: #3fb950;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 10px;
                font-weight: 700;
            }
            QLabel#toolResultStatusErr {
                color: #2e0d11;
                background: #f85149;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 10px;
                font-weight: 700;
            }
            QLabel#toolResultSummary {
                color: #d5deea;
                font-size: 12px;
            }
            QLabel#toolResultMeta {
                color: #8fa4bf;
                font-size: 11px;
                font-style: italic;
            }
            QPushButton#toolResultDetailsButton {
                padding: 2px 8px;
            }
            QTextBrowser#toolResultDetails {
                color: #b9d1ec;
                background: #0d131d;
            }
            """
        )

    def _toggle_details(self, visible: bool):
        self._expanded = bool(visible)
        self.details.setVisible(self._expanded)
        self.details_button.setText("Hide" if self._expanded else "Details")


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
        self._mode = "ai"

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        title = QtWidgets.QLabel("Assistant Chat")
        title.setObjectName("chatPanelTitle")
        header.addWidget(title)

        header.addStretch(1)

        self.mode_badge = QtWidgets.QLabel("AI")
        self.mode_badge.setObjectName("chatModeBadge")
        header.addWidget(self.mode_badge)

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
            QLabel#chatPanelTitle {
                color: #d7dde5;
                font-size: 13px;
                font-weight: 700;
            }
            QLabel#chatModeBadge {
                color: #04162f;
                background: #4ea1ff;
                border-radius: 9px;
                font-size: 11px;
                font-weight: 700;
                padding: 2px 8px;
                min-width: 28px;
                qproperty-alignment: AlignCenter;
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
            self.mode_badge.setText("CLI")
            self.mode_badge.setStyleSheet(
                "color:#231700;background:#f4b347;border-radius:9px;padding:2px 8px;font-size:11px;font-weight:700;"
            )
            self.input_edit.setPlaceholderText("CLI mode: type a PyMOL command (Enter to run, Shift+Enter newline)")
        else:
            self.mode_badge.setText("AI")
            self.mode_badge.setStyleSheet(
                "color:#04162f;background:#4ea1ff;border-radius:9px;padding:2px 8px;font-size:11px;font-weight:700;"
            )
            self.input_edit.setPlaceholderText("Ask PyMOL assistant... (Enter to send, Shift+Enter newline)")

    def clear_transcript(self):
        self._active_ai_bubble = None
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
                bubble = MessageBubble("You", kind="user", markdown=False)
                bubble.set_text(text)
                self._append_widget(bubble)
            elif role == "tool_start":
                self._append_widget(ToolStartChip(text))
            elif role == "tool_result":
                self._append_widget(ToolResultCard(text, bool(getattr(event, "ok", False)), getattr(event, "metadata", None)))
            elif role == "reasoning":
                bubble = MessageBubble("Reasoning", kind="reasoning", markdown=False)
                bubble.set_text(text)
                self._append_widget(bubble)
            elif role == "error":
                bubble = MessageBubble("Error", kind="error", markdown=False)
                bubble.set_text(text)
                self._append_widget(bubble)
            else:
                bubble = MessageBubble("System", kind="system", markdown=False)
                bubble.set_text(text)
                self._append_widget(bubble)

    def _append_ai_text(self, text: str):
        if not self._active_ai_bubble:
            self._active_ai_bubble = MessageBubble("Assistant", kind="assistant", markdown=True)
            self._append_widget(self._active_ai_bubble)
        self._active_ai_bubble.append_text(text)
        self._scroll_to_bottom()

    def _append_widget(self, widget: QtWidgets.QWidget):
        self.feed_layout.insertWidget(self.feed_layout.count() - 1, widget)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        bar = self.scroll.verticalScrollBar()
        QtCore.QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))
