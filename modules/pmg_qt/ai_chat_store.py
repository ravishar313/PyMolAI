from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional


class AiChatStore:
    """Fast local chat/session persistence for PyMolAI.

    Layout:
      ~/.pymolai/chats/<chat_id>/manifest.json
      ~/.pymolai/chats/<chat_id>/events.jsonl
      ~/.pymolai/chats/<chat_id>/session.pse
      ~/.pymolai/chats/index.jsonl
    """

    def __init__(
        self,
        root_dir: Optional[str] = None,
        *,
        flush_delay_sec: float = 0.08,
        checkpoint_delay_sec: float = 1.5,
        soft_cap: int = 100,
    ):
        self.root_dir = Path(os.path.expanduser(root_dir or "~/.pymolai/chats"))
        self.index_path = self.root_dir / "index.jsonl"
        self.flush_delay_sec = max(0.0, float(flush_delay_sec))
        self.checkpoint_delay_sec = max(0.0, float(checkpoint_delay_sec))
        self.soft_cap = max(1, int(soft_cap))

        self.root_dir.mkdir(parents=True, exist_ok=True)

        self.current_chat_id: Optional[str] = None
        self._current_chat_dir: Optional[Path] = None
        self._manifest: Optional[Dict[str, Any]] = None
        self._events_fp = None

        self._pending_event_lines: List[str] = []
        self._manifest_dirty = False
        self._index_dirty = False
        self._flush_due_at: Optional[float] = None

        self._scene_dirty = False
        self._checkpoint_due_at: Optional[float] = None

        self._index_latest: Dict[str, Dict[str, Any]] = {}
        self._load_index_cache()

    @staticmethod
    def _now_iso() -> str:
        return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    @staticmethod
    def _slug(text: str, fallback: str = "chat", max_len: int = 36) -> str:
        raw = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(text or ""))
        raw = "-".join(part for part in raw.split("-") if part)
        if not raw:
            raw = fallback
        return raw[:max_len]

    @staticmethod
    def _first_line(text: str, max_len: int = 160) -> str:
        line = str(text or "").splitlines()[0].strip() if str(text or "").splitlines() else ""
        if len(line) > max_len:
            return line[: max_len - 1] + "..."
        return line

    def _new_chat_id(self, title_hint: str = "") -> str:
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        slug = self._slug(title_hint, fallback="chat")
        return "%s-%s-%s" % (ts, slug, uuid.uuid4().hex[:6])

    def _new_manifest(self, chat_id: str, title_hint: str = "") -> Dict[str, Any]:
        now = self._now_iso()
        stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        slug = self._slug(title_hint, fallback="new-chat", max_len=28).replace("-", " ").strip()
        title = "%s - %s" % (stamp, slug or "new chat")
        return {
            "chat_id": chat_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "last_opened_at": now,
            "preview": "",
            "message_count": 0,
            "has_session_pse": False,
            "session_pse_path": "session.pse",
            "runtime_state": {
                "input_mode": "ai",
                "history": [],
                "model_info": {},
            },
            "save_status": {
                "last_pse_save_ok": True,
                "last_pse_save_at": None,
                "last_pse_error": None,
            },
        }

    @staticmethod
    def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(str(tmp_path), str(path))

    def _append_index_row(self, row: Dict[str, Any]) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        with self.index_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _index_row_from_manifest(self, manifest: Dict[str, Any]) -> Dict[str, Any]:
        message_count = int(manifest.get("message_count") or 0)
        save_status = manifest.get("save_status") or {}
        return {
            "chat_id": manifest.get("chat_id"),
            "title": manifest.get("title") or "",
            "updated_at": manifest.get("updated_at") or "",
            "preview": manifest.get("preview") or "",
            "message_count": message_count,
            "has_session_pse": bool(manifest.get("has_session_pse")),
            "last_pse_save_ok": bool(save_status.get("last_pse_save_ok", True)),
        }

    @staticmethod
    def _is_visible_chat_row(row: Dict[str, Any]) -> bool:
        return int(row.get("message_count") or 0) > 0

    def _load_index_cache(self) -> None:
        self._index_latest = {}
        if self.index_path.exists():
            with self.index_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    chat_id = str(row.get("chat_id") or "").strip()
                    if not chat_id:
                        continue
                    if row.get("deleted"):
                        self._index_latest.pop(chat_id, None)
                    else:
                        if self._is_visible_chat_row(row):
                            self._index_latest[chat_id] = row
                        else:
                            self._index_latest.pop(chat_id, None)

        if self._index_latest:
            return

        for child in self.root_dir.iterdir() if self.root_dir.exists() else ():
            if not child.is_dir():
                continue
            manifest_path = child / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                with manifest_path.open("r", encoding="utf-8") as handle:
                    manifest = json.load(handle)
            except Exception:
                continue
            chat_id = str(manifest.get("chat_id") or "").strip()
            if not chat_id:
                continue
            row = self._index_row_from_manifest(manifest)
            if self._is_visible_chat_row(row):
                self._index_latest[chat_id] = row

    def _ensure_events_handle(self) -> None:
        if self._events_fp is not None:
            return
        if not self._current_chat_dir:
            return
        events_path = self._current_chat_dir / "events.jsonl"
        self._events_fp = events_path.open("a", encoding="utf-8")

    def _close_events_handle(self) -> None:
        if self._events_fp is None:
            return
        try:
            self._events_fp.flush()
            self._events_fp.close()
        except Exception:
            pass
        self._events_fp = None

    @property
    def current_manifest(self) -> Optional[Dict[str, Any]]:
        return dict(self._manifest) if self._manifest else None

    def list_chats(self, query: str = "", offset: int = 0, limit: int = 30) -> List[Dict[str, Any]]:
        query_low = str(query or "").strip().lower()
        rows = [row for row in self._index_latest.values() if self._is_visible_chat_row(row)]
        rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        if query_low:
            rows = [
                row
                for row in rows
                if query_low in str(row.get("title") or "").lower()
                or query_low in str(row.get("preview") or "").lower()
            ]
        start = max(0, int(offset))
        end = start + max(1, int(limit))
        return rows[start:end]

    def count_chats(self) -> int:
        return len(self._index_latest)

    def _touch_flush_deadline(self) -> None:
        if self.flush_delay_sec <= 0:
            self._flush_due_at = 0.0
            return
        import time

        self._flush_due_at = time.monotonic() + self.flush_delay_sec

    def _touch_checkpoint_deadline(self) -> None:
        if self.checkpoint_delay_sec <= 0:
            self._checkpoint_due_at = 0.0
            return
        import time

        self._checkpoint_due_at = time.monotonic() + self.checkpoint_delay_sec

    def has_pending_io(self) -> bool:
        return bool(self._pending_event_lines or self._manifest_dirty or self._index_dirty)

    def has_pending_checkpoint(self) -> bool:
        return bool(self._scene_dirty or self._checkpoint_due_at is not None)

    def has_unsaved_changes(self) -> bool:
        return self.has_pending_io() or self.has_pending_checkpoint()

    def create_chat(self, title_hint: str = "") -> str:
        self.flush_now()
        self._close_events_handle()

        # Drop prior empty draft before creating another draft chat.
        if self.current_chat_id and self._manifest and int(self._manifest.get("message_count") or 0) == 0:
            self.delete_chat(self.current_chat_id)

        chat_id = self._new_chat_id(title_hint=title_hint)
        chat_dir = self.root_dir / chat_id
        chat_dir.mkdir(parents=True, exist_ok=True)

        manifest = self._new_manifest(chat_id, title_hint=title_hint)
        self._atomic_write_json(chat_dir / "manifest.json", manifest)

        self.current_chat_id = chat_id
        self._current_chat_dir = chat_dir
        self._manifest = manifest
        self._scene_dirty = False
        self._checkpoint_due_at = None
        self._pending_event_lines = []
        self._manifest_dirty = False
        self._index_dirty = False
        self._touch_flush_deadline()
        self._ensure_events_handle()
        return chat_id

    def open_chat(self, chat_id: str) -> bool:
        self.flush_now()
        chat_id = str(chat_id or "").strip()
        if not chat_id:
            return False

        chat_dir = self.root_dir / chat_id
        manifest_path = chat_dir / "manifest.json"
        if not manifest_path.exists():
            return False

        try:
            with manifest_path.open("r", encoding="utf-8") as handle:
                manifest = json.load(handle)
        except Exception:
            return False

        self._close_events_handle()
        self.current_chat_id = chat_id
        self._current_chat_dir = chat_dir
        self._manifest = manifest
        self._manifest["last_opened_at"] = self._now_iso()
        self._manifest_dirty = True
        self._index_dirty = True
        self._scene_dirty = False
        self._checkpoint_due_at = None
        self._ensure_events_handle()
        self._touch_flush_deadline()
        self.flush_now()
        return True

    def _normalize_event(self, event: Any) -> Dict[str, Any]:
        if isinstance(event, dict):
            role = event.get("role", "system")
            text = event.get("text", "")
            ok = event.get("ok")
            metadata = event.get("metadata") or {}
            ts = event.get("ts") or self._now_iso()
        else:
            role = getattr(event, "role", "system")
            role = getattr(role, "value", role)
            text = getattr(event, "text", "")
            ok = getattr(event, "ok", None)
            metadata = getattr(event, "metadata", {}) or {}
            ts = self._now_iso()

        try:
            json.dumps(metadata, ensure_ascii=False)
        except Exception:
            metadata = {"raw": str(metadata)}

        out = {
            "ts": str(ts),
            "role": str(role or "system"),
            "text": str(text or ""),
            "ok": ok if isinstance(ok, bool) else None,
            "metadata": metadata,
        }
        return out

    @staticmethod
    def _sanitize_runtime_state(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        payload = dict(state or {})
        input_mode = "cli" if str(payload.get("input_mode") or "").lower() == "cli" else "ai"
        history = payload.get("history") or []
        if not isinstance(history, list):
            history = []
        if len(history) > 80:
            history = history[-80:]
        model_info = payload.get("model_info") or {}
        if not isinstance(model_info, dict):
            model_info = {}
        return {
            "input_mode": input_mode,
            "history": history,
            "model_info": model_info,
        }

    def set_runtime_state(self, chat_id: str, state: Optional[Dict[str, Any]]) -> None:
        if not self._manifest or chat_id != self.current_chat_id:
            return
        self._manifest["runtime_state"] = self._sanitize_runtime_state(state)
        self._manifest["updated_at"] = self._now_iso()
        self._manifest_dirty = True
        if int(self._manifest.get("message_count") or 0) > 0:
            self._index_dirty = True
        self._touch_flush_deadline()

    def append_events(self, chat_id: str, events: Iterable[Any]) -> int:
        if not self._manifest or chat_id != self.current_chat_id:
            return 0

        appended = 0
        first_user_captured = int(self._manifest.get("message_count") or 0) == 0

        for event in events:
            record = self._normalize_event(event)
            self._pending_event_lines.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            appended += 1

            self._manifest["message_count"] = int(self._manifest.get("message_count") or 0) + 1
            self._manifest["updated_at"] = record["ts"]
            preview = self._first_line(record.get("text") or "")
            if preview:
                self._manifest["preview"] = preview

            if first_user_captured and record.get("role") == "user" and preview:
                stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
                self._manifest["title"] = "%s - %s" % (stamp, self._slug(preview, fallback="chat", max_len=28).replace("-", " "))
                first_user_captured = False

        if appended:
            self._manifest_dirty = True
            self._index_dirty = True
            self._touch_flush_deadline()

        return appended

    def mark_scene_dirty(self, chat_id: str, reason: str = "") -> None:
        if chat_id != self.current_chat_id:
            return
        self._scene_dirty = True

    def schedule_checkpoint(self, chat_id: str) -> None:
        if chat_id != self.current_chat_id:
            return
        if not self._scene_dirty:
            return
        self._touch_checkpoint_deadline()

    def _update_save_status(self, ok: bool, error: Optional[str]) -> None:
        if not self._manifest:
            return
        now = self._now_iso()
        save_status = self._manifest.setdefault("save_status", {})
        save_status["last_pse_save_ok"] = bool(ok)
        save_status["last_pse_save_at"] = now
        save_status["last_pse_error"] = None if ok else str(error or "unknown error")
        if ok:
            self._manifest["has_session_pse"] = True
        self._manifest["updated_at"] = now
        self._manifest_dirty = True
        self._index_dirty = True
        self._touch_flush_deadline()

    def run_checkpoint(self, save_callback: Callable[[str], None]) -> bool:
        if not self.current_chat_id or not self._current_chat_dir:
            return False
        if not self._scene_dirty:
            return False

        session_path = self._current_chat_dir / "session.pse"
        ok = False
        error = None
        try:
            save_callback(str(session_path))
            ok = True
        except Exception as exc:  # noqa: BLE001
            error = exc

        self._update_save_status(ok=ok, error=str(error) if error else None)
        self._scene_dirty = not ok
        if self._scene_dirty:
            self._touch_checkpoint_deadline()
        else:
            self._checkpoint_due_at = None
        self.flush_now()
        return ok

    def force_checkpoint(self, save_callback: Callable[[str], None]) -> bool:
        self.flush_now()
        return self.run_checkpoint(save_callback)

    def pump(self, save_callback: Callable[[str], None]) -> None:
        import time

        now = time.monotonic()
        if self._flush_due_at is not None and now >= self._flush_due_at:
            self.flush_now()

        if self._checkpoint_due_at is not None and now >= self._checkpoint_due_at:
            self.run_checkpoint(save_callback)

    def flush_now(self) -> None:
        if not self._manifest or not self._current_chat_dir:
            self._flush_due_at = None
            return

        self._ensure_events_handle()
        if self._pending_event_lines and self._events_fp is not None:
            self._events_fp.writelines(self._pending_event_lines)
            self._events_fp.flush()
            self._pending_event_lines = []

        if self._manifest_dirty:
            self._atomic_write_json(self._current_chat_dir / "manifest.json", self._manifest)
            self._manifest_dirty = False

        if self._index_dirty:
            row = self._index_row_from_manifest(self._manifest)
            if self._is_visible_chat_row(row):
                self._index_latest[self.current_chat_id] = row
                self._append_index_row(row)
            elif self.current_chat_id in self._index_latest:
                self._index_latest.pop(self.current_chat_id, None)
                self._append_index_row({"chat_id": self.current_chat_id, "deleted": True, "updated_at": self._now_iso()})
            self._index_dirty = False

        self._flush_due_at = None

    def load_chat(self, chat_id: str) -> Optional[Dict[str, Any]]:
        self.flush_now()
        chat_id = str(chat_id or "").strip()
        if not chat_id:
            return None

        chat_dir = self.root_dir / chat_id
        manifest_path = chat_dir / "manifest.json"
        if not manifest_path.exists():
            return None

        try:
            with manifest_path.open("r", encoding="utf-8") as handle:
                manifest = json.load(handle)
        except Exception:
            return None

        events = []
        events_path = chat_dir / "events.jsonl"
        if events_path.exists():
            with events_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue
                    events.append(record)

        session_rel = str(manifest.get("session_pse_path") or "session.pse")
        session_path = chat_dir / session_rel

        return {
            "chat_id": chat_id,
            "manifest": manifest,
            "events": events,
            "session_path": str(session_path),
            "session_exists": session_path.exists(),
        }

    def delete_chat(self, chat_id: str) -> bool:
        chat_id = str(chat_id or "").strip()
        if not chat_id:
            return False

        if self.current_chat_id == chat_id:
            self.flush_now()
            self._close_events_handle()
            self.current_chat_id = None
            self._current_chat_dir = None
            self._manifest = None
            self._pending_event_lines = []
            self._manifest_dirty = False
            self._index_dirty = False
            self._flush_due_at = None
            self._scene_dirty = False
            self._checkpoint_due_at = None

        chat_dir = self.root_dir / chat_id
        if chat_dir.exists():
            shutil.rmtree(str(chat_dir), ignore_errors=True)

        self._index_latest.pop(chat_id, None)
        self._append_index_row({"chat_id": chat_id, "deleted": True, "updated_at": self._now_iso()})
        return True

    def delete_oldest(self, count: int) -> int:
        n = max(0, int(count))
        if n <= 0:
            return 0
        rows = list(self._index_latest.values())
        rows.sort(key=lambda row: str(row.get("updated_at") or ""))
        deleted = 0
        for row in rows[:n]:
            chat_id = str(row.get("chat_id") or "")
            if chat_id and self.delete_chat(chat_id):
                deleted += 1
        return deleted

    def get_last_valid_session_path(self, exclude_chat_id: str = "") -> str:
        exclude_chat_id = str(exclude_chat_id or "")
        rows = list(self._index_latest.values())
        rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        for row in rows:
            chat_id = str(row.get("chat_id") or "")
            if not chat_id or chat_id == exclude_chat_id:
                continue
            if not row.get("has_session_pse"):
                continue
            if row.get("last_pse_save_ok") is False:
                continue
            path = self.root_dir / chat_id / "session.pse"
            if path.exists():
                return str(path)
        return ""

    def close(self) -> None:
        self.flush_now()
        self._close_events_handle()
