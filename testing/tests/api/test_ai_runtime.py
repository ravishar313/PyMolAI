from types import SimpleNamespace

from pymol.ai.message_types import ToolCall, UiEvent, UiRole
from pymol.ai.runtime import AiRuntime
from pymol.shortcut import Shortcut


class DummyParser:
    def __init__(self):
        self.commands = []

    def parse(self, command):
        self.commands.append(command)
        return 0 if command == "bad_command" else 1


class DummyCmd:
    def __init__(self):
        self.kwhash = Shortcut(["show", "hide", "color", "zoom", "fetch"])
        self._parser = DummyParser()
        self._pymol = SimpleNamespace()
        self._call_in_gui_thread = lambda fn: fn()
        self._snapshot_idx = 0

    def get_names(self, type_name, enabled_only=0):
        if type_name == "objects":
            return ["obj1"] if enabled_only else ["obj1", "obj2"]
        if type_name == "public_selections":
            return ["sel1"]
        return []

    def count_atoms(self, selection):
        return 12

    def get_vis(self):
        return {"obj1": 1}

    def get_view(self, output=0, quiet=1):
        return [0.0] * 18

    def get_viewport(self, output=0, quiet=1):
        return [800, 600]

    def get_object_list(self, selection="(all)", quiet=1):
        return ["obj1"]

    def png(self, path, width=0, height=0, ray=0, quiet=1, prior=0):
        self._snapshot_idx += 1
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n" + bytes([self._snapshot_idx]))


class FakeClient:
    def __init__(self, turns):
        self.turns = list(turns)

    def stream_assistant_turn(self, **kwargs):
        if not self.turns:
            return {"assistant_text": "", "tool_calls": []}
        return self.turns.pop(0)


class FakeClientStreaming:
    def stream_assistant_turn(self, **kwargs):
        on_text_chunk = kwargs.get("on_text_chunk")
        if callable(on_text_chunk):
            on_text_chunk("Done. Ligands are FMN and D59.")
        return {"assistant_text": "Done. Ligands are FMN and D59.", "tool_calls": []}


def _runtime(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("PYMOL_AI_DISABLE", raising=False)
    runtime = AiRuntime(DummyCmd())
    runtime.set_ui_mode("qt")
    return runtime


def _events(runtime):
    return runtime.drain_ui_events()


def test_drain_ui_events_limit_preserves_remainder(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="one"))
    runtime.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="two"))
    runtime.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="three"))

    first = runtime.drain_ui_events(limit=2)
    assert [e.text for e in first] == ["one", "two"]
    assert runtime.has_pending_ui_events()

    second = runtime.drain_ui_events(limit=2)
    assert [e.text for e in second] == ["three"]
    assert not runtime.has_pending_ui_events()


def test_ui_event_queue_compaction_prefers_low_priority_drop(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.ui_max_events = 3

    runtime.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="s1"))
    runtime.emit_ui_event(UiEvent(role=UiRole.REASONING, text="r1"))
    runtime.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="s2"))
    runtime.emit_ui_event(UiEvent(role=UiRole.USER, text="keep user"))
    runtime.emit_ui_event(UiEvent(role=UiRole.ERROR, text="keep error"))

    events = _events(runtime)
    texts = [e.text for e in events]
    assert "keep user" in texts
    assert "keep error" in texts
    assert any("compacted to keep UI responsive" in t for t in texts)


