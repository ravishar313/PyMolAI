import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "modules" / "pymol" / "ai"))

import claude_sdk_loop as sdk_loop_module

ClaudeSdkLoop = sdk_loop_module.ClaudeSdkLoop


class StreamEvent:
    def __init__(self, text="", thinking=""):
        delta = type("Delta", (), {})()
        if text:
            setattr(delta, "type", "text_delta")
            setattr(delta, "text", text)
        else:
            setattr(delta, "type", "thinking_delta")
            setattr(delta, "thinking", thinking)

        event = type("Event", (), {})()
        setattr(event, "type", "content_block_delta")
        setattr(event, "delta", delta)
        self.event = event


class FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeToolUseBlock:
    def __init__(self, tool_id, name, tool_input):
        self.type = "tool_use"
        self.id = tool_id
        self.name = name
        self.input = tool_input


class FakeToolResultBlock:
    def __init__(self, tool_use_id, content, is_error=False):
        self.type = "tool_result"
        self.tool_use_id = tool_use_id
        self.content = content
        self.is_error = is_error


class AssistantMessage:
    def __init__(self, text="", content=None):
        self.content = list(content) if content is not None else [FakeTextBlock(text)]


class UserMessage:
    def __init__(self, *, parent_tool_use_id=None, tool_use_result=None, content=None):
        self.parent_tool_use_id = parent_tool_use_id
        self.tool_use_result = tool_use_result
        self.content = list(content or [])


class ResultMessage:
    def __init__(self, *, session_id="sess_test", is_error=False, result=""):
        self.session_id = session_id
        self.is_error = is_error
        self.result = result


@dataclass
class PermissionResultAllow:
    behavior: str = "allow"
    updated_input: dict | None = None
    updated_permissions: list | None = None


@dataclass
class PermissionResultDeny:
    behavior: str = "deny"
    message: str = ""
    interrupt: bool = False


class FakeClient:
    last_options = None
    last_query_prompt = None
    last_query_session_id = None
    messages = None

    def __init__(self, options):
        self.options = options
        FakeClient.last_options = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def query(self, prompt, session_id="default"):
        self.prompt = prompt
        self.session_id = session_id
        FakeClient.last_query_prompt = prompt
        FakeClient.last_query_session_id = session_id

    async def interrupt(self):
        self.interrupted = True

    async def receive_response(self):
        if self.messages is not None:
            for item in self.messages:
                yield item
            return
        yield StreamEvent(text="Hello ")
        yield AssistantMessage("Hello world")
        yield ResultMessage(session_id="sess_new")


@dataclass
class FakeOptions:
    model: str
    system_prompt: str
    max_turns: int
    permission_mode: str
    include_partial_messages: bool
    continue_conversation: bool
    resume: str
    mcp_servers: dict
    env: dict
    cwd: str
    allowed_tools: list | None = None
    max_buffer_size: int | None = None
    can_use_tool: object | None = None


@dataclass
class FakeToolDef:
    name: str
    handler: object


def _symbols():
    def create_sdk_mcp_server(name, version="1.0.0", tools=None):
        return {"type": "sdk", "name": name, "version": version, "tools": list(tools or [])}

    def tool(name, _desc, _schema):
        def decorator(fn):
            return FakeToolDef(name=name, handler=fn)

        return decorator

    class _Module:
        PermissionResultAllow = PermissionResultAllow
        PermissionResultDeny = PermissionResultDeny

    return {
        "module": _Module(),
        "ClaudeCodeOptions": FakeOptions,
        "ClaudeSDKClient": FakeClient,
        "create_sdk_mcp_server": create_sdk_mcp_server,
        "tool": tool,
    }


def test_map_openrouter_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    loop = ClaudeSdkLoop()
    env = loop.map_openrouter_env()

    assert env["ANTHROPIC_AUTH_TOKEN"] == "or-key"
    assert env["ANTHROPIC_BASE_URL"].startswith("https://openrouter.ai/")
    assert env["ANTHROPIC_API_KEY"] == ""


def test_trace_stream_default_off_and_setter(monkeypatch):
    monkeypatch.delenv("PYMOL_AI_TRACE_STREAM", raising=False)
    loop = ClaudeSdkLoop()
    assert loop._trace_stream is False
    loop.set_trace_stream(True)
    assert loop._trace_stream is True
    loop.set_trace_stream(False)
    assert loop._trace_stream is False


def test_build_tool_server_has_only_two_tools(monkeypatch):
    monkeypatch.setattr(sdk_loop_module, "_import_sdk_symbols", _symbols)
    loop = ClaudeSdkLoop()
    symbols = _symbols()

    server = loop.build_tool_server(
        create_sdk_mcp_server=symbols["create_sdk_mcp_server"],
        tool=symbols["tool"],
        run_command_tool=lambda _id, _args: {"ok": True},
        snapshot_tool=lambda _id, _args: {"ok": True},
    )

    assert server["type"] == "sdk"
    assert server["name"] == "pymol_tools"
    assert server["version"] == "1.0.0"
    tool_names = {getattr(t, "name", getattr(t, "__tool_name__", "")) for t in server["tools"]}
    assert tool_names == {"run_pymol_command", "capture_viewer_snapshot"}


