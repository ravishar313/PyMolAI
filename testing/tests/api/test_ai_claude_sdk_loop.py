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


class AssistantMessage:
    def __init__(self, text):
        self.content = [FakeTextBlock(text)]


class ResultMessage:
    def __init__(self, *, session_id="sess_test", is_error=False, result=""):
        self.session_id = session_id
        self.is_error = is_error
        self.result = result


class FakeClient:
    last_options = None

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

    async def interrupt(self):
        self.interrupted = True

    async def receive_response(self):
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
    allowed_tools: list
    env: dict
    cwd: str
    max_buffer_size: int | None = None


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

    return {
        "module": object(),
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
        on_reasoning_chunk=None,
        should_cancel=lambda: False,
        run_command_tool=lambda _id, _args: {"ok": True, "command": "zoom"},
        snapshot_tool=lambda _id, _args: {"ok": True},
    )

    assert result.error is None
    assert result.session_id == "sess_new"

    opts = FakeClient.last_options
    assert opts.permission_mode == "bypassPermissions"
    assert set(opts.allowed_tools) == {
        "run_pymol_command",
        "capture_viewer_snapshot",
    }
    assert opts.resume == "sess_old"
    assert opts.continue_conversation is True
    assert opts.max_buffer_size == 2097152


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
