from __future__ import annotations

import json
import os
import re
import threading
from typing import Dict, List, Optional

from .doom_loop_detector import DoomLoopDetector
from .message_types import ToolCall, UiEvent, UiRole
from .openrouter_client import DEFAULT_MODEL, OpenRouterClient, OpenRouterClientError
from .tool_execution import run_pymol_command

SYSTEM_PROMPT = """You are a PyMOL desktop agent.
You can either:
1) call tools to act in PyMOL, or
2) provide a final direct answer without tool calls.

Rules:
- Use tool calls when an action/query in PyMOL is needed.
- If tool results already answer the user, return a concise final answer and DO NOT call tools.
- Do not use shell commands.
- Prefer continuing current session state; avoid redundant fetch/load.
- Keep answers concise and practical.
"""

_RE_PDB_ID = re.compile(r"^[0-9][A-Za-z0-9]{3}$")


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

        self._busy = False
        self._lock = threading.Lock()
        self._event_lock = threading.Lock()
        self._ui_events: List[UiEvent] = []
        self._ui_mode = "text"

        self._stream_line_buffer = ""

        disabled = os.getenv("PYMOL_AI_DISABLE", "").strip() == "1"
        self.enabled = bool(self._api_key) and not disabled

        self._client: Optional[OpenRouterClient] = None

    @property
    def _api_key(self) -> str:
        return os.getenv("OPENROUTER_API_KEY", "").strip()

    def set_reasoning_visible(self, visible: bool) -> None:
        self.reasoning_visible = bool(visible)

    def set_ui_mode(self, mode: str) -> None:
        self._ui_mode = mode if mode in ("qt", "text") else "text"

    def emit_ui_event(self, event: UiEvent) -> None:
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

        self.emit_ui_event(UiEvent(role=UiRole.TOOL_START, text="[CLI one-off] %s" % (rest,)))
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
            self.history.clear()
            self._stream_line_buffer = ""
            self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="session memory cleared"))
            return

        self.emit_ui_event(UiEvent(role=UiRole.ERROR, text="unknown /ai command. Try /ai help"))

    def _start_agent_request(self, prompt: str) -> None:
        with self._lock:
            if self._busy:
                self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="request already in progress"))
                return
            self._busy = True

        self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="planning..."))
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

    def _build_messages(self, prompt: str) -> List[Dict[str, object]]:
        msgs: List[Dict[str, object]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        msgs.extend(self.history[-60:])
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
            }
        ]

    def _on_assistant_chunk(self, chunk: str) -> None:
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

    def _execute_cli_command(self, command: str) -> None:
        fixed, note = self._canonicalize_command(command)
        if note:
            self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text=note))

        self._append_history({"role": "user", "content": "CLI command: %s" % (fixed,)})
        result = self._run_in_gui(lambda c=fixed: run_pymol_command(self.cmd, c))
        if result.ok:
            lines = "\n".join(result.feedback_lines) if result.feedback_lines else "ok"
            self.emit_ui_event(UiEvent(role=UiRole.TOOL_RESULT, text=lines, ok=True))
        else:
            text = result.error
            if result.feedback_lines:
                text = text + "\n" + "\n".join(result.feedback_lines)
            self.emit_ui_event(UiEvent(role=UiRole.TOOL_RESULT, text=text, ok=False))

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

    def _tool_result_content(self, ok: bool, command: str, error: str, feedback_lines: List[str]) -> str:
        payload = {
            "ok": ok,
            "command": command,
            "error": error or None,
            "feedback_lines": feedback_lines,
        }
        return json.dumps(payload, ensure_ascii=False)

    def _agent_worker(self, prompt: str) -> None:
        detector = DoomLoopDetector(threshold=self.doom_loop_threshold)
        messages = self._build_messages(prompt)

        try:
            self._append_history({"role": "user", "content": prompt})

            for step in range(1, self.max_agent_steps + 1):
                turn = self._client_or_error().stream_assistant_turn(
                    model=self.model,
                    messages=messages,
                    tools=self._agent_tools(),
                    on_text_chunk=self._on_assistant_chunk,
                    on_reasoning_chunk=(
                        (lambda t: self.reasoning_visible and self.emit_ui_event(UiEvent(role=UiRole.REASONING, text=t)))
                    ),
                )
                self._flush_assistant_chunks()

                assistant_text = str(turn.get("assistant_text") or "").strip()
                tool_calls = list(turn.get("tool_calls") or [])

                if not tool_calls:
                    if assistant_text:
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
                messages.append(self._assistant_message_with_tools(assistant_text, tool_calls))

                for tc in tool_calls:
                    if tc.name != "run_pymol_command":
                        err_content = self._tool_result_content(
                            ok=False,
                            command="",
                            error="unsupported tool: %s" % (tc.name,),
                            feedback_lines=[],
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.tool_call_id,
                                "name": tc.name,
                                "content": err_content,
                            }
                        )
                        self._append_history(messages[-1])
                        self.emit_ui_event(UiEvent(role=UiRole.TOOL_RESULT, text=err_content, ok=False))
                        continue

                    command = str(tc.arguments.get("command") or "").strip()
                    command, note = self._canonicalize_command(command)
                    if note:
                        self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text=note))

                    self.emit_ui_event(UiEvent(role=UiRole.TOOL_START, text=command))
                    exec_result = self._run_in_gui(lambda c=command: run_pymol_command(self.cmd, c))

                    result_text = self._tool_result_content(
                        ok=exec_result.ok,
                        command=exec_result.command,
                        error=exec_result.error,
                        feedback_lines=exec_result.feedback_lines,
                    )
                    visible_text = result_text
                    if len(visible_text) > self.tool_result_max_chars:
                        visible_text = visible_text[: self.tool_result_max_chars] + "... [truncated]"

                    self.emit_ui_event(
                        UiEvent(role=UiRole.TOOL_RESULT, text=visible_text, ok=exec_result.ok)
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
                    messages.append(tool_msg)
                    self._append_history(tool_msg)

                    loop = detector.add_call(tc.name, tc.arguments)
                    if loop:
                        warning = (
                            "DOOM LOOP DETECTED: tool '%s' repeated %d times with identical arguments. "
                            "Try a different approach and do not repeat the same tool call."
                            % (loop["tool_name"], loop["call_count"])
                        )
                        messages.append({"role": "system", "content": warning})
                        self.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text=warning))

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


def get_ai_runtime(cmd, create: bool = True) -> Optional[AiRuntime]:
    pymol_state = getattr(cmd, "_pymol", None)
    if pymol_state is None:
        return None

    runtime = getattr(pymol_state, "ai_runtime", None)
    if runtime is None and create:
        runtime = AiRuntime(cmd)
        setattr(pymol_state, "ai_runtime", runtime)

    return runtime
