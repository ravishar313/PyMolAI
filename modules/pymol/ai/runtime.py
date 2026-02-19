from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Dict, List, Optional, Tuple

from .claude_sdk_loop import ClaudeSdkLoop
from .message_types import UiEvent, UiRole
from .openrouter_client import DEFAULT_MODEL
from .state_snapshot import build_viewer_state_snapshot
from .tool_execution import run_pymol_command
from .vision_capture import capture_viewer_snapshot

SYSTEM_PROMPT = """You are a PyMOL desktop agent.
You can either:
1) call tools to act in PyMOL, or
2) provide a final direct answer without tool calls.

Rules:
- Use tool calls when an action/query in PyMOL is needed.
- If tool results already answer the user, return a concise final answer and DO NOT call tools.
- Do not use shell commands.
- Prefer continuing current session state; avoid redundant fetch/load.
- capture_viewer_snapshot is INTERNAL validation only. The user cannot see this image in chat.
- Never say you are taking a screenshot "to show" the user.
- If you use capture_viewer_snapshot, describe it as internal validation of viewer state.
- After state-changing commands, use capture_viewer_snapshot to verify the scene actually reflects the requested outcome.
- Do not claim completion until scene validation has been performed (or explicitly explain why validation failed).
- Do not repeat the same setup sentence or intent text step after step.
- If a strategy fails repeatedly, switch approach or ask the user for clarification.
- Do not re-run the same successful command in the same request unless you clearly explain why.
- Keep answers concise and practical.
"""

_READ_ONLY_PREFIXES = (
    "get_",
    "count_",
    "iterate",
    "indicate",
    "help",
)

_RE_PDB_ID = re.compile(r"^[0-9][A-Za-z0-9]{3}$")
_HIDDEN_SYSTEM_PREFIXES = (
    "Validation required:",
    "Visual validation required now:",
)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


