from types import SimpleNamespace

import pytest

from pymol.ai.openrouter_client import PlanParseError, parse_plan_text
from pymol.ai.protocol import AiPlan
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


def _runtime(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("PYMOL_AI_DISABLE", raising=False)
    runtime = AiRuntime(DummyCmd())
    runtime.final_answer_enabled = False
    return runtime


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


def test_missing_api_key_does_not_enable(monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    runtime = AiRuntime(DummyCmd())
    runtime.handle_typed_input("/ai on")
    out, _ = capsys.readouterr()
    assert "OPENROUTER_API_KEY is not set" in out
    assert not runtime.enabled


def test_ai_mode_routes_regular_text_to_ai(monkeypatch):
    runtime = _runtime(monkeypatch)
    calls = []
    runtime._start_plan_request = lambda prompt: calls.append(prompt)

    assert runtime.handle_typed_input("show cartoon") is True
    assert calls == ["show cartoon"]


def test_disabled_ai_consumes_input_with_message(monkeypatch, capsys):
    runtime = _runtime(monkeypatch)
    runtime.enabled = False

    assert runtime.handle_typed_input("show cartoon") is True
    out, _ = capsys.readouterr()
    assert "disabled" in out
    assert runtime.cmd._parser.commands == []


def test_cli_mode_persistent_executes_directly(monkeypatch):
    runtime = _runtime(monkeypatch)

    runtime.handle_typed_input("/cli")
    assert runtime.input_mode == "cli"

    runtime.handle_typed_input("zoom")
    assert runtime.cmd._parser.commands == ["zoom"]
    assert any("CLI command: zoom" in x["content"] for x in runtime.history)


def test_cli_one_off_executes_without_switching_mode(monkeypatch):
    runtime = _runtime(monkeypatch)
    assert runtime.input_mode == "ai"

    runtime.handle_typed_input("/cli color red")
    assert runtime.cmd._parser.commands == ["color red"]
    assert runtime.input_mode == "ai"


def test_cli_one_off_load_pdbid_translates_to_fetch(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.handle_typed_input("/cli load 1bom")
    assert runtime.cmd._parser.commands == ["fetch 1bom"]


def test_handle_plan_success_auto_executes(monkeypatch):
    runtime = _runtime(monkeypatch)
    plan = AiPlan(summary="style", commands=["show cartoon", "color chain"])

    runtime._handle_plan_success("style this", plan)

    assert runtime.cmd._parser.commands == ["show cartoon", "color chain"]


def test_execution_stops_on_first_failure(monkeypatch):
    runtime = _runtime(monkeypatch)
    plan = AiPlan(summary="mixed", commands=["show cartoon", "bad_command", "color chain"])

    runtime._execute_plan(plan)

    assert runtime.cmd._parser.commands == ["show cartoon", "bad_command"]


def test_auto_repair_continues_after_failure(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.max_auto_repairs = 1
    runtime._repair_plan = lambda *_: AiPlan(summary="repair", commands=["color chain"])
    plan = AiPlan(summary="mixed", commands=["bad_command"])

    runtime._execute_until_complete("style", plan)

    assert runtime.cmd._parser.commands == ["bad_command", "color chain"]


def test_model_response_parser_failure_is_safe():
    with pytest.raises(PlanParseError):
        parse_plan_text("not-json")


def test_model_response_parser_with_plan_json():
    text = (
        "assistant text\\n<PLAN_JSON>"
        '{"summary":"style","commands":["show cartoon","zoom"],"warnings":[]}'
        "</PLAN_JSON>"
    )
    plan = parse_plan_text(text)
    assert plan.summary == "style"
    assert plan.commands == ["show cartoon", "zoom"]


def test_runtime_normalizes_multiline_plan(monkeypatch):
    runtime = _runtime(monkeypatch)
    plan = AiPlan(summary="style", commands=["show cartoon;color chain\nzoom"])
    normalized = runtime._normalize_plan(plan)
    assert normalized.commands == ["show cartoon", "color chain", "zoom"]


def test_stream_filter_hides_plan_json(monkeypatch, capsys):
    runtime = _runtime(monkeypatch)
    runtime._emit_chunk("thinking...\n<PLAN_JSON>{\"summary\":\"s\",")
    runtime._emit_chunk("\"commands\":[\"zoom\"]}</PLAN_JSON>\n")
    runtime._flush_chunks()
    out, _ = capsys.readouterr()
    assert "thinking..." in out
    assert "<PLAN_JSON>" not in out
    assert "\"commands\"" not in out
