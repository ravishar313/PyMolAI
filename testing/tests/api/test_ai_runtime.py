from types import SimpleNamespace

from pymol.ai import runtime as runtime_module
from pymol.ai.api_key_store import ApiKeyStatus
from pymol.ai.openbio_api_key_store import ApiKeyStatus as OpenBioApiKeyStatus
from pymol.ai.message_types import UiEvent, UiRole
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
        self.kwhash = Shortcut(["show", "hide", "color", "zoom", "fetch", "select"])
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


class FakeSdkLoop:
    def __init__(self, plans):
        self._plans = list(plans)
        self.calls = []

    def map_openrouter_env(self):
        return {
            "ANTHROPIC_BASE_URL": "https://openrouter.ai/api",
            "ANTHROPIC_AUTH_TOKEN": "test",
            "ANTHROPIC_API_KEY": "",
        }

    def run_turn(self, **kwargs):
        self.calls.append(kwargs)
        plan = self._plans.pop(0) if self._plans else {"assistant_text": "", "session_id": None}

        for action in plan.get("actions") or []:
            if action["kind"] == "tool_run":
                kwargs["run_command_tool"](action.get("id", "call_run"), action.get("args", {}))
            elif action["kind"] == "tool_snapshot":
                kwargs["snapshot_tool"](action.get("id", "call_snapshot"), action.get("args", {}))
            elif action["kind"] == "openbio_tool":
                cb = kwargs.get("openbio_api_tool")
                if cb:
                    cb(
                        action.get("id", "call_openbio"),
                        action.get("tool_name", "openbio_api_health"),
                        action.get("args", {}),
                    )
            elif action["kind"] == "external_tool_result":
                cb = kwargs.get("on_tool_result")
                if cb:
                    cb(
                        action.get("id", "external_call"),
                        action.get("tool_name", ""),
                        action.get("args", {}),
                        action.get("result"),
                        action.get("is_error"),
                    )
            elif action["kind"] == "stream":
                kwargs["on_text_chunk"](action.get("text", ""))
            elif action["kind"] == "reason":
                cb = kwargs.get("on_reasoning_chunk")
                if cb:
                    cb(action.get("text", ""))

        return SimpleNamespace(
            assistant_text=plan.get("assistant_text", ""),
            session_id=plan.get("session_id"),
            error=plan.get("error"),
            error_class=plan.get("error_class"),
            interrupted=plan.get("interrupted", False),
            num_turns=plan.get("num_turns"),
        )