def test_run_turn_sets_bypass_permissions_and_allowed_tools(monkeypatch):
    monkeypatch.setattr(sdk_loop_module, "_import_sdk_symbols", _symbols)

    loop = ClaudeSdkLoop()
    result = loop.run_turn(
        prompt="test",
        model="anthropic/claude-sonnet-4",
        system_prompt="sys",
        max_turns=8,
        max_buffer_size=2097152,
        resume_session_id="sess_old",
        on_text_chunk=lambda _t: None,
        on_message_boundary=lambda: None,
        on_reasoning_chunk=None,
        on_tool_result=None,
        should_cancel=lambda: False,
        run_command_tool=lambda _id, _args: {"ok": True, "command": "zoom"},
        snapshot_tool=lambda _id, _args: {"ok": True},
    )

    assert result.error is None
    assert result.session_id == "sess_new"

    opts = FakeClient.last_options
    assert opts.permission_mode == "bypassPermissions"
    assert opts.can_use_tool is not None
    assert opts.allowed_tools in (None, [])
    assert opts.resume == "sess_old"
    assert opts.continue_conversation is False
    assert opts.max_buffer_size == 2097152
    assert FakeClient.last_query_session_id == "sess_old"


def test_run_turn_uses_non_default_query_session_when_not_resuming(monkeypatch):
    monkeypatch.setattr(sdk_loop_module, "_import_sdk_symbols", _symbols)

    loop = ClaudeSdkLoop()
    result = loop.run_turn(
        prompt="test",
        model="anthropic/claude-sonnet-4",
        system_prompt="sys",
        max_turns=8,
        max_buffer_size=2097152,
        resume_session_id=None,
        on_text_chunk=lambda _t: None,
        on_message_boundary=lambda: None,
        on_reasoning_chunk=None,
        on_tool_result=None,
        should_cancel=lambda: False,
        run_command_tool=lambda _id, _args: {"ok": True, "command": "zoom"},
        snapshot_tool=lambda _id, _args: {"ok": True},
    )

    assert result.error is None
    assert isinstance(FakeClient.last_query_session_id, str)
    assert FakeClient.last_query_session_id
    assert FakeClient.last_query_session_id != "default"


def test_run_turn_prefers_explicit_query_session_id(monkeypatch):
    monkeypatch.setattr(sdk_loop_module, "_import_sdk_symbols", _symbols)

    loop = ClaudeSdkLoop()
    result = loop.run_turn(
        prompt="test",
        model="anthropic/claude-sonnet-4",
        system_prompt="sys",
        max_turns=8,
        max_buffer_size=2097152,
        resume_session_id=None,
        query_session_id="chat_scope_123",
        on_text_chunk=lambda _t: None,
        on_message_boundary=lambda: None,
        on_reasoning_chunk=None,
        on_tool_result=None,
        should_cancel=lambda: False,
        run_command_tool=lambda _id, _args: {"ok": True, "command": "zoom"},
        snapshot_tool=lambda _id, _args: {"ok": True},
    )

    assert result.error is None
    assert FakeClient.last_query_session_id == "chat_scope_123"


def test_classify_error_marks_invalid_thinking_signature_as_resume_invalid():
    error = (
        'API Error: 400 {"error":{"message":"Provider returned error","metadata":{"raw":"'
        '{"type":"error","error":{"type":"invalid_request_error","message":"messages.99.content.0: '
        'Invalid `signature` in `thinking` block"}}"}}}'
    )

    assert sdk_loop_module._classify_error(error) == "resume_invalid"


def test_bash_can_use_tool_guard_keeps_cwd_local(monkeypatch):
    monkeypatch.setattr(sdk_loop_module, "_import_sdk_symbols", _symbols)
    loop = ClaudeSdkLoop()
    loop.run_turn(
        prompt="test",
        model="anthropic/claude-sonnet-4",
        system_prompt="sys",
        max_turns=4,
        max_buffer_size=None,
        resume_session_id=None,
        on_text_chunk=lambda _t: None,
        on_message_boundary=lambda: None,
        on_reasoning_chunk=None,
        on_tool_result=None,
        should_cancel=lambda: False,
        run_command_tool=lambda _id, _args: {"ok": True, "command": "zoom"},
        snapshot_tool=lambda _id, _args: {"ok": True},
    )
    opts = FakeClient.last_options
    guard = opts.can_use_tool
    assert guard is not None

    allowed = asyncio.run(guard("Bash", {"command": "pwd"}, None))
    assert allowed.behavior == "allow"
    assert allowed.updated_input["cwd"] == opts.cwd

    denied = asyncio.run(guard("Bash", {"command": "cd /tmp && pwd"}, None))
    assert denied.behavior == "deny"


