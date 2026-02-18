from __future__ import annotations

import os
import re
import threading
from typing import Dict, List, Optional

from .openrouter_client import (
    DEFAULT_MODEL,
    OpenRouterClient,
    OpenRouterClientError,
    PLAN_JSON_END,
    PLAN_JSON_START,
    PlanParseError,
)
from .protocol import AiPlan
from .safety import PlanValidationError, validate_plan

SYSTEM_PROMPT = """You are PyMOL AI assistant.
Return concise output and end with one machine-parseable JSON block between tags.
Only propose native PyMOL command-line commands compatible with cmd.do parser.
Never propose shell commands or filesystem tooling outside native PyMOL commands.
Limit to at most 10 commands.
Prefer continuing from current session state instead of repeating fetch/load if not needed.

Output format:
Assistant text...
<PLAN_JSON>
{
  \"summary\": \"brief intent summary\",
  \"commands\": [\"command 1\", \"command 2\"],
  \"warnings\": [\"optional warning\"],
  \"reasoning\": \"short rationale\"
}
</PLAN_JSON>
"""

_RE_PDB_ID = re.compile(r"^[0-9][A-Za-z0-9]{3}$")


class AiRuntime:
    def __init__(self, cmd):
        self.cmd = cmd
        self.history: List[Dict[str, str]] = []
        self.model = os.getenv("PYMOL_AI_DEFAULT_MODEL") or DEFAULT_MODEL
        self.reasoning_visible = False
        self.input_mode = "ai"
        self.max_auto_repairs = int(os.getenv("PYMOL_AI_MAX_REPAIRS", "4"))
        self.final_answer_enabled = os.getenv("PYMOL_AI_FINAL_ANSWER", "1") != "0"

        self._busy = False
        self._lock = threading.Lock()

        self._line_buffer = ""
        self._raw_stream_buffer = ""
        self._in_plan_json = False

        disabled = os.getenv("PYMOL_AI_DISABLE", "").strip() == "1"
        self.enabled = bool(self._api_key) and not disabled

        self._client: Optional[OpenRouterClient] = None

    @property
    def _api_key(self) -> str:
        return os.getenv("OPENROUTER_API_KEY", "").strip()

    def is_pending(self) -> bool:
        return False

    def set_reasoning_visible(self, visible: bool) -> None:
        self.reasoning_visible = bool(visible)

    def handle_typed_input(self, text: str) -> bool:
        raw = text.rstrip("\n")
        stripped = raw.strip()

        if not stripped:
            return False

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
            self._emit("AI> disabled. Use /ai on, or /cli to switch to command mode")
            return True

        self._start_plan_request(raw)
        return True

    def _emit(self, message: str) -> None:
        def _do_emit():
            print(message)

        self._run_in_gui(_do_emit)

    def _emit_chunk(self, chunk: str) -> None:
        self._run_in_gui(lambda: self._consume_stream_text(chunk))

    def _flush_chunks(self) -> None:
        def _do_flush():
            if self._raw_stream_buffer and not self._in_plan_json:
                self._line_buffer += self._raw_stream_buffer
            self._raw_stream_buffer = ""
            if self._line_buffer.strip():
                print("AI> " + self._line_buffer.strip())
            self._line_buffer = ""
            self._in_plan_json = False

        self._run_in_gui(_do_flush)

    def _consume_stream_text(self, chunk: str) -> None:
        self._raw_stream_buffer += chunk

        while self._raw_stream_buffer:
            if self._in_plan_json:
                end = self._raw_stream_buffer.find(PLAN_JSON_END)
                if end < 0:
                    keep = max(0, len(PLAN_JSON_END) - 1)
                    if len(self._raw_stream_buffer) > keep:
                        self._raw_stream_buffer = self._raw_stream_buffer[-keep:]
                    return
                self._raw_stream_buffer = self._raw_stream_buffer[end + len(PLAN_JSON_END) :]
                self._in_plan_json = False
                continue

            start = self._raw_stream_buffer.find(PLAN_JSON_START)
            if start < 0:
                keep = max(0, len(PLAN_JSON_START) - 1)
                if len(self._raw_stream_buffer) <= keep:
                    return
                emit_text = self._raw_stream_buffer[:-keep]
                self._raw_stream_buffer = self._raw_stream_buffer[-keep:]
            else:
                emit_text = self._raw_stream_buffer[:start]
                self._raw_stream_buffer = self._raw_stream_buffer[start + len(PLAN_JSON_START) :]
                self._in_plan_json = True

            if emit_text:
                self._append_stream_lines(emit_text)

    def _append_stream_lines(self, text: str) -> None:
        self._line_buffer += text
        while "\n" in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split("\n", 1)
            if line.strip():
                print("AI> " + line.rstrip())

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
            self._emit("AI> CLI mode enabled. Commands are now executed directly")
            return

        if rest == "off":
            self.input_mode = "ai"
            self._emit("AI> AI mode enabled")
            return

        if rest == "help":
            self._emit("AI> /cli | /cli off | /cli <pymol command>")
            return

        self._emit("AI> [CLI one-off] %s" % (rest,))
        self._execute_cli_command(rest)

    def _handle_ai_control(self, command: str) -> None:
        parts = command.split()

        if len(parts) == 1:
            self.input_mode = "ai"
            self._emit("AI> AI mode enabled")
            return

        if parts[1].lower() == "help":
            self._emit(
                "AI> /ai (switch to AI mode) | /ai on | /ai off | /ai model <id> | /ai clear | /ai help"
            )
            return

        action = parts[1].lower()

        if action == "on":
            if not self._api_key:
                self.enabled = False
                self._emit("AI> OPENROUTER_API_KEY is not set. Export it and retry /ai on")
                return
            if os.getenv("PYMOL_AI_DISABLE", "").strip() == "1":
                self.enabled = False
                self._emit("AI> PYMOL_AI_DISABLE=1 is set. Unset it to enable AI")
                return
            self.enabled = True
            self.input_mode = "ai"
            self._emit("AI> enabled")
            return

        if action == "off":
            self.enabled = False
            self._emit("AI> disabled")
            return

        if action == "model":
            if len(parts) < 3:
                self._emit("AI> usage: /ai model <openrouter_model_id>")
                return
            self.model = parts[2]
            self._emit("AI> model set to %s" % (self.model,))
            return

        if action == "clear":
            self.history.clear()
            self._line_buffer = ""
            self._raw_stream_buffer = ""
            self._in_plan_json = False
            self._emit("AI> session memory cleared")
            return

        self._emit("AI> unknown /ai command. Try /ai help")

    def _start_plan_request(self, prompt: str) -> None:
        with self._lock:
            if self._busy:
                self._emit("AI> request already in progress")
                return
            self._busy = True

        self._emit("AI> planning...")
        thread = threading.Thread(
            target=self._plan_worker,
            kwargs={"prompt": prompt},
            name="pymol-ai-plan",
            daemon=True,
        )
        thread.start()

    def _make_messages(self, prompt: str) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self.history[-20:])
        messages.append({"role": "user", "content": prompt})
        return messages

    def _append_history(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})
        if len(self.history) > 40:
            self.history = self.history[-40:]

    def _plan_worker(self, prompt: str) -> None:
        try:
            client = self._client_or_error()
            plan = client.stream_plan(
                model=self.model,
                messages=self._make_messages(prompt),
                on_chunk=self._emit_chunk,
            )
            self._flush_chunks()
            self._handle_plan_success(prompt, plan)
        except (OpenRouterClientError, PlanParseError, PlanValidationError) as exc:
            self._flush_chunks()
            self._emit("AI> failed: %s" % (exc,))
        except Exception as exc:  # noqa: BLE001
            self._flush_chunks()
            self._emit("AI> unexpected error: %s" % (exc,))
        finally:
            with self._lock:
                self._busy = False

    def _handle_plan_success(self, prompt: str, plan: AiPlan) -> None:
        plan = self._normalize_plan(plan)
        validate_plan(plan)

        self._append_history("user", prompt)
        self._append_history(
            "assistant",
            "%s\n%s" % (plan.summary, "\n".join(plan.commands)),
        )

        self._emit("AI> Preview")
        self._emit("AI> Intent: %s" % (plan.summary,))
        for i, command in enumerate(plan.commands, start=1):
            self._emit("AI> %d. %s" % (i, command))
        for warning in plan.warnings:
            self._emit("AI> Warning: %s" % (warning,))
        if self.reasoning_visible and plan.reasoning:
            self._emit("AI> Reasoning: %s" % (plan.reasoning,))

        self._emit("AI> auto-running plan")
        result = self._execute_until_complete(prompt, plan)
        self._emit_final_answer(prompt, plan, result)

    def _normalize_plan(self, plan: AiPlan) -> AiPlan:
        from pymol import parsing

        normalized: List[str] = []
        for command in plan.commands:
            for line in str(command).splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = parsing.split(line, ";", 1)
                for part in parts:
                    part = part.strip()
                    if part:
                        fixed, _ = self._canonicalize_command(part)
                        normalized.append(fixed)

        if not normalized:
            raise PlanValidationError("plan has no executable commands")

        return AiPlan(
            summary=plan.summary,
            commands=normalized,
            warnings=list(plan.warnings),
            reasoning=plan.reasoning,
        )

    def _canonicalize_command(self, command: str):
        stripped = command.strip()
        low = stripped.lower()
        if low.startswith("load "):
            arg = stripped[5:].strip()
            if (
                _RE_PDB_ID.match(arg)
                and "." not in arg
                and "/" not in arg
                and "\\" not in arg
            ):
                return "fetch %s" % (arg,), "AI> translated load %s -> fetch %s" % (arg, arg)
        return stripped, None

    def _execute_cli_command(self, command: str) -> None:
        fixed, note = self._canonicalize_command(command)
        if note:
            self._emit(note)

        self._append_history("user", "CLI command: %s" % (fixed.strip(),))
        result = self._run_in_gui(lambda c=fixed: self.cmd._parser.parse(c))
        if result != 1:
            self._emit("AI> CLI command failed")

    def _execute_plan(self, plan: AiPlan):
        total = len(plan.commands)
        self._emit("AI> executing %d command(s)..." % (total,))

        completed = 0
        completed_commands: List[str] = []
        for i, command in enumerate(plan.commands, start=1):
            self._emit("AI> [%d/%d] %s" % (i, total, command))
            try:
                result = self._run_in_gui(lambda c=command: self.cmd._parser.parse(c))
            except Exception as exc:  # noqa: BLE001
                self._emit("AI> stopped at step %d: %s" % (i, exc))
                self._emit("AI> completed %d/%d" % (completed, total))
                return {
                    "ok": False,
                    "failed_index": i,
                    "failed_command": command,
                    "error": str(exc),
                    "completed_commands": completed_commands,
                }

            if result != 1:
                self._emit("AI> stopped at step %d due to command failure" % (i,))
                self._emit("AI> completed %d/%d" % (completed, total))
                return {
                    "ok": False,
                    "failed_index": i,
                    "failed_command": command,
                    "error": "parser returned failure",
                    "completed_commands": completed_commands,
                }

            completed += 1
            completed_commands.append(command)

        self._emit("AI> done. completed %d/%d" % (completed, total))
        return {"ok": True, "completed_commands": completed_commands}

    def _execute_until_complete(self, user_prompt: str, plan: AiPlan):
        current = plan
        repair_count = 0

        while True:
            result = self._execute_plan(current)
            if result.get("ok"):
                return result

            if repair_count >= self.max_auto_repairs:
                self._emit(
                    "AI> reached auto-repair limit (%d). Last failure was: %s"
                    % (self.max_auto_repairs, result.get("failed_command", "unknown"))
                )
                return result

            repair_count += 1
            self._emit(
                "AI> repairing plan after failed step %d (attempt %d/%d)..."
                % (
                    result.get("failed_index", -1),
                    repair_count,
                    self.max_auto_repairs,
                )
            )

            repaired = self._repair_plan(user_prompt, current, result)
            if repaired is None:
                result["repair_failed"] = True
                return result

            current = repaired

    def _build_state_snapshot(self) -> str:
        lines: List[str] = []
        try:
            names = self._run_in_gui(lambda: self.cmd.get_names("objects"))
            if names:
                lines.append("Objects: " + ", ".join(names[:10]))
                if len(names) > 10:
                    lines.append("Object count: %d" % (len(names),))
        except Exception:
            pass

        try:
            ligands: List[str] = []

            def _collect_ligands():
                from pymol import stored

                stored._ai_ligands = []
                self.cmd.iterate(
                    "organic",
                    "stored._ai_ligands.append((resn,resi,chain))",
                )
                return stored._ai_ligands

            raw = self._run_in_gui(_collect_ligands) or []
            seen = set()
            for resn, resi, chain in raw:
                key = "%s %s %s" % (resn, resi, chain)
                if key not in seen:
                    seen.add(key)
                    ligands.append(key)
            if ligands:
                lines.append("Ligands (unique resn/resi/chain): " + ", ".join(ligands[:20]))
                if len(ligands) > 20:
                    lines.append("Ligand count (unique): %d" % (len(ligands),))
        except Exception:
            pass

        return "\n".join(lines) if lines else "No snapshot data available."

    def _emit_final_answer(self, user_prompt: str, plan: AiPlan, result: Dict[str, object]) -> None:
        if not self.final_answer_enabled:
            return

        state_snapshot = self._build_state_snapshot()
        completion_text = "completed successfully" if result.get("ok") else "ended with failure"
        fail_cmd = result.get("failed_command", "")
        fail_err = result.get("error", "")

        messages = [
            {
                "role": "system",
                "content": (
                    "You are PyMOL assistant. Provide a direct user-facing answer in plain text. "
                    "Summarize what was found/done, and include failures clearly if any. "
                    "Be concise and practical."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Original request:\n%s\n\n"
                    "Planned intent:\n%s\n\n"
                    "Planned commands:\n%s\n\n"
                    "Execution status: %s\n"
                    "Failed command: %s\n"
                    "Failure signal: %s\n\n"
                    "Current state snapshot:\n%s\n\n"
                    "Now answer the original request directly."
                )
                % (
                    user_prompt,
                    plan.summary,
                    "\n".join(plan.commands),
                    completion_text,
                    fail_cmd or "(none)",
                    fail_err or "(none)",
                    state_snapshot,
                ),
            },
        ]

        try:
            client = self._client_or_error()
            self._emit("AI> answering...")
            answer_buf: List[str] = []

            def _on_chunk(chunk: str):
                answer_buf.append(chunk)

            answer = client.stream_text(model=self.model, messages=messages, on_chunk=_on_chunk)
            answer = answer.strip()
            if answer:
                for line in answer.splitlines():
                    if line.strip():
                        self._emit("AI> " + line.strip())
        except Exception:
            # Keep silent on synthesis failure; execution output is already shown.
            pass

    def _repair_plan(self, user_prompt: str, previous_plan: AiPlan, result: Dict[str, object]):
        failed_command = str(result.get("failed_command", ""))
        error_text = str(result.get("error", "unknown error"))
        completed = list(result.get("completed_commands", []))

        repair_prompt = (
            "The prior command plan failed. Create a corrected continuation plan that completes the user's request "
            "from the current PyMOL session state.\n\n"
            "Original user request:\n"
            "%s\n\n"
            "Previously proposed summary:\n"
            "%s\n\n"
            "Commands that already succeeded (do not repeat unless strictly necessary):\n"
            "%s\n\n"
            "Failed command:\n"
            "%s\n\n"
            "Failure signal:\n"
            "%s\n\n"
            "Requirements:\n"
            "- Avoid the failed command unless rewritten correctly.\n"
            "- Continue from current state; avoid unnecessary fetch/load repeats.\n"
            "- Return at most 10 commands."
        ) % (
            user_prompt,
            previous_plan.summary,
            "\n".join(completed) if completed else "(none)",
            failed_command,
            error_text,
        )

        try:
            client = self._client_or_error()
            repaired = client.stream_plan(
                model=self.model,
                messages=self._make_messages(repair_prompt),
                on_chunk=self._emit_chunk,
            )
            self._flush_chunks()
            repaired = self._normalize_plan(repaired)
            validate_plan(repaired)

            self._append_history("user", repair_prompt)
            self._append_history(
                "assistant",
                "%s\n%s" % (repaired.summary, "\n".join(repaired.commands)),
            )

            self._emit("AI> Repaired plan")
            self._emit("AI> Intent: %s" % (repaired.summary,))
            for i, command in enumerate(repaired.commands, start=1):
                self._emit("AI> %d. %s" % (i, command))

            return repaired
        except (OpenRouterClientError, PlanParseError, PlanValidationError) as exc:
            self._flush_chunks()
            self._emit("AI> repair planning failed: %s" % (exc,))
            return None
        except Exception as exc:  # noqa: BLE001
            self._flush_chunks()
            self._emit("AI> unexpected repair error: %s" % (exc,))
            return None


def get_ai_runtime(cmd, create: bool = True) -> Optional[AiRuntime]:
    pymol_state = getattr(cmd, "_pymol", None)
    if pymol_state is None:
        return None

    runtime = getattr(pymol_state, "ai_runtime", None)
    if runtime is None and create:
        runtime = AiRuntime(cmd)
        setattr(pymol_state, "ai_runtime", runtime)

    return runtime