def _runtime(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("PYMOL_AI_DISABLE", raising=False)
    monkeypatch.setenv("PYMOL_AI_REASONING_DEFAULT", "0")
    monkeypatch.setenv("PYMOL_AI_CONVERSATION_MODE", "local_first")
    runtime = AiRuntime(DummyCmd())
    runtime.set_ui_mode("qt")
    return runtime


def _events(runtime):
    return runtime.drain_ui_events()


def test_runtime_bootstraps_saved_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("PYMOL_AI_REASONING_DEFAULT", "0")
    monkeypatch.setenv("PYMOL_AI_CONVERSATION_MODE", "local_first")

    def fake_load():
        monkeypatch.setenv("OPENROUTER_API_KEY", "saved-key-1234")
        return ApiKeyStatus(
            has_key=True,
            source="saved",
            masked_key="****1234",
            keyring_available=True,
        )

    monkeypatch.setattr(runtime_module, "load_saved_key_into_env_if_needed", fake_load)

    runtime = AiRuntime(DummyCmd())
    runtime.set_ui_mode("qt")

    assert runtime.enabled is True
    assert runtime._api_key == "saved-key-1234"
    assert runtime._api_key_source == "saved"


def test_runtime_bootstraps_saved_openbio_api_key(monkeypatch):
    monkeypatch.delenv("OPENBIO_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("PYMOL_AI_REASONING_DEFAULT", "0")
    monkeypatch.setenv("PYMOL_AI_CONVERSATION_MODE", "local_first")

    def fake_load_openbio():
        monkeypatch.setenv("OPENBIO_API_KEY", "saved-openbio-key-1234")
        return OpenBioApiKeyStatus(
            has_key=True,
            source="saved",
            masked_key="****1234",
            keyring_available=True,
        )

    monkeypatch.setattr(runtime_module, "load_openbio_saved_key_into_env_if_needed", fake_load_openbio)

    runtime = AiRuntime(DummyCmd())
    runtime.set_ui_mode("qt")

    assert runtime._openbio_api_key == "saved-openbio-key-1234"
    assert runtime._openbio_api_key_source == "saved"


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
    runtime._sdk_session_id = "abc"
    old_query_session_id = runtime._chat_query_session_id

    runtime.clear_session(emit_notice=False)
    assert runtime.history == []
    assert runtime._stream_line_buffer == ""
    assert runtime._recent_tool_results == []
    assert runtime._sdk_session_id is None
    assert runtime._chat_query_session_id != old_query_session_id
    assert _events(runtime) == []

    runtime.clear_session(emit_notice=True)
    events = _events(runtime)
    assert any(e.role == UiRole.SYSTEM and "session memory cleared" in e.text for e in events)


def test_ensure_ai_default_mode(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.input_mode = "cli"
    runtime.enabled = False

    ok = runtime.ensure_ai_default_mode(emit_notice=False)
    assert ok is True
    assert runtime.input_mode == "ai"
    assert runtime.enabled is True


def test_export_import_session_state_roundtrip(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.input_mode = "cli"
    runtime.history = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    runtime.model = "openai/test"
    runtime.enabled = True
    runtime.reasoning_visible = True
    runtime._sdk_session_id = "sess_1"
    runtime.conversation_mode = "hybrid_resume"
    runtime._chat_query_session_id = "chat_scope_1"

    state = runtime.export_session_state()
    assert state["input_mode"] == "cli"
    assert len(state["history"]) == 2
    assert state["backend"] == "claude_sdk"
    assert state["sdk_session_id"] == "sess_1"
    assert state["conversation_mode"] == "hybrid_resume"
    assert state["chat_query_session_id"] == "chat_scope_1"

    restored = _runtime(monkeypatch)
    restored.import_session_state(state, apply_model=False)
    assert restored.input_mode == "cli"
    assert restored.history == runtime.history
    assert restored._sdk_session_id == "sess_1"
    assert restored.conversation_mode == "hybrid_resume"
    assert restored._chat_query_session_id == "chat_scope_1"
    restored.import_session_state(state, apply_model=True)
    assert restored.model == "openai/test"
    assert restored.reasoning_visible is True


def test_runtime_events_and_history_do_not_expose_api_key(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.handle_typed_input("/ai")
    events = _events(runtime)
    serialized = repr(events) + repr(runtime.history) + repr(runtime.export_session_state())
    assert "test-key" not in serialized


def test_missing_api_key_does_not_enable(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(
        runtime_module,
        "load_saved_key_into_env_if_needed",
        lambda: ApiKeyStatus(has_key=False, source="none", masked_key="", keyring_available=True),
    )
    runtime = AiRuntime(DummyCmd())
    runtime.set_ui_mode("qt")

    runtime.handle_typed_input("/ai")
    events = _events(runtime)
    assert not runtime.enabled
    assert any("OPENROUTER_API_KEY (or ANTHROPIC_AUTH_TOKEN) is not set" in e.text for e in events)
    assert not any("AI enabled" in e.text for e in events)

    runtime.handle_typed_input("/ai on")
    assert not runtime.enabled
    assert any("OPENROUTER_API_KEY (or ANTHROPIC_AUTH_TOKEN) is not set" in e.text for e in _events(runtime))


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


def test_sdk_path_emits_stream_and_tool_metadata(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.screenshot_validate_required = False
    runtime._sdk_loop = FakeSdkLoop(
        [
            {
                "actions": [
                    {"kind": "stream", "text": "Working...\n"},
                    {"kind": "tool_run", "id": "tool_1", "args": {"command": "zoom"}},
                ],
                "assistant_text": "Done.",
                "session_id": "sess_a",
            }
        ]
    )

    runtime._agent_worker("zoom please")

    events = _events(runtime)
    assert any(e.role == UiRole.TOOL_RESULT for e in events)
    tool_meta = [e.metadata for e in events if e.role == UiRole.TOOL_RESULT][0]
    assert "tool_call_id" in tool_meta
    assert "tool_name" in tool_meta
    assert "tool_args" in tool_meta
    assert "tool_command" in tool_meta
    assert "tool_result_json" in tool_meta
    assert runtime._sdk_session_id == "sess_a"


def test_stream_only_output_does_not_emit_missing_final_error(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.screenshot_validate_required = False
    runtime._sdk_loop = FakeSdkLoop(
        [
            {
                "actions": [{"kind": "stream", "text": "Loaded 5del successfully.\n"}],
                "assistant_text": "",
                "session_id": "sess_stream",
            }
        ]
    )

    runtime._agent_worker("load 5del")

    events = _events(runtime)
    assert any(e.role == UiRole.AI and "Loaded 5del successfully." in e.text for e in events)
    assert not any(
        e.role == UiRole.ERROR and "did not receive a final answer" in str(e.text or "")
        for e in events
    )
    assert runtime.history[-1]["role"] == "assistant"
    assert "Loaded 5del successfully." in str(runtime.history[-1]["content"])


def test_iteration_cap_emits_continue_prompt(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.screenshot_validate_required = False
    runtime._sdk_loop = FakeSdkLoop(
        [
            {
                "assistant_text": "",
                "session_id": "sess_iter_cap",
                "num_turns": runtime.max_agent_steps,
            }
        ]
    )

    runtime._agent_worker("run full workflow")

    events = _events(runtime)
    assert any(
        e.role == UiRole.SYSTEM and "Tell me to continue" in str(e.text or "")
        for e in events
    )
    assert not any(
        e.role == UiRole.ERROR and "did not receive a final answer" in str(e.text or "")
        for e in events
    )


def test_stream_chunks_emit_progress_without_newline(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime._on_assistant_chunk("12345")
    first = _events(runtime)
    assert len(first) == 1
    assert first[0].role == UiRole.AI
    assert first[0].text == "12345"
    assert first[0].metadata.get("stream_chunk") is True
    runtime._on_assistant_chunk("67890")
    events = _events(runtime)
    assert any(e.role == UiRole.AI and e.text == "67890" for e in events)


def test_sdk_fail_fast_no_fallback(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime._sdk_loop = FakeSdkLoop(
        [{"error": "provider failed", "error_class": "sdk_error", "assistant_text": "", "session_id": None}]
    )

    runtime._agent_worker("do task")
    events = _events(runtime)
    assert any(e.role == UiRole.ERROR and "provider failed" in e.text for e in events)


def test_resume_invalid_retries_with_context_bootstrap(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.history = [{"role": "assistant", "content": "previous"}]
    runtime._sdk_session_id = "old_session"
    runtime.conversation_mode = "hybrid_resume"
    runtime._sdk_loop = FakeSdkLoop(
        [
            {"error": "session expired", "error_class": "resume_invalid"},
            {"assistant_text": "Recovered", "session_id": "new_session"},
        ]
    )

    runtime._agent_worker("continue")

    assert len(runtime._sdk_loop.calls) == 2
    assert runtime._sdk_loop.calls[0]["resume_session_id"] == "old_session"
    assert runtime._sdk_loop.calls[1]["resume_session_id"] is None
    assert "Conversation context:" in runtime._sdk_loop.calls[1]["prompt"]
    assert runtime._sdk_session_id == "new_session"


def test_snapshot_auto_enforcement_when_missing(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.screenshot_validate_required = True
    runtime._sdk_loop = FakeSdkLoop(
        [
            {
                "actions": [
                    {"kind": "tool_run", "id": "tool_1", "args": {"command": "zoom"}},
                ],
                "assistant_text": "Done",
                "session_id": "sess_b",
            }
        ]
    )

    runtime._agent_worker("zoom and answer")
    events = _events(runtime)
    snapshot_events = [
        e for e in events if e.role == UiRole.TOOL_RESULT and e.metadata.get("tool_name") == "capture_viewer_snapshot"
    ]
    assert snapshot_events
    assert snapshot_events[0].metadata.get("tool_call_id") == "auto_capture_viewer_snapshot_1"


def test_snapshot_failure_fallback_warning(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.screenshot_validate_required = True

    def failing_png(path, width=0, height=0, ray=0, quiet=1, prior=0):
        raise RuntimeError("png failed")

    runtime.cmd.png = failing_png
    runtime._sdk_loop = FakeSdkLoop(
        [
            {
                "actions": [
                    {"kind": "tool_run", "id": "tool_1", "args": {"command": "zoom"}},
                ],
                "assistant_text": "Done",
                "session_id": "sess_c",
            }
        ]
    )

    runtime._agent_worker("check")
    events = _events(runtime)
    assert any(e.role == UiRole.TOOL_RESULT and e.ok is False for e in events)
    assert any(
        e.role == UiRole.TOOL_RESULT
        and e.metadata.get("visual_validation") == "validated: state-only (screenshot failed)"
        for e in events
    )


def test_external_bash_tool_result_is_visible(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.screenshot_validate_required = False
    runtime._sdk_loop = FakeSdkLoop(
        [
            {
                "actions": [
                    {
                        "kind": "external_tool_result",
                        "id": "tool_bash_1",
                        "tool_name": "Bash",
                        "args": {"command": "which ffmpeg"},
                        "result": {"stdout": "/opt/homebrew/bin/ffmpeg", "exit_code": 0},
                        "is_error": False,
                    }
                ],
                "assistant_text": "ffmpeg is installed",
                "session_id": "sess_shell",
            }
        ]
    )

    runtime._agent_worker("check ffmpeg")
    events = _events(runtime)
    tool_events = [e for e in events if e.role == UiRole.TOOL_RESULT]
    assert tool_events
    evt = tool_events[0]
    assert evt.ok is True
    assert evt.metadata.get("tool_name") == "Bash"
    assert evt.metadata.get("tool_command") == "which ffmpeg"
    result_json = evt.metadata.get("tool_result_json")
    assert "ffmpeg" in str(result_json)


def test_cancel_request_stops_worker_cleanly(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime._sdk_loop = FakeSdkLoop([{"error": "cancelled", "error_class": "cancelled", "interrupted": True}])
    runtime.request_cancel()

    runtime._agent_worker("do work")
    events = _events(runtime)
    assert any(e.role == UiRole.SYSTEM and "request cancelled" in e.text for e in events)
    assert not any(e.role == UiRole.ERROR and "unexpected error" in e.text for e in events)


def test_reasoning_hidden_by_default_optional(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime._sdk_loop = FakeSdkLoop(
        [{"actions": [{"kind": "reason", "text": "thinking"}], "assistant_text": "done", "session_id": "s"}]
    )

    runtime._agent_worker("x")
    events = _events(runtime)
    assert not any(e.role == UiRole.REASONING for e in events)

    runtime.reasoning_visible = True
    runtime._sdk_loop = FakeSdkLoop(
        [{"actions": [{"kind": "reason", "text": "thinking2"}], "assistant_text": "done2", "session_id": "s2"}]
    )
    runtime._agent_worker("y")
    events = _events(runtime)
    assert any(e.role == UiRole.REASONING and "thinking2" in e.text for e in events)


def test_default_max_agent_steps_is_high(monkeypatch):
    runtime = _runtime(monkeypatch)
    assert runtime.max_agent_steps == 64


def test_local_first_mode_uses_history_and_no_resume(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.history = [
        {"role": "assistant", "content": "prior answer"},
        {"role": "tool", "name": "run_pymol_command", "content": '{"ok":true,"command":"zoom"}'},
    ]
    runtime._sdk_session_id = "old_session"
    runtime._sdk_loop = FakeSdkLoop(
        [{"assistant_text": "ok", "session_id": "sess_local"}]
    )

    runtime._agent_worker("next")

    assert len(runtime._sdk_loop.calls) == 1
    call = runtime._sdk_loop.calls[0]
    assert call["resume_session_id"] is None
    assert "Conversation context:" in call["prompt"]
    assert "tool[run_pymol_command]:" in call["prompt"]


def test_conversation_mode_matrix(monkeypatch):
    runtime = _runtime(monkeypatch)

    runtime.conversation_mode = "resume_only"
    runtime._sdk_session_id = "sess_old"
    runtime._sdk_loop = FakeSdkLoop([{"assistant_text": "a", "session_id": "s1"}])
    runtime._agent_worker("one")
    call = runtime._sdk_loop.calls[-1]
    assert call["resume_session_id"] == "sess_old"
    assert "Conversation context:" not in call["prompt"]

    runtime.conversation_mode = "hybrid_resume"
    runtime._sdk_session_id = "sess_old_2"
    runtime._sdk_loop = FakeSdkLoop([{"assistant_text": "b", "session_id": "s2"}])
    runtime._agent_worker("two")
    call = runtime._sdk_loop.calls[-1]
    assert call["resume_session_id"] == "sess_old_2"


def test_internal_system_reminders_not_visible(monkeypatch):
    runtime = _runtime(monkeypatch)
    runtime.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="Visual validation required now: call capture_viewer_snapshot before final answer."))
    runtime.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="Validation required: capture_viewer_snapshot must be called before final answer because scene-changing commands were executed."))
    runtime.emit_ui_event(UiEvent(role=UiRole.SYSTEM, text="AI mode enabled"))

    events = _events(runtime)
    assert len(events) == 1
    assert events[0].role == UiRole.SYSTEM
    assert events[0].text == "AI mode enabled"


def test_openbio_tools_not_available_without_openbio_key(monkeypatch):
    monkeypatch.delenv("OPENBIO_API_KEY", raising=False)
    runtime = _runtime(monkeypatch)
    runtime._sdk_loop = FakeSdkLoop([{"assistant_text": "ok", "session_id": "sess_no_openbio"}])

    runtime._agent_worker("list openbio tools")

    assert len(runtime._sdk_loop.calls) == 1
    assert runtime._sdk_loop.calls[0].get("openbio_api_tool") is None


def test_openbio_tool_execution_emits_tool_result_and_history(monkeypatch):
    monkeypatch.setenv("OPENBIO_API_KEY", "openbio-test-key")
    runtime = _runtime(monkeypatch)
    runtime.screenshot_validate_required = False
    runtime._sdk_loop = FakeSdkLoop(
        [
            {
                "actions": [
                    {
                        "kind": "openbio_tool",
                        "id": "ob_1",
                        "tool_name": "openbio_api_list_tools",
                        "args": {"category": "pubmed", "limit": 3},
                    }
                ],
                "assistant_text": "Done",
                "session_id": "sess_openbio",
            }
        ]
    )

    monkeypatch.setattr(
        runtime_module,
        "execute_openbio_api_gateway_tool",
        lambda tool_name, tool_args, **_kwargs: {
            "ok": True,
            "tool_name": tool_name,
            "echo_args": dict(tool_args or {}),
        },
    )

    runtime._agent_worker("find pubmed tools")
    events = _events(runtime)
    openbio_events = [
        e
        for e in events
        if e.role == UiRole.TOOL_RESULT and e.metadata.get("tool_name") == "openbio_api_list_tools"
    ]
    assert openbio_events
    assert openbio_events[0].ok is True
    result_json = openbio_events[0].metadata.get("tool_result_json")
    assert "echo_args" in str(result_json)
    assert any(
        m.get("role") == "tool" and m.get("name") == "openbio_api_list_tools"
        for m in runtime.history
    )
