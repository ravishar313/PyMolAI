from __future__ import annotations

import json
import os
import re
import threading
from typing import Dict, List, Optional, Tuple

from .doom_loop_detector import DoomLoopDetector
from .message_types import ToolCall, UiEvent, UiRole
from .openrouter_client import (
    DEFAULT_MODEL,
    OpenRouterClient,
    OpenRouterClientError,
    build_multimodal_user_content,
)
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
    "DOOM LOOP DETECTED:",
)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


class AiRuntime:
    def __init__(self, cmd):
        self.cmd = cmd
        self.history: List[Dict[str, object]] = []
        self.model = os.getenv("PYMOL_AI_DEFAULT_MODEL") or DEFAULT_MODEL
        self.reasoning_visible = False
        self.input_mode = "ai"
        self.final_answer_enabled = os.getenv("PYMOL_AI_FINAL_ANSWER", "1") != "0"

        self.max_agent_steps = _env_int("PYMOL_AI_MAX_STEPS", 16)
        self.max_auto_repairs = _env_int("PYMOL_AI_MAX_REPAIRS", 4)
        self.tool_result_max_chars = _env_int("PYMOL_AI_TOOL_RESULT_MAX_CHARS", 4096)
        self.doom_loop_threshold = _env_int("PYMOL_AI_DOOM_LOOP_THRESHOLD", 3)

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

        self._stream_line_buffer = ""
        self._stream_had_output = False

        disabled = os.getenv("PYMOL_AI_DISABLE", "").strip() == "1"
        self.enabled = bool(self._api_key) and not disabled

        self._client: Optional[OpenRouterClient] = None
        self._recent_tool_results: List[Dict[str, object]] = []

    @property
    def _api_key(self) -> str:
        return os.getenv("OPENROUTER_API_KEY", "").strip()

    def set_reasoning_visible(self, visible: bool) -> None:
        self.reasoning_visible = bool(visible)

    def set_ui_mode(self, mode: str) -> None:
        self._ui_mode = mode if mode in ("qt", "text") else "text"

    @property
    def current_input_mode(self) -> str:
        return self.input_mode

    def request_cancel(self) -> bool:
        self._cancel_event.set()
        with self._lock:
            busy = bool(self._busy)
        if busy:
            self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="cancellation requested..."))
        return busy

    def clear_session(self, emit_notice: bool = True) -> None:
        self.history.clear()
        self._stream_line_buffer = ""
        self._recent_tool_results.clear()
        if emit_notice:
            self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="session memory cleared"))

    def emit_ui_event(self, event: UiEvent) -> None:
        if event.role == UiRole.SYSTEM and self._is_internal_system_reminder(event.text):
            return

        with self._event_lock:
            self._ui_events.append(event)

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

    def drain_ui_events(self) -> List[UiEvent]:
        with self._event_lock:
            out = list(self._ui_events)
            self._ui_events.clear()
        return out

    def handle_typed_input(self, text: str) -> bool:
        raw = text.rstrip("\n")
        stripped = raw.strip()
        if not stripped:
            return False

        self.emit_ui_event(UiEvent(role=UiRole.USER, text=stripped))

        if stripped.startswith("/cli"):
            self._handle_cli_control(raw)
            return True

        if stripped.startswith("/ai"):
            self._handle_ai_control(stripped)
            return True

        if self.input_mode == "cli":
            self._execute_cli_command(raw)
            return True

        if not self.enabled:
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

    def _client_or_error(self) -> OpenRouterClient:
        if self._client is None:
            self._client = OpenRouterClient(api_key=self._api_key)
        return self._client

    def _handle_cli_control(self, command: str) -> None:
        rest = command[len("/cli") :].strip()

        if not rest or rest == "on":
            self.input_mode = "cli"
            self.emit_ui_event(
                UiEvent(role=UiRole.SYSTEM, text="CLI mode enabled. Commands are executed directly")
            )
            return

        if rest == "off":
            self.input_mode = "ai"
            self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="AI mode enabled"))
            return

        if rest == "help":
            self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="/cli | /cli off | /cli <pymol command>"))
            return

        self._execute_cli_command(rest)

    def _handle_ai_control(self, command: str) -> None:
        parts = command.split()

        if len(parts) == 1:
            self.input_mode = "ai"
            self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="AI mode enabled"))
            return

        if parts[1].lower() == "help":
            self.emit_ui_event(
                UiEvent(
                    role=UiRole.SYSTEM,
                    text="/ai (switch to AI mode) | /ai on | /ai off | /ai model <id> | /ai clear | /ai help",
                )
            )
            return

        action = parts[1].lower()

        if action == "on":
            if not self._api_key:
                self.enabled = False
                self.emit_ui_event(
                    UiEvent(role=UiRole.ERROR, text="OPENROUTER_API_KEY is not set. Export it and retry /ai on")
                )
                return
            if os.getenv("PYMOL_AI_DISABLE", "").strip() == "1":
                self.enabled = False
                self.emit_ui_event(UiEvent(role=UiRole.ERROR, text="PYMOL_AI_DISABLE=1 is set. Unset it to enable AI"))
                return
            self.enabled = True
            self.input_mode = "ai"
            self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="AI enabled"))
            return

        if action == "off":
            self.enabled = False
            self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="AI disabled"))
            return

        if action == "model":
            if len(parts) < 3:
                self.emit_ui_event(UiEvent(role=UiRole.ERROR, text="usage: /ai model <openrouter_model_id>"))
                return
            self.model = parts[2]
            self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="model set to %s" % (self.model,)))
            return

        if action == "clear":
            self.clear_session(emit_notice=True)
            return

        self.emit_ui_event(UiEvent(role=UiRole.ERROR, text="unknown /ai command. Try /ai help"))

    def _start_agent_request(self, prompt: str) -> None:
        with self._lock:
            if self._busy:
                self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="request already in progress"))
                return
            self._busy = True
            self._cancel_event.clear()

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

    def _build_messages(
        self,
        prompt: str,
        *,
        snapshot_image_data_url: Optional[str] = None,
        snapshot_state_summary: Optional[Dict[str, object]] = None,
    ) -> List[Dict[str, object]]:
        state_summary = snapshot_state_summary or self._state_summary_for_prompt()

        msgs: List[Dict[str, object]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "system",
                "content": "Current viewer state (compact JSON):\n%s"
                % (json.dumps(state_summary, ensure_ascii=False),),
            },
        ]
        msgs.extend(self.history[-60:])

        if snapshot_image_data_url:
            msgs.append(
                {
                    "role": "user",
                    "content": build_multimodal_user_content(
                        "Visual validation context for current viewer state.",
                        snapshot_image_data_url,
                    ),
                }
            )

        msgs.append({"role": "user", "content": prompt})
        return msgs

    def _agent_tools(self) -> List[Dict[str, object]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "run_pymol_command",
                    "description": "Run a single PyMOL command in the current session.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                        "required": ["command"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "capture_viewer_snapshot",
                    "description": (
                        "Capture current PyMOL viewport screenshot and compact viewer state summary. "
                        "Use ONLY for internal visual validation before final answer when scene changed. "
                        "Do not claim this screenshot is shown to the user."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "purpose": {"type": "string"},
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def _on_assistant_chunk(self, chunk: str) -> None:
        if self._cancel_event.is_set():
            return
        if chunk:
            self._stream_had_output = True
        self._stream_line_buffer += chunk
        while "\n" in self._stream_line_buffer:
            line, self._stream_line_buffer = self._stream_line_buffer.split("\n", 1)
            if line.strip():
                self.emit_ui_event(UiEvent(role=UiRole.AI, text=line.strip()))

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

        self._append_history({"role": "user", "content": "CLI command: %s" % (fixed,)})
        result = self._run_in_gui(lambda c=fixed: run_pymol_command(self.cmd, c))
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
                text="Ran tool: %s" % (fixed,),
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

    def _assistant_message_with_tools(self, assistant_text: str, tool_calls: List[ToolCall]) -> Dict[str, object]:
        tc_payload = []
        for tc in tool_calls:
            tc_payload.append(
                {
                    "id": tc.tool_call_id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments_json},
                }
            )
        return {"role": "assistant", "content": assistant_text, "tool_calls": tc_payload}

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
        detector = DoomLoopDetector(threshold=self.doom_loop_threshold)

        snapshot_image_data_url: Optional[str] = None
        snapshot_state_summary: Optional[Dict[str, object]] = None
        cancelled = False
        successful_commands_this_turn = set()
        loop_nudged_at_step = 0

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

        def maybe_handle_stall(loop: Dict[str, object], step_index: int) -> bool:
            nonlocal loop_nudged_at_step
            if not loop:
                return False

            if loop_nudged_at_step == 0:
                loop_type = str(loop.get("loop_type") or "stall")
                family = str(loop.get("command_family") or "").strip()
                hidden = (
                    "DOOM LOOP DETECTED: %s%s. Stop repeating the same setup phrasing. "
                    "Switch approach or ask the user to clarify target selection."
                    % (loop_type, (" (%s)" % family) if family else "")
                )
                self._append_history({"role": "system", "content": hidden})
                loop_nudged_at_step = step_index
                return False

            if step_index <= loop_nudged_at_step:
                return False

            stuck = "I'm stuck; please narrow or clarify the target selection."
            self.emit_ui_event(UiEvent(role=UiRole.ERROR, text=stuck))
            self._append_history({"role": "assistant", "content": stuck})
            return True

        try:
            if check_cancel():
                return
            self._append_history({"role": "user", "content": prompt})

            pending_validation_required = False
            validation_done_this_turn = False

            for step in range(1, self.max_agent_steps + 1):
                if check_cancel():
                    return

                messages = self._build_messages(
                    prompt,
                    snapshot_image_data_url=snapshot_image_data_url,
                    snapshot_state_summary=snapshot_state_summary,
                )

                # Ephemeral image: use for immediate call and then clear.
                snapshot_image_data_url = None

                self._stream_had_output = False
                turn = self._client_or_error().stream_assistant_turn(
                    model=self.model,
                    messages=messages,
                    tools=self._agent_tools(),
                    on_text_chunk=self._on_assistant_chunk,
                    on_reasoning_chunk=(
                        (lambda t: self.reasoning_visible and self.emit_ui_event(UiEvent(role=UiRole.REASONING, text=t)))
                    ),
                    should_cancel=is_cancelled,
                )
                if check_cancel():
                    return

                self._flush_assistant_chunks()

                assistant_text = str(turn.get("assistant_text") or "").strip()
                tool_calls = list(turn.get("tool_calls") or [])
                intent_loop = detector.add_assistant_intent(assistant_text)
                if maybe_handle_stall(intent_loop, step):
                    return

                if not tool_calls:
                    if self.screenshot_validate_required and pending_validation_required and not validation_done_this_turn:
                        nudge = (
                            "Validation required: capture_viewer_snapshot must be called before final answer "
                            "because scene-changing commands were executed."
                        )
                        self._append_history({"role": "system", "content": nudge})
                        continue

                    # Avoid duplicate final answer if already streamed.
                    if assistant_text:
                        if not self._stream_had_output:
                            self.emit_ui_event(UiEvent(role=UiRole.AI, text=assistant_text))
                    elif self.final_answer_enabled:
                        self.emit_ui_event(
                            UiEvent(
                                role=UiRole.ERROR,
                                text="I completed the loop but did not receive a final answer from the model.",
                            )
                        )
                    self._append_history({"role": "assistant", "content": assistant_text})
                    return

                self._append_history(self._assistant_message_with_tools(assistant_text, tool_calls))

                validation_done_this_turn = False

                for tc in tool_calls:
                    if check_cancel():
                        return

                    if tc.name == "capture_viewer_snapshot":
                        payload, image_data_url, state_summary = self._execute_snapshot_tool()
                        snapshot_image_data_url = image_data_url
                        snapshot_state_summary = state_summary

                        meta = {
                            "tool_call_id": tc.tool_call_id,
                            "tool_name": tc.name,
                            "tool_args": tc.arguments,
                            "tool_command": None,
                            "tool_result_json": self._tool_result_metadata_payload(payload),
                        }
                        if payload["ok"]:
                            meta["visual_validation"] = "validated: screenshot+state"
                            self.emit_ui_event(
                                UiEvent(
                                    role=UiRole.TOOL_RESULT,
                                    text="Ran tool: capture_viewer_snapshot",
                                    ok=True,
                                    metadata=meta,
                                )
                            )
                        else:
                            meta["visual_validation"] = "validated: state-only (screenshot failed)"
                            self.emit_ui_event(
                                UiEvent(
                                    role=UiRole.TOOL_RESULT,
                                    text="Ran tool: capture_viewer_snapshot",
                                    ok=False,
                                    metadata=meta,
                                )
                            )

                        content = self._tool_result_content(payload)
                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": tc.tool_call_id,
                            "name": tc.name,
                            "content": content,
                        }
                        self._append_history(tool_msg)
                        validation_done_this_turn = True
                        pending_validation_required = False
                        continue

                    if tc.name != "run_pymol_command":
                        payload = {
                            "ok": False,
                            "command": "",
                            "error": "unsupported tool: %s" % (tc.name,),
                            "feedback_lines": [],
                        }
                        err_content = self._tool_result_content(payload)
                        self.emit_ui_event(
                            UiEvent(
                                role=UiRole.TOOL_RESULT,
                                text="Ran tool: %s" % (tc.name,),
                                ok=False,
                                metadata={
                                    "tool_call_id": tc.tool_call_id,
                                    "tool_name": tc.name,
                                    "tool_args": tc.arguments,
                                    "tool_command": None,
                                    "tool_result_json": self._tool_result_metadata_payload(payload),
                                },
                            )
                        )
                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": tc.tool_call_id,
                            "name": tc.name,
                            "content": err_content,
                        }
                        self._append_history(tool_msg)
                        continue

                    command = str(tc.arguments.get("command") or "").strip()
                    command, note = self._canonicalize_command(command)
                    if note:
                        self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text=note))

                    command_key = self._normalized_command_key(command)
                    if command_key in successful_commands_this_turn:
                        payload = {
                            "ok": True,
                            "command": command,
                            "error": None,
                            "feedback_lines": [],
                            "skipped": True,
                            "skip_reason": "duplicate command skipped in current turn",
                        }
                    else:
                        exec_result = self._run_in_gui(lambda c=command: run_pymol_command(self.cmd, c))
                        self._remember_tool_result(exec_result.command, exec_result.ok, exec_result.error)
                        if check_cancel():
                            return
                        payload = {
                            "ok": exec_result.ok,
                            "command": exec_result.command,
                            "error": exec_result.error or None,
                            "feedback_lines": exec_result.feedback_lines,
                        }
                        if exec_result.ok:
                            successful_commands_this_turn.add(command_key)

                    result_text = self._tool_result_content(payload)
                    self.emit_ui_event(
                        UiEvent(
                            role=UiRole.TOOL_RESULT,
                            text="Ran tool: %s" % (command,),
                            ok=bool(payload.get("ok")),
                            metadata={
                                "tool_call_id": tc.tool_call_id,
                                "tool_name": tc.name,
                                "tool_args": tc.arguments,
                                "tool_command": command,
                                "tool_result_json": self._tool_result_metadata_payload(payload),
                            },
                        )
                    )

                    msg_content = result_text
                    if len(msg_content) > self.tool_result_max_chars:
                        msg_content = msg_content[: self.tool_result_max_chars] + "... [truncated]"

                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": tc.tool_call_id,
                        "name": tc.name,
                        "content": msg_content,
                    }
                    self._append_history(tool_msg)

                    if self._is_state_changing_command(str(payload.get("command") or command)):
                        pending_validation_required = True

                    loop = detector.add_call(
                        tc.name,
                        tc.arguments,
                        validation_required=pending_validation_required,
                    )
                    if maybe_handle_stall(loop, step):
                        return

                # If tools executed and validation required but not done, nudge immediately.
                if self.screenshot_validate_required and pending_validation_required and not validation_done_this_turn:
                    nudge = (
                        "Visual validation required now: call capture_viewer_snapshot before final answer."
                    )
                    self._append_history({"role": "system", "content": nudge})

            self.emit_ui_event(
                UiEvent(
                    role=UiRole.ERROR,
                    text=(
                        "Agent reached step limit (%d). Please refine your request or use /cli for direct commands."
                        % (self.max_agent_steps,)
                    ),
                )
            )
        except OpenRouterClientError as exc:
            self.emit_ui_event(UiEvent(role=UiRole.ERROR, text=str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.emit_ui_event(UiEvent(role=UiRole.ERROR, text="unexpected error: %s" % (exc,)))
        finally:
            with self._lock:
                self._busy = False
            self._cancel_event.clear()


def get_ai_runtime(cmd, create: bool = True) -> Optional[AiRuntime]:
    pymol_state = getattr(cmd, "_pymol", None)
    if pymol_state is None:
        return None

    runtime = getattr(pymol_state, "ai_runtime", None)
    if runtime is None and create:
        runtime = AiRuntime(cmd)
        setattr(pymol_state, "ai_runtime", runtime)

    return runtime