def test_run_turn_emits_external_tool_results_from_assistant_blocks(monkeypatch):
    monkeypatch.setattr(sdk_loop_module, "_import_sdk_symbols", _symbols)
    FakeClient.messages = [
        AssistantMessage(
            content=[
                FakeToolUseBlock("toolu_1", "Bash", {"command": "which ffmpeg"}),
                FakeToolResultBlock("toolu_1", [{"type": "text", "text": "/opt/homebrew/bin/ffmpeg"}], False),
            ]
        ),
        ResultMessage(session_id="sess_new"),
    ]
    emitted = []

    loop = ClaudeSdkLoop()
    result = loop.run_turn(
        prompt="test",
        model="anthropic/claude-sonnet-4",
        system_prompt="sys",
        max_turns=4,
        max_buffer_size=None,
        resume_session_id=None,
        on_text_chunk=lambda _t: None,
        on_message_boundary=lambda: None,
        on_reasoning_chunk=None,
        on_tool_result=lambda tool_id, name, args, output, is_error: emitted.append(
            (tool_id, name, args, output, is_error)
        ),
        should_cancel=lambda: False,
        run_command_tool=lambda _id, _args: {"ok": True},
        snapshot_tool=lambda _id, _args: {"ok": True},
    )

    assert result.error is None
    assert emitted
    tool_id, name, args, output, is_error = emitted[0]
    assert tool_id == "toolu_1"
    assert name == "Bash"
    assert args.get("command") == "which ffmpeg"
    assert "ffmpeg" in str(output)
    assert is_error is False
    FakeClient.messages = None


def test_run_turn_emits_external_tool_results_from_user_message(monkeypatch):
    monkeypatch.setattr(sdk_loop_module, "_import_sdk_symbols", _symbols)
    FakeClient.messages = [
        AssistantMessage(content=[FakeToolUseBlock("toolu_1", "Bash", {"command": "which ffmpeg"})]),
        UserMessage(
            parent_tool_use_id="toolu_1",
            tool_use_result={"stdout": "/opt/homebrew/bin/ffmpeg", "exit_code": 0},
            content=[],
        ),
        ResultMessage(session_id="sess_new"),
    ]
    emitted = []

    loop = ClaudeSdkLoop()
    result = loop.run_turn(
        prompt="test",
        model="anthropic/claude-sonnet-4",
        system_prompt="sys",
        max_turns=4,
        max_buffer_size=None,
        resume_session_id=None,
        on_text_chunk=lambda _t: None,
        on_message_boundary=lambda: None,
        on_reasoning_chunk=None,
        on_tool_result=lambda tool_id, name, args, output, is_error: emitted.append(
            (tool_id, name, args, output, is_error)
        ),
        should_cancel=lambda: False,
        run_command_tool=lambda _id, _args: {"ok": True},
        snapshot_tool=lambda _id, _args: {"ok": True},
    )

    assert result.error is None
    assert emitted
    tool_id, name, args, output, is_error = emitted[0]
    assert tool_id == "toolu_1"
    assert name == "Bash"
    assert args.get("command") == "which ffmpeg"
    assert "ffmpeg" in str(output)
    assert is_error is False
    FakeClient.messages = None


def test_snapshot_tool_returns_image_content_shape(monkeypatch):
    monkeypatch.setattr(sdk_loop_module, "_import_sdk_symbols", _symbols)
    loop = ClaudeSdkLoop()
    symbols = _symbols()
    data_url = "data:image/png;base64,QUJDRA=="

    server = loop.build_tool_server(
        create_sdk_mcp_server=symbols["create_sdk_mcp_server"],
        tool=symbols["tool"],
        run_command_tool=lambda _id, _args: {"ok": True},
        snapshot_tool=lambda _id, _args: {"payload": {"ok": True}, "image_data_url": data_url},
    )

    snap_tool = next(t for t in server["tools"] if t.name == "capture_viewer_snapshot")
    result = asyncio.run(snap_tool.handler({"purpose": "validate"}))
    content = result["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image"
    assert content[1]["data"] == "QUJDRA=="
    assert content[1]["mimeType"] == "image/png"


def test_run_turn_uses_result_message_text_when_no_assistant_text(monkeypatch):
    monkeypatch.setattr(sdk_loop_module, "_import_sdk_symbols", _symbols)
    FakeClient.messages = [
        AssistantMessage(content=[]),
        ResultMessage(session_id="sess_new", result="Completed successfully."),
    ]

    loop = ClaudeSdkLoop()
    result = loop.run_turn(
        prompt="test",
        model="anthropic/claude-sonnet-4",
        system_prompt="sys",
        max_turns=4,
        max_buffer_size=None,
        resume_session_id=None,
        on_text_chunk=lambda _t: None,
        on_message_boundary=lambda: None,
        on_reasoning_chunk=None,
        on_tool_result=None,
        should_cancel=lambda: False,
        run_command_tool=lambda _id, _args: {"ok": True},
        snapshot_tool=lambda _id, _args: {"ok": True},
    )

    assert result.error is None
    assert result.assistant_text == "Completed successfully."
    FakeClient.messages = None
