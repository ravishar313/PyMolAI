from __future__ import annotations

import os

from pymol.Qt import QtCore, QtWidgets

from pymol.ai.openbio_api_key_store import (
    ApiKeyStoreError,
    clear_saved_key_and_loaded_env_if_needed,
    get_status,
    save_key,
    validate_key_live,
)


class _KeyValidationWorker(QtCore.QObject):
    finished = QtCore.Signal(bool, str)

    def __init__(self, key: str, timeout_sec: float):
        super().__init__()
        self._key = str(key or "").strip()
        self._timeout_sec = float(timeout_sec)

    @QtCore.Slot()
    def run(self):
        try:
            validate_key_live(self._key, timeout_sec=self._timeout_sec)
        except Exception as exc:  # noqa: BLE001
            self.finished.emit(False, str(exc))
            return
        self.finished.emit(True, "API key is valid.")


class AiOpenBioApiKeyDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, *, on_changed=None):
        super().__init__(parent)
        self._on_changed = on_changed
        self._worker = None
        self._worker_thread = None

        self.setWindowTitle("OpenBio API Key")
        self.setModal(True)
        self.resize(520, 180)

        layout = QtWidgets.QVBoxLayout(self)

        self.status_label = QtWidgets.QLabel(self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        form = QtWidgets.QFormLayout()
        self.key_input = QtWidgets.QLineEdit(self)
        self.key_input.setEchoMode(QtWidgets.QLineEdit.PasswordEchoOnEdit)
        self.key_input.setPlaceholderText("Enter OpenBio API key")
        form.addRow("API Key", self.key_input)
        layout.addLayout(form)

        buttons_row = QtWidgets.QHBoxLayout()
        self.save_button = QtWidgets.QPushButton("Save", self)
        self.clear_button = QtWidgets.QPushButton("Clear", self)
        self.test_button = QtWidgets.QPushButton("Test", self)
        self.close_button = QtWidgets.QPushButton("Close", self)
        buttons_row.addWidget(self.save_button)
        buttons_row.addWidget(self.clear_button)
        buttons_row.addWidget(self.test_button)
        buttons_row.addStretch(1)
        buttons_row.addWidget(self.close_button)
        layout.addLayout(buttons_row)

        self.save_button.clicked.connect(self._on_save)
        self.clear_button.clicked.connect(self._on_clear)
        self.test_button.clicked.connect(self._on_test)
        self.close_button.clicked.connect(self.accept)

        self._refresh_status()

    def _status_text(self) -> str:
        status = get_status()
        if status.source == "env" and status.has_key:
            source = "environment"
        elif status.source == "saved" and status.has_key:
            source = "saved keychain"
        else:
            source = "not set"

        key_text = status.masked_key or "(none)"
        keyring_text = "available" if status.keyring_available else "unavailable"
        return (
            "Current source: %s\n"
            "Current key: %s\n"
            "System keychain: %s"
        ) % (source, key_text, keyring_text)

    def _refresh_status(self):
        self.status_label.setText(self._status_text())

    def _notify_changed(self):
        callback = self._on_changed
        if callable(callback):
            callback()

    def _on_save(self):
        key = str(self.key_input.text() or "").strip()
        if not key:
            QtWidgets.QMessageBox.warning(self, "Save API Key", "Please enter a non-empty API key.")
            return

        try:
            save_key(key)
        except ApiKeyStoreError as exc:
            QtWidgets.QMessageBox.critical(self, "Save API Key", str(exc))
            return

        os.environ["OPENBIO_API_KEY"] = key
        os.environ["PYMOL_AI_OPENBIO_KEY_SOURCE"] = "saved_keyring"
        self.key_input.clear()
        self._notify_changed()
        self._refresh_status()
        QtWidgets.QMessageBox.information(self, "Save API Key", "API key saved to system keychain.")

    def _on_clear(self):
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Clear API Key",
            "Delete the saved API key from system keychain?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if confirm != QtWidgets.QMessageBox.Yes:
            return

        try:
            env_cleared = clear_saved_key_and_loaded_env_if_needed()
        except ApiKeyStoreError as exc:
            QtWidgets.QMessageBox.critical(self, "Clear API Key", str(exc))
            return

        self.key_input.clear()
        self._notify_changed()
        self._refresh_status()
        msg = "Saved API key cleared."
        if env_cleared:
            msg += " Active in-process key was also cleared."
        QtWidgets.QMessageBox.information(self, "Clear API Key", msg)

    def _set_testing_state(self, active: bool):
        self.save_button.setEnabled(not active)
        self.clear_button.setEnabled(not active)
        self.test_button.setEnabled(not active)
        self.close_button.setEnabled(not active)

    def _on_test(self):
        if self._worker_thread is not None:
            return

        key = str(self.key_input.text() or "").strip() or str(os.getenv("OPENBIO_API_KEY") or "").strip()
        if not key:
            QtWidgets.QMessageBox.warning(self, "Test API Key", "No API key is available to test.")
            return

        self._set_testing_state(True)
        worker = _KeyValidationWorker(key, timeout_sec=10.0)
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_test_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._worker = worker
        self._worker_thread = thread
        thread.start()

    @QtCore.Slot(bool, str)
    def _on_test_finished(self, ok: bool, message: str):
        self._worker = None
        self._worker_thread = None
        self._set_testing_state(False)
        if ok:
            QtWidgets.QMessageBox.information(self, "Test API Key", "API key validation succeeded.")
            return
        text = str(message or "API key validation failed.")
        QtWidgets.QMessageBox.warning(self, "Test API Key", text)