class AiRuntime:
    def __init__(self, cmd):
        self.cmd = cmd
        self._logger = logging.getLogger("pymol.ai")
        self._log_to_terminal = os.getenv("PYMOL_AI_LOG_STDOUT", "1") != "0"
        self._log_to_python_logger = os.getenv("PYMOL_AI_LOGGER", "0") == "1"
        self.history: List[Dict[str, object]] = []
        self.model = os.getenv("PYMOL_AI_DEFAULT_MODEL") or DEFAULT_MODEL
        self.reasoning_visible = False
        self.input_mode = "ai"
        self.final_answer_enabled = os.getenv("PYMOL_AI_FINAL_ANSWER", "1") != "0"

        self.max_agent_steps = _env_int("PYMOL_AI_MAX_STEPS", 16)
        self.tool_result_max_chars = _env_int("PYMOL_AI_TOOL_RESULT_MAX_CHARS", 4096)
        self.long_tool_warn_sec = _env_float("PYMOL_AI_LONG_TOOL_WARN_SEC", 8.0)
        self.ui_event_batch = max(1, _env_int("PYMOL_AI_UI_EVENT_BATCH", 40))
        self.ui_max_events = max(0, _env_int("PYMOL_AI_UI_MAX_EVENTS", 2000))
        self.sdk_max_buffer_size = max(0, _env_int("PYMOL_AI_SDK_MAX_BUFFER_SIZE", 10 * 1024 * 1024))
        self.trace_stream_chunks = _env_int("PYMOL_AI_TRACE_STREAM", 1) == 1

        self.screenshot_width = _env_int("PYMOL_AI_SCREENSHOT_WIDTH", 1024)
        self.screenshot_height = _env_int("PYMOL_AI_SCREENSHOT_HEIGHT", 0)
        self.screenshot_validate_required = _env_int("PYMOL_AI_SCREENSHOT_VALIDATE_REQUIRED", 1) == 1
        self.state_max_selections = _env_int("PYMOL_AI_STATE_MAX_SELECTIONS", 20)
        self.state_max_objects = _env_int("PYMOL_AI_STATE_MAX_OBJECTS", 30)

        self._busy = False
        self._lock = threading.Lock()
        self._event_lock = threading.Lock()
        self._ui_events: List[UiEvent] = []
        self._ui_mode = "text"
        self._cancel_event = threading.Event()
        self._ui_compaction_notice_sent = False

        self._stream_line_buffer = ""
        self._stream_had_output = False
        self._stream_full_text = ""

        disabled = os.getenv("PYMOL_AI_DISABLE", "").strip() == "1"
        self.enabled = bool(self._api_key) and not disabled

        self._agent_backend = "claude_sdk"
        self._sdk_session_id: Optional[str] = None
        self._sdk_loop = ClaudeSdkLoop(logger=self._log_ai)
        self._sdk_loop.map_openrouter_env()
        self._recent_tool_results: List[Dict[str, object]] = []
        self._log_ai(
            "runtime initialized",
            enabled=self.enabled,
            input_mode=self.input_mode,
            model=self.model,
            backend=self._agent_backend,
            api_key_set=bool(self._api_key),
        )

    @property
    def _api_key(self) -> str:
        return (os.getenv("OPENROUTER_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or "").strip()

    def set_reasoning_visible(self, visible: bool) -> None:
        self.reasoning_visible = bool(visible)

    def set_ui_mode(self, mode: str) -> None:
        self._ui_mode = mode if mode in ("qt", "text") else "text"

    @property
    def current_input_mode(self) -> str:
        return self.input_mode

    @staticmethod
    def _log_value(value: object, max_len: int = 220) -> str:
        text = str(value).replace("\n", "\\n")
        if len(text) > max_len:
            return text[:max_len] + "...(truncated)"
        return text

    def _log_ai(self, message: str, level: str = "INFO", **fields) -> None:
        parts = []
        for key, value in fields.items():
            parts.append("%s=%s" % (key, self._log_value(value)))
        line = "[PyMolAI] %s %s" % (level.upper(), message)
        if parts:
            line += " | " + " ".join(parts)

        if self._log_to_terminal:
            print(line)
        if self._log_to_python_logger:
            log_level = getattr(logging, str(level).upper(), logging.INFO)
            self._logger.log(log_level, line)

    def request_cancel(self) -> bool:
        self._cancel_event.set()
        with self._lock:
            busy = bool(self._busy)
        self._log_ai("cancel requested", busy=busy)
        if busy:
            self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="cancellation requested..."))
        return busy

    def clear_session(self, emit_notice: bool = True) -> None:
        self.history.clear()
        self._stream_line_buffer = ""
        self._stream_full_text = ""
        self._recent_tool_results.clear()
        self._sdk_session_id = None
        self._log_ai("session cleared", emit_notice=emit_notice)
        if emit_notice:
            self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="session memory cleared"))

    def ensure_ai_default_mode(self, emit_notice: bool = False) -> bool:
        disabled = os.getenv("PYMOL_AI_DISABLE", "").strip() == "1"
        has_key = bool(self._api_key)
        self.input_mode = "ai"
        self.enabled = has_key and not disabled
        self._log_ai(
            "ensure default ai mode",
            enabled=self.enabled,
            has_key=has_key,
            disabled=disabled,
        )
        if emit_notice and self.enabled:
            self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="AI enabled"))
        return self.enabled

    def export_session_state(self) -> Dict[str, object]:
        return {
            "input_mode": "cli" if self.input_mode == "cli" else "ai",
            "history": list(self.history[-80:]),
            "backend": self._agent_backend,
            "sdk_session_id": self._sdk_session_id,
            "model_info": {
                "model": self.model,
                "enabled": bool(self.enabled),
                "reasoning_visible": bool(self.reasoning_visible),
                "final_answer_enabled": bool(self.final_answer_enabled),
            },
        }

    def import_session_state(self, state: Optional[Dict[str, object]], apply_model: bool = False) -> None:
        payload = dict(state or {})
        self._stream_line_buffer = ""
        self._stream_full_text = ""

        mode = "cli" if str(payload.get("input_mode") or "").lower() == "cli" else "ai"
        self.input_mode = mode
        self._agent_backend = str(payload.get("backend") or "claude_sdk")
        session_id = str(payload.get("sdk_session_id") or "").strip()
        self._sdk_session_id = session_id or None

        history = payload.get("history") or []
        if isinstance(history, list):
            self.history = list(history[-80:])
        else:
            self.history = []

        if apply_model:
            model_info = payload.get("model_info") or {}
            if isinstance(model_info, dict):
                model = str(model_info.get("model") or "").strip()
                if model:
                    self.model = model
                if "enabled" in model_info:
                    self.enabled = bool(model_info.get("enabled"))
                if "reasoning_visible" in model_info:
                    self.reasoning_visible = bool(model_info.get("reasoning_visible"))
        self._log_ai(
            "session state imported",
            apply_model=apply_model,
            input_mode=self.input_mode,
            history_len=len(self.history),
            sdk_session_id=bool(self._sdk_session_id),
            enabled=self.enabled,
        )

    def emit_ui_event(self, event: UiEvent) -> None:
        if event.role == UiRole.SYSTEM and self._is_internal_system_reminder(event.text):
            return

        with self._event_lock:
            self._ui_events.append(event)
            self._compact_ui_events_locked()

        if self._ui_mode != "qt":
            prefix = {
                UiRole.USER: "USER>",
                UiRole.AI: "AI>",
                UiRole.TOOL_START: "TOOL>",
                UiRole.TOOL_RESULT: "TOOL>",
                UiRole.SYSTEM: "SYS>",
                UiRole.REASONING: "RZN>",
                UiRole.ERROR: "ERR>",
            }.get(event.role, "AI>")
            print("%s %s" % (prefix, event.text))

    @staticmethod
    def _is_internal_system_reminder(text: str) -> bool:
        msg = str(text or "")
        return any(msg.startswith(prefix) for prefix in _HIDDEN_SYSTEM_PREFIXES)

    def _compact_ui_events_locked(self) -> None:
        if self.ui_max_events <= 0:
            return
        if len(self._ui_events) <= self.ui_max_events:
            return

        def drop_index():
            for i, evt in enumerate(self._ui_events):
                if evt.role in (UiRole.REASONING, UiRole.SYSTEM):
                    if "compacted to keep UI responsive" in str(evt.text or ""):
                        continue
                    return i
            return 0

        while len(self._ui_events) > self.ui_max_events:
            self._ui_events.pop(drop_index())

        if not self._ui_compaction_notice_sent:
            if len(self._ui_events) >= self.ui_max_events:
                self._ui_events.pop(drop_index())
            self._ui_events.append(
                UiEvent(
                    role=UiRole.SYSTEM,
                    text="chat output compacted to keep UI responsive",
                )
            )
            self._ui_compaction_notice_sent = True

    def has_pending_ui_events(self) -> bool:
        with self._event_lock:
            return bool(self._ui_events)

    def drain_ui_events(self, limit: Optional[int] = None) -> List[UiEvent]:
        with self._event_lock:
            if limit is None:
                out = list(self._ui_events)
                self._ui_events.clear()
            else:
                n = max(0, int(limit))
                out = list(self._ui_events[:n])
                del self._ui_events[:n]
            if not self._ui_events:
                self._ui_compaction_notice_sent = False
        return out

    def handle_typed_input(self, text: str) -> bool:
        raw = text.rstrip("\n")
        stripped = raw.strip()
        if not stripped:
            return False

        self._log_ai(
            "input received",
            input_mode=self.input_mode,
            enabled=self.enabled,
            busy=self._busy,
            text=stripped,
        )
        self.emit_ui_event(UiEvent(role=UiRole.USER, text=stripped))

        if stripped.startswith("/cli"):
            self._handle_cli_control(raw)
            return True

        if stripped.startswith("/ai"):
            self._handle_ai_control(stripped)
            return True

        if self.input_mode == "cli":
            self._log_ai("routing to CLI execution", command=raw)
            self._execute_cli_command(raw)
            return True

        if not self.enabled:
            self._log_ai("ai request rejected: disabled", level="WARNING", text=raw)
            self.emit_ui_event(
                UiEvent(
                    role=UiRole.ERROR,
                    text="AI disabled. Use /ai on, or /cli to switch to command mode",
                )
            )
            return True

        self._start_agent_request(raw)
        return True

    def _run_in_gui(self, fn):
        call = getattr(self.cmd, "_call_in_gui_thread", None)
        if callable(call):
            return call(fn)
        return fn()

    def _handle_cli_control(self, command: str) -> None:
        rest = command[len("/cli") :].strip()

        if not rest or rest == "on":
            self.input_mode = "cli"
            self._log_ai("cli mode enabled")
            self.emit_ui_event(
                UiEvent(role=UiRole.SYSTEM, text="CLI mode enabled. Commands are executed directly")
            )
            return

        if rest == "off":
            self.input_mode = "ai"
            self._log_ai("cli mode disabled; ai mode selected")
            self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="AI mode enabled"))
            return

        if rest == "help":
            self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="/cli | /cli off | /cli <pymol command>"))
            return

        self._execute_cli_command(rest)

    def _enable_ai(self) -> bool:
        if not self._api_key:
            self.enabled = False
            self._log_ai("failed to enable AI: missing API key", level="ERROR")
            self.emit_ui_event(
                UiEvent(
                    role=UiRole.ERROR,
                    text="OPENROUTER_API_KEY (or ANTHROPIC_AUTH_TOKEN) is not set. Export it and retry /ai on",
                )
            )
            return False
        if os.getenv("PYMOL_AI_DISABLE", "").strip() == "1":
            self.enabled = False
            self._log_ai("failed to enable AI: PYMOL_AI_DISABLE=1", level="ERROR")
            self.emit_ui_event(UiEvent(role=UiRole.ERROR, text="PYMOL_AI_DISABLE=1 is set. Unset it to enable AI"))
            return False
        self.enabled = True
        self.input_mode = "ai"
        self._log_ai("ai enabled", model=self.model)
        self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="AI enabled"))
        return True

    def _handle_ai_control(self, command: str) -> None:
        parts = command.split()

        if len(parts) == 1:
            self._enable_ai()
            return

        if parts[1].lower() == "help":
            self.emit_ui_event(
                UiEvent(
                    role=UiRole.SYSTEM,
                    text="/ai (same as /ai on) | /ai on | /ai off | /ai model <id> | /ai clear | /ai help",
                )
            )
            return

        action = parts[1].lower()

        if action == "on":
            self._enable_ai()
            return

        if action == "off":
            self.enabled = False
            self._log_ai("ai disabled via /ai off")
            self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="AI disabled"))
            return

        if action == "model":
            if len(parts) < 3:
                self.emit_ui_event(UiEvent(role=UiRole.ERROR, text="usage: /ai model <openrouter_model_id>"))
                return
            self.model = parts[2]
            self._log_ai("ai model changed", model=self.model)
            self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="model set to %s" % (self.model,)))
            return

        if action == "clear":
            self.clear_session(emit_notice=True)
            return

        self.emit_ui_event(UiEvent(role=UiRole.ERROR, text="unknown /ai command. Try /ai help"))

    def _start_agent_request(self, prompt: str) -> None:
        with self._lock:
            if self._busy:
                self._log_ai("request skipped because worker is busy", level="WARNING")
                self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="request already in progress"))
                return
            self._busy = True
            self._cancel_event.clear()
        self._log_ai("starting ai worker", prompt=prompt, resume_session_id=self._sdk_session_id or "")

        thread = threading.Thread(
            target=self._agent_worker,
            kwargs={"prompt": prompt},
            name="pymol-ai-agent",
            daemon=True,
        )
        thread.start()

    def _append_history(self, message: Dict[str, object]) -> None:
        self.history.append(message)
        if len(self.history) > 80:
            self.history = self.history[-80:]

    def _state_summary_for_prompt(self) -> Dict[str, object]:
        return self._run_in_gui(
            lambda: build_viewer_state_snapshot(
                self.cmd,
                max_objects=self.state_max_objects,
                max_selections=self.state_max_selections,
                recent_tool_results=self._recent_tool_results,
            )
        )

    def _build_turn_prompt(self, prompt: str, *, include_history_context: bool) -> str:
        state_summary = self._state_summary_for_prompt()
        lines = [
            "Current viewer state (compact JSON):",
            json.dumps(state_summary, ensure_ascii=False),
        ]

        if include_history_context:
            lines.append("")
            lines.append("Conversation context:")
            for msg in self.history[-40:]:
                role = str(msg.get("role") or "").strip()
                if role not in ("user", "assistant", "system"):
                    continue
                content = str(msg.get("content") or "").strip()
                if not content:
                    continue
                lines.append("%s: %s" % (role, content[:500]))

        lines.append("")
        lines.append("User request:")
        lines.append(str(prompt or ""))
        return "\n".join(lines)

    def _on_assistant_chunk(self, chunk: str) -> None:
        if self._cancel_event.is_set():
            return
        piece = str(chunk or "")
        if not piece:
            return
        self._stream_had_output = True
        self._stream_full_text += piece
        if self.trace_stream_chunks:
            self._log_ai(
                "stream chunk",
                level="DEBUG",
                chars=len(piece),
                preview=piece[:120],
            )
        self.emit_ui_event(UiEvent(role=UiRole.AI, text=piece, metadata={"stream_chunk": True}))

    def _flush_assistant_chunks(self) -> None:
        if self._stream_line_buffer.strip():
            self.emit_ui_event(UiEvent(role=UiRole.AI, text=self._stream_line_buffer.strip()))
        self._stream_line_buffer = ""

    def _canonicalize_command(self, command: str):
        stripped = str(command or "").strip()
        low = stripped.lower()
        if low.startswith("load "):
            arg = stripped[5:].strip()
            if _RE_PDB_ID.match(arg) and "." not in arg and "/" not in arg and "\\" not in arg:
                return "fetch %s" % (arg,), "translated load %s -> fetch %s" % (arg, arg)
        return stripped, None

    def _is_state_changing_command(self, command: str) -> bool:
        text = str(command or "").strip().lower()
        if not text:
            return False
        first = text.split(None, 1)[0]
        if first.startswith(_READ_ONLY_PREFIXES):
            return False
        return True

    def _remember_tool_result(self, command: str, ok: bool, error: str) -> None:
        self._recent_tool_results.append(
            {
                "command": command,
                "ok": ok,
                "error": error[:240] if error else "",
            }
        )
        if len(self._recent_tool_results) > 20:
            self._recent_tool_results = self._recent_tool_results[-20:]

    def _execute_cli_command(self, command: str) -> None:
        fixed, note = self._canonicalize_command(command)
        if note:
            self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text=note))

        self._log_ai("executing cli command", command=fixed)
        self._append_history({"role": "user", "content": "CLI command: %s" % (fixed,)})
        result = self._run_in_gui(lambda c=fixed: run_pymol_command(self.cmd, c))
        self._log_ai(
            "cli command finished",
            command=result.command,
            ok=result.ok,
            error=result.error or "",
            feedback_lines=len(result.feedback_lines or []),
        )
        self._remember_tool_result(result.command, result.ok, result.error)
        payload = {
            "ok": result.ok,
            "command": result.command,
            "error": result.error or None,
            "feedback_lines": result.feedback_lines,
        }
        self.emit_ui_event(
            UiEvent(
                role=UiRole.TOOL_RESULT,
                text="Executed: %s" % (fixed,),
                ok=result.ok,
                metadata={
                    "tool_call_id": "cli:%s" % (self._normalized_command_key(fixed) or "command",),
                    "tool_name": "run_pymol_command",
                    "tool_args": {"command": fixed},
                    "tool_command": fixed,
                    "tool_result_json": self._tool_result_metadata_payload(payload),
                },
            )
        )

    def _tool_result_content(self, payload: Dict[str, object]) -> str:
        return json.dumps(payload, ensure_ascii=False)

    def _tool_result_metadata_payload(self, payload: Dict[str, object]) -> object:
        serialized = self._tool_result_content(payload)
        if len(serialized) <= self.tool_result_max_chars:
            return payload
        return {
            "truncated": True,
            "preview": serialized[: self.tool_result_max_chars] + "... [truncated]",
        }

    @staticmethod
    def _normalized_command_key(command: str) -> str:
        return re.sub(r"\s+", " ", str(command or "").strip().lower())

    def _execute_snapshot_tool(self) -> Tuple[Dict[str, object], Optional[str], Dict[str, object]]:
        capture = self._run_in_gui(
            lambda: capture_viewer_snapshot(
                self.cmd,
                width=self.screenshot_width,
                height=self.screenshot_height,
            )
        )

        state_summary = self._state_summary_for_prompt()
        image_data_url = capture.get("image_data_url") if capture.get("ok") else None

        payload = {
            "ok": bool(capture.get("ok")),
            "error": capture.get("error"),
            "meta": capture.get("meta", {}),
            "state_summary": state_summary,
            "used_screenshot": bool(capture.get("ok")),
        }

        return payload, image_data_url, state_summary

    def _agent_worker(self, prompt: str) -> None:
        cancelled = False

        def is_cancelled() -> bool:
            return self._cancel_event.is_set()

        def check_cancel() -> bool:
            nonlocal cancelled
            if not is_cancelled():
                return False
            if not cancelled:
                self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="request cancelled"))
                cancelled = True
            return True

        try:
            self._log_ai("agent turn started", prompt=prompt)
            if check_cancel():
                return

            self._append_history({"role": "user", "content": prompt})
            self._stream_had_output = False
            self._stream_line_buffer = ""
            self._stream_full_text = ""

            pending_validation_required = False
            validation_done_this_turn = False
            slow_tool_notice_emitted = False
            snapshot_state_summary: Optional[Dict[str, object]] = None

            def maybe_emit_slow_tool_warning(elapsed: float) -> None:
                nonlocal slow_tool_notice_emitted
                if self.long_tool_warn_sec < 0:
                    return
                if elapsed < self.long_tool_warn_sec:
                    return
                if slow_tool_notice_emitted:
                    return
                self.emit_ui_event(
                    UiEvent(
                        role=UiRole.SYSTEM,
                        text=(
                            "tool step took %.1fs; UI may be busy during heavy PyMOL operations"
                            % (elapsed,)
                        ),
                    )
                )
                self._log_ai("slow tool warning emitted", elapsed="%.3f" % (elapsed,))
                slow_tool_notice_emitted = True

            def execute_run_command_tool(tool_call_id: str, tool_args: Dict[str, object]) -> Dict[str, object]:
                nonlocal pending_validation_required
                command = str(tool_args.get("command") or "").strip()
                command, note = self._canonicalize_command(command)
                if note:
                    self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text=note))

                self._log_ai("tool run start", tool_call_id=tool_call_id, command=command)
                started = time.monotonic()
                exec_result = self._run_in_gui(lambda c=command: run_pymol_command(self.cmd, c))
                elapsed = time.monotonic() - started
                maybe_emit_slow_tool_warning(elapsed)
                self._log_ai(
                    "tool run done",
                    tool_call_id=tool_call_id,
                    command=exec_result.command,
                    ok=exec_result.ok,
                    elapsed="%.3f" % (elapsed,),
                    error=exec_result.error or "",
                )

                self._remember_tool_result(exec_result.command, exec_result.ok, exec_result.error)
                payload = {
                    "ok": exec_result.ok,
                    "command": exec_result.command,
                    "error": exec_result.error or None,
                    "feedback_lines": exec_result.feedback_lines,
                }
                metadata = {
                    "tool_call_id": tool_call_id,
                    "tool_name": "run_pymol_command",
                    "tool_args": dict(tool_args or {}),
                    "tool_command": command,
                    "tool_result_json": self._tool_result_metadata_payload(payload),
                }
                self.emit_ui_event(
                    UiEvent(
                        role=UiRole.TOOL_RESULT,
                        text="Executed: %s" % (command,),
                        ok=bool(payload.get("ok")),
                        metadata=metadata,
                    )
                )

                msg_content = self._tool_result_content(payload)
                if len(msg_content) > self.tool_result_max_chars:
                    msg_content = msg_content[: self.tool_result_max_chars] + "... [truncated]"
                self._append_history(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": "run_pymol_command",
                        "content": msg_content,
                    }
                )

                if self._is_state_changing_command(str(payload.get("command") or command)):
                    pending_validation_required = True

                return payload

            def execute_snapshot_tool(tool_call_id: str, tool_args: Dict[str, object]) -> Dict[str, object]:
                nonlocal pending_validation_required, validation_done_this_turn, snapshot_state_summary
                self._log_ai("snapshot tool start", tool_call_id=tool_call_id, args=tool_args)
                started = time.monotonic()
                payload, image_data_url, state_summary = self._execute_snapshot_tool()
                elapsed = time.monotonic() - started
                maybe_emit_slow_tool_warning(elapsed)
                self._log_ai(
                    "snapshot tool done",
                    tool_call_id=tool_call_id,
                    ok=bool(payload.get("ok")),
                    elapsed="%.3f" % (elapsed,),
                    error=payload.get("error") or "",
                )

                snapshot_state_summary = state_summary
                metadata = {
                    "tool_call_id": tool_call_id,
                    "tool_name": "capture_viewer_snapshot",
                    "tool_args": dict(tool_args or {}),
                    "tool_command": None,
                    "tool_result_json": self._tool_result_metadata_payload(payload),
                }
                if payload["ok"]:
                    metadata["visual_validation"] = "validated: screenshot+state"
                else:
                    metadata["visual_validation"] = "validated: state-only (screenshot failed)"

                self.emit_ui_event(
                    UiEvent(
                        role=UiRole.TOOL_RESULT,
                        text="Executed: capture_viewer_snapshot",
                        ok=bool(payload.get("ok")),
                        metadata=metadata,
                    )
                )

                self._append_history(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": "capture_viewer_snapshot",
                        "content": self._tool_result_content(payload),
                    }
                )

                validation_done_this_turn = True
                pending_validation_required = False
                return {
                    "payload": payload,
                    "image_data_url": image_data_url,
                }

            def run_sdk_turn(request_prompt: str, resume_session_id: Optional[str]):
                return self._sdk_loop.run_turn(
                    prompt=request_prompt,
                    model=self.model,
                    system_prompt=SYSTEM_PROMPT,
                    max_turns=self.max_agent_steps,
                    resume_session_id=resume_session_id,
                    on_text_chunk=self._on_assistant_chunk,
                    on_reasoning_chunk=(
                        (lambda t: self.reasoning_visible and self.emit_ui_event(UiEvent(role=UiRole.REASONING, text=t)))
                    ),
                    should_cancel=is_cancelled,
                    run_command_tool=execute_run_command_tool,
                    snapshot_tool=execute_snapshot_tool,
                    max_buffer_size=self.sdk_max_buffer_size or None,
                )

            turn_prompt = self._build_turn_prompt(prompt, include_history_context=False)
            self._log_ai(
                "sdk turn run",
                include_history_context=False,
                resume_session_id=self._sdk_session_id or "",
                max_turns=self.max_agent_steps,
                model=self.model,
            )
            result = run_sdk_turn(turn_prompt, self._sdk_session_id)

            if (
                result.error_class == "resume_invalid"
                and self._sdk_session_id
                and not check_cancel()
            ):
                self._sdk_session_id = None
                turn_prompt = self._build_turn_prompt(prompt, include_history_context=True)
                self._log_ai("sdk resume invalid; retrying with local history context")
                result = run_sdk_turn(turn_prompt, None)

            if check_cancel():
                return

            self._flush_assistant_chunks()
            self._sdk_session_id = result.session_id or self._sdk_session_id
            self._log_ai(
                "sdk turn completed",
                error_class=result.error_class or "",
                has_error=bool(result.error),
                session_id=self._sdk_session_id or "",
            )

            if result.error:
                if result.error_class == "cancelled":
                    check_cancel()
                    return
                self._log_ai("sdk turn failed", level="ERROR", error=result.error, error_class=result.error_class or "")
                self.emit_ui_event(UiEvent(role=UiRole.ERROR, text=str(result.error)))
                return

            if self.screenshot_validate_required and pending_validation_required and not validation_done_this_turn:
                execute_snapshot_tool("auto_capture_viewer_snapshot_1", {"purpose": "auto_validation"})

            assistant_text = str(result.assistant_text or "").strip()
            if assistant_text:
                self._log_ai("assistant final text emitted", chars=len(assistant_text))
                if not self._stream_had_output:
                    self.emit_ui_event(UiEvent(role=UiRole.AI, text=assistant_text))
                self._append_history({"role": "assistant", "content": assistant_text})
            elif self._stream_had_output and self._stream_full_text.strip():
                streamed_text = self._stream_full_text.strip()
                self._log_ai("assistant final text inferred from streamed chunks", chars=len(streamed_text))
                self._append_history({"role": "assistant", "content": streamed_text})
            elif self.final_answer_enabled:
                self._log_ai("missing final assistant answer from sdk", level="ERROR")
                self.emit_ui_event(
                    UiEvent(
                        role=UiRole.ERROR,
                        text="I completed the loop but did not receive a final answer from the model.",
                    )
                )
        except Exception as exc:  # noqa: BLE001
            self._log_ai("unexpected runtime exception", level="ERROR", error=exc)
            self.emit_ui_event(UiEvent(role=UiRole.ERROR, text="unexpected error: %s" % (exc,)))
        finally:
            with self._lock:
                self._busy = False
            self._cancel_event.clear()
            self._log_ai("agent turn finished", cancelled=cancelled)


def get_ai_runtime(cmd, create: bool = True) -> Optional[AiRuntime]:
    pymol_state = getattr(cmd, "_pymol", None)
    if pymol_state is None:
        return None

    runtime = getattr(pymol_state, "ai_runtime", None)
    if runtime is None and create:
        runtime = AiRuntime(cmd)
        setattr(pymol_state, "ai_runtime", runtime)

    return runtime