def test_ai_controls_model_clear_and_mode(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.history = [{"role": "user", "content": "hello"}]
    runtime.input_mode = "cli"

    assert runtime.handle_typed_input("/ai model openai/gpt-4o-mini")
    assert runtime.model == "openai/gpt-4o-mini"

    assert runtime.handle_typed_input("/ai")
    assert runtime.input_mode == "ai"

    assert runtime.handle_typed_input("/ai clear")
    assert runtime.history == []


def test_clear_session_api(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.history = [{"role": "user", "content": "hello"}]
    runtime._stream_line_buffer = "partial"
    runtime._recent_tool_results = [{"command": "zoom", "ok": True, "error": ""}]

    runtime.clear_session(emit_notice=False)
    assert runtime.history == []
    assert runtime._stream_line_buffer == ""
    assert runtime._recent_tool_results == []
    assert _events(runtime) == []

    runtime.clear_session(emit_notice=True)
    events = _events(runtime)
    assert any(e.role == UiRole.SYSTEM and "session memory cleared" in e.text for e in events)


def test_cancel_request_stops_worker_cleanly(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.request_cancel()

    runtime._agent_worker("do work")
    events = _events(runtime)
    assert any(e.role == UiRole.SYSTEM and "request cancelled" in e.text for e in events)
    assert not any(e.role == UiRole.ERROR and "unexpected error" in e.text for e in events)


def test_missing_api_key_does_not_enable(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    runtime = AiRuntime(DummyCmd())
    runtime.set_ui_mode("qt")

    runtime.handle_typed_input("/ai on")
    assert not runtime.enabled
    assert any("OPENROUTER_API_KEY is not set" in e.text for e in _events(runtime))


def test_ai_mode_routes_text_to_agent(monkeypatch):
    runtime = _runtime(monkeypatch)
    calls = []
    runtime._start_agent_request = lambda prompt: calls.append(prompt)

    assert runtime.handle_typed_input("show cartoon") is True
    assert calls == ["show cartoon"]


def test_cli_mode_and_one_off(monkeypatch):
    runtime = _runtime(monkeypatch)

    runtime.handle_typed_input("/cli")
    assert runtime.input_mode == "cli"

    runtime.handle_typed_input("zoom")
    assert runtime.cmd._parser.commands == ["zoom"]

    runtime.handle_typed_input("/ai")
    runtime.handle_typed_input("/cli load 1bom")
    assert runtime.cmd._parser.commands[-1] == "fetch 1bom"


def test_agent_no_tool_call_means_final_answer(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime._client = FakeClient([
        {"assistant_text": "Done. Ligands are FMN and D59.", "tool_calls": []}
    ])

    runtime._agent_worker("what ligands are there")

    events = _events(runtime)
    assert any(e.role == UiRole.AI and "Ligands" in e.text for e in events)
    assert runtime.history[-1]["role"] == "assistant"


def test_no_duplicate_ai_message_when_streaming_no_tools(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime._client = FakeClientStreaming()

    runtime._agent_worker("what ligands are there")
    events = _events(runtime)
    ai_lines = [e.text for e in events if e.role == UiRole.AI]
    assert ai_lines.count("Done. Ligands are FMN and D59.") == 1
    assert not any(e.role == UiRole.ERROR for e in events)


def test_agent_tool_call_then_final_answer(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.screenshot_validate_required = False
    runtime._client = FakeClient([
        {
            "assistant_text": "I will zoom.",
            "tool_calls": [
                ToolCall(
                    tool_call_id="call_1",
                    name="run_pymol_command",
                    arguments={"command": "zoom"},
                    arguments_json='{"command":"zoom"}',
                )
            ],
        },
        {"assistant_text": "Zoom complete.", "tool_calls": []},
    ])

    runtime._agent_worker("zoom in")

    assert runtime.cmd._parser.commands == ["zoom"]
    assert any(m.get("role") == "tool" for m in runtime.history)
    events = _events(runtime)
    tool_events = [e for e in events if e.role == UiRole.TOOL_RESULT]
    assert tool_events
    meta = tool_events[0].metadata
    assert "tool_call_id" in meta
    assert "tool_name" in meta
    assert "tool_args" in meta
    assert "tool_command" in meta
    assert "tool_result_json" in meta
    assert not any(e.role == UiRole.SYSTEM and e.text == "planning..." for e in events)


def test_tool_failure_is_returned_to_loop(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.screenshot_validate_required = False
    runtime._client = FakeClient([
        {
            "assistant_text": "Trying first approach.",
            "tool_calls": [
                ToolCall(
                    tool_call_id="call_1",
                    name="run_pymol_command",
                    arguments={"command": "bad_command"},
                    arguments_json='{"command":"bad_command"}',
                )
            ],
        },
        {
            "assistant_text": "Trying corrected command.",
            "tool_calls": [
                ToolCall(
                    tool_call_id="call_2",
                    name="run_pymol_command",
                    arguments={"command": "color red"},
                    arguments_json='{"command":"color red"}',
                )
            ],
        },
        {"assistant_text": "Done.", "tool_calls": []},
    ])

    runtime._agent_worker("make it red")

    assert runtime.cmd._parser.commands == ["bad_command", "color red"]


def test_doom_loop_warning_injected(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.doom_loop_threshold = 2
    runtime._client = FakeClient([
        {
            "assistant_text": "Try 1",
            "tool_calls": [
                ToolCall(
                    tool_call_id="call_1",
                    name="run_pymol_command",
                    arguments={"command": "zoom"},
                    arguments_json='{"command":"zoom"}',
                )
            ],
        },
        {
            "assistant_text": "Try 2",
            "tool_calls": [
                ToolCall(
                    tool_call_id="call_2",
                    name="run_pymol_command",
                    arguments={"command": "zoom"},
                    arguments_json='{"command":"zoom"}',
                )
            ],
        },
        {"assistant_text": "Done", "tool_calls": []},
    ])

    runtime._agent_worker("zoom")

    _events(runtime)
    assert any(
        m.get("role") == "system" and "DOOM LOOP DETECTED" in str(m.get("content", ""))
        for m in runtime.history
    )


def test_step_limit_has_explicit_error(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.max_agent_steps = 1
    runtime._client = FakeClient([
        {
            "assistant_text": "working",
            "tool_calls": [
                ToolCall(
                    tool_call_id="call_1",
                    name="run_pymol_command",
                    arguments={"command": "zoom"},
                    arguments_json='{"command":"zoom"}',
                )
            ],
        }
    ])

    runtime._agent_worker("zoom a lot")

    events = _events(runtime)
    assert any(e.role == UiRole.ERROR and "step limit" in e.text for e in events)
    assert not any(e.role == UiRole.SYSTEM and e.text == "planning..." for e in events)


def test_validation_required_inserts_snapshot_turn(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.screenshot_validate_required = True
    runtime._client = FakeClient([
        {
            "assistant_text": "run command",
            "tool_calls": [
                ToolCall(
                    tool_call_id="call_1",
                    name="run_pymol_command",
                    arguments={"command": "zoom"},
                    arguments_json='{"command":"zoom"}',
                )
            ],
        },
        {
            "assistant_text": "validating",
            "tool_calls": [
                ToolCall(
                    tool_call_id="call_2",
                    name="capture_viewer_snapshot",
                    arguments={"purpose": "validate"},
                    arguments_json='{"purpose":"validate"}',
                )
            ],
        },
        {"assistant_text": "Done", "tool_calls": []},
    ])

    runtime._agent_worker("zoom then answer")
    events = _events(runtime)
    snapshot_events = [
        e
        for e in events
        if e.role == UiRole.TOOL_RESULT and e.metadata.get("tool_name") == "capture_viewer_snapshot"
    ]
    assert snapshot_events
    assert any(
        e.role == UiRole.TOOL_RESULT
        and e.metadata.get("visual_validation") == "validated: screenshot+state"
        for e in events
    )
    meta = snapshot_events[0].metadata
    assert "tool_call_id" in meta
    assert "tool_args" in meta
    assert "tool_result_json" in meta


def test_duplicate_command_in_turn_is_skipped(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.screenshot_validate_required = False
    runtime.doom_loop_threshold = 5
    runtime._client = FakeClient([
        {
            "assistant_text": "I will zoom now and then confirm.",
            "tool_calls": [
                ToolCall(
                    tool_call_id="call_1",
                    name="run_pymol_command",
                    arguments={"command": "zoom"},
                    arguments_json='{"command":"zoom"}',
                )
            ],
        },
        {
            "assistant_text": "I will zoom now and then confirm.",
            "tool_calls": [
                ToolCall(
                    tool_call_id="call_2",
                    name="run_pymol_command",
                    arguments={"command": "zoom"},
                    arguments_json='{"command":"zoom"}',
                )
            ],
        },
        {"assistant_text": "Done.", "tool_calls": []},
    ])

    runtime._agent_worker("zoom")
    assert runtime.cmd._parser.commands == ["zoom"]

    events = _events(runtime)
    tool_events = [e for e in events if e.role == UiRole.TOOL_RESULT]
    assert len(tool_events) >= 2
    assert any(
        isinstance(e.metadata.get("tool_result_json"), dict)
        and e.metadata.get("tool_result_json", {}).get("skipped") is True
        for e in tool_events
    )


def test_long_tool_step_warns_once_per_turn(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.long_tool_warn_sec = 0.0
    runtime.screenshot_validate_required = False
    runtime._client = FakeClient([
        {
            "assistant_text": "running",
            "tool_calls": [
                ToolCall(
                    tool_call_id="call_1",
                    name="run_pymol_command",
                    arguments={"command": "zoom"},
                    arguments_json='{"command":"zoom"}',
                ),
                ToolCall(
                    tool_call_id="call_2",
                    name="run_pymol_command",
                    arguments={"command": "color red"},
                    arguments_json='{"command":"color red"}',
                ),
            ],
        },
        {"assistant_text": "Done.", "tool_calls": []},
    ])

    runtime._agent_worker("do two steps")
    events = _events(runtime)
    warnings = [e for e in events if e.role == UiRole.SYSTEM and "tool step took" in e.text]
    assert len(warnings) == 1


def test_stall_loop_aborts_after_hidden_nudge(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.screenshot_validate_required = False
    runtime.doom_loop_threshold = 2
    runtime._client = FakeClient([
        {
            "assistant_text": "I will set this up step by step and apply electrostatics for you.",
            "tool_calls": [
                ToolCall(
                    tool_call_id="call_1",
                    name="run_pymol_command",
                    arguments={"command": "select fmn, resn FMN"},
                    arguments_json='{"command":"select fmn, resn FMN"}',
                )
            ],
        },
        {
            "assistant_text": "I will set this up step by step and apply electrostatics for you.",
            "tool_calls": [
                ToolCall(
                    tool_call_id="call_2",
                    name="run_pymol_command",
                    arguments={"command": "select binding_site, fmn expand 5"},
                    arguments_json='{"command":"select binding_site, fmn expand 5"}',
                )
            ],
        },
        {
            "assistant_text": "I will set this up step by step and apply electrostatics for you.",
            "tool_calls": [
                ToolCall(
                    tool_call_id="call_3",
                    name="run_pymol_command",
                    arguments={"command": "show surface, binding_site"},
                    arguments_json='{"command":"show surface, binding_site"}',
                )
            ],
        },
    ])

    runtime._agent_worker("show FMN electrostatics")

    assert runtime.cmd._parser.commands == ["select fmn, resn FMN", "select binding_site, fmn expand 5"]
    assert any(
        m.get("role") == "system" and str(m.get("content", "")).startswith("DOOM LOOP DETECTED:")
        for m in runtime.history
    )
    events = _events(runtime)
    assert any(e.role == UiRole.ERROR and "I'm stuck" in e.text for e in events)


def test_snapshot_failure_fallback_warning(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.screenshot_validate_required = True

    def failing_png(path, width=0, height=0, ray=0, quiet=1, prior=0):
        raise RuntimeError("png failed")

    runtime.cmd.png = failing_png
    runtime._client = FakeClient([
        {
            "assistant_text": "validate",
            "tool_calls": [
                ToolCall(
                    tool_call_id="call_1",
                    name="capture_viewer_snapshot",
                    arguments={},
                    arguments_json="{}",
                )
            ],
        },
        {"assistant_text": "done", "tool_calls": []},
    ])

    runtime._agent_worker("check")
    events = _events(runtime)
    assert any(e.role == UiRole.TOOL_RESULT and e.ok is False for e in events)
    assert any(
        e.role == UiRole.TOOL_RESULT
        and e.metadata.get("visual_validation") == "validated: state-only (screenshot failed)"
        for e in events
    )


def test_internal_system_reminders_not_visible(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="Visual validation required now: call capture_viewer_snapshot before final answer."))
    runtime.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="Validation required: capture_viewer_snapshot must be called before final answer because scene-changing commands were executed."))
    runtime.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="DOOM LOOP DETECTED: tool 'run_pymol_command' repeated 3 times with identical arguments. Try a different approach and do not repeat the same tool call."))
    runtime.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="AI mode enabled"))

    events = _events(runtime)
    assert len(events) == 1
    assert events[0].role == UiRole.SYSTEM
    assert events[0].text == "AI mode enabled"
