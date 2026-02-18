from types import SimpleNamespace

from pymol.ai.message_types import ToolCall, UiRole
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


class FakeClient:
    def __init__(self, turns):
        self.turns = list(turns)

    def stream_assistant_turn(self, **kwargs):
        if not self.turns:
            return {"assistant_text": "", "tool_calls": []}
        return self.turns.pop(0)


def _runtime(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("PYMOL_AI_DISABLE", raising=False)
    runtime = AiRuntime(DummyCmd())
    runtime.set_ui_mode("qt")
    return runtime


def _events(runtime):
    return runtime.drain_ui_events()


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


def test_agent_tool_call_then_final_answer(monkeypatch):
    runtime = _runtime(monkeypatch)
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


def test_tool_failure_is_returned_to_loop(monkeypatch):
    runtime = _runtime(monkeypatch)
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

    events = _events(runtime)
    assert any(e.role == UiRole.SYSTEM and "DOOM LOOP DETECTED" in e.text for e in events)


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
