from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple


class ClaudeSdkLoopError(RuntimeError):
    def __init__(self, message: str, *, error_class: str = "sdk_error"):
        super().__init__(message)
        self.error_class = error_class


@dataclass
class SdkTurnResult:
    assistant_text: str = ""
    session_id: Optional[str] = None
    error: Optional[str] = None
    error_class: Optional[str] = None
    interrupted: bool = False


def _import_sdk_symbols() -> Dict[str, Any]:
    options_cls = None
    try:
        import claude_agent_sdk as sdk_mod  # type: ignore[import-not-found]
        options_cls = getattr(sdk_mod, "ClaudeAgentOptions", None)
    except Exception as exc:  # noqa: BLE001
        raise ClaudeSdkLoopError(
            "Claude Agent SDK is unavailable. Install claude-agent-sdk on Python >= 3.10.",
            error_class="sdk_unavailable",
        ) from exc

    client_cls = getattr(sdk_mod, "ClaudeSDKClient", None)
    if options_cls is None or client_cls is None:
        raise ClaudeSdkLoopError(
            "Claude Agent SDK import failed: missing core client symbols.",
            error_class="sdk_unavailable",
        )

    create_sdk_mcp_server = getattr(sdk_mod, "create_sdk_mcp_server", None)
    tool = getattr(sdk_mod, "tool", None)
    if not (callable(create_sdk_mcp_server) and callable(tool)):
        raise ClaudeSdkLoopError(
            "Claude Agent SDK import failed: missing MCP tool symbols.",
            error_class="sdk_unavailable",
        )

    return {
        "module": sdk_mod,
        "sdk_package": "claude-agent-sdk",
        "ClaudeCodeOptions": options_cls,
        "ClaudeSDKClient": client_cls,
        "create_sdk_mcp_server": create_sdk_mcp_server,
        "tool": tool,
    }


def _to_mapping(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    out = {}
    for key in dir(obj):
        if key.startswith("_"):
            continue
        try:
            out[key] = getattr(obj, key)
        except Exception:
            pass
    return out


def _extract_stream_chunks(message: Any) -> Tuple[str, str]:
    text = ""
    reasoning = ""

    event = getattr(message, "event", message)
    data = _to_mapping(event)
    event_type = str(data.get("type") or "")

    if event_type != "content_block_delta":
        return "", ""

    delta = data.get("delta")
    if delta is None:
        return "", ""

    delta_data = _to_mapping(delta)
    delta_type = str(delta_data.get("type") or "")
    if delta_type == "text_delta":
        text = str(delta_data.get("text") or "")
    elif delta_type == "thinking_delta":
        reasoning = str(delta_data.get("thinking") or delta_data.get("text") or "")

    return text, reasoning


def _extract_assistant_text(message: Any) -> Tuple[str, str]:
    text_parts = []
    reasoning_parts = []

    content = getattr(message, "content", None)
    if not content:
        fallback = getattr(message, "text", None)
        return (str(fallback or ""), "")

    for block in content:
        data = _to_mapping(block)
        block_type = str(data.get("type") or "")
        if block_type == "text":
            txt = str(data.get("text") or "")
            if txt:
                text_parts.append(txt)
        elif block_type in ("thinking", "redacted_thinking"):
            r = str(data.get("thinking") or data.get("text") or "")
            if r:
                reasoning_parts.append(r)

    return "".join(text_parts), "\n".join(reasoning_parts)


def _classify_error(message: str) -> str:
    low = str(message or "").lower()
    if "resume" in low or "session" in low and ("not found" in low or "invalid" in low or "expired" in low):
        return "resume_invalid"
    if "auth" in low or "api key" in low or "401" in low or "403" in low:
        return "auth_error"
    if "cancel" in low or "interrupt" in low:
        return "cancelled"
    if "rate" in low or "429" in low:
        return "rate_limited"
    return "sdk_error"


def _decode_data_url_image(data_url: str) -> Tuple[Optional[str], Optional[str]]:
    raw = str(data_url or "").strip()
    if not raw.startswith("data:") or "," not in raw:
        return None, None
    header, encoded = raw.split(",", 1)
    mime_type = "image/png"
    try:
        media = header[5:]
        if ";" in media:
            mime_type = media.split(";", 1)[0] or mime_type
        elif media:
            mime_type = media
    except Exception:
        pass
    return encoded or None, mime_type


class ClaudeSdkLoop:
    SERVER_NAME = "pymol_tools"

    def __init__(self, logger: Optional[Callable[..., None]] = None):
        self._log_fn = logger
        self._logger = logging.getLogger("pymol.ai.sdk")
        self._trace_stream = os.getenv("PYMOL_AI_TRACE_STREAM", "1") == "1"

    def _log(self, message: str, level: str = "INFO", **fields) -> None:
        if self._log_fn:
            self._log_fn(message, level=level, **fields)
            return
        parts = []
        for key, value in fields.items():
            parts.append("%s=%s" % (key, value))
        line = "[PyMolAI] %s %s" % (level.upper(), message)
        if parts:
            line += " | " + " ".join(parts)
        self._logger.log(getattr(logging, str(level).upper(), logging.INFO), line)

    def map_openrouter_env(self) -> Dict[str, str]:
        base = (
            os.getenv("ANTHROPIC_BASE_URL")
            or os.getenv("OPENROUTER_BASE_URL")
            or "https://openrouter.ai/api"
        )
        token = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("OPENROUTER_API_KEY") or ""

        os.environ.setdefault("ANTHROPIC_BASE_URL", base)
        if token:
            os.environ.setdefault("ANTHROPIC_AUTH_TOKEN", token)
        os.environ.setdefault("ANTHROPIC_API_KEY", "")
        self._log(
            "mapped OpenRouter env for Claude SDK",
            base_url=base,
            has_auth_token=bool(token),
        )

        return {
            "ANTHROPIC_BASE_URL": base,
            "ANTHROPIC_AUTH_TOKEN": token,
            "ANTHROPIC_API_KEY": "",
        }

    def build_tool_server(
        self,
        *,
        create_sdk_mcp_server,
        tool,
        run_command_tool: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        snapshot_tool: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    ):
        tool_seq = {"n": 0}

        def next_id(prefix: str) -> str:
            tool_seq["n"] += 1
            return "%s_%d" % (prefix, tool_seq["n"])

        @tool(
            "run_pymol_command",
            "Run a single PyMOL command in the current session.",
            {"command": str, "rationale": str},
        )
        async def run_pymol_command(args):
            payload = run_command_tool(next_id("run_pymol_command"), dict(args or {}))
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(payload, ensure_ascii=False),
                    }
                ]
            }

        @tool(
            "capture_viewer_snapshot",
            "Capture current PyMOL viewport screenshot and compact viewer state summary.",
            {"purpose": str},
        )
        async def capture_viewer_snapshot(args):
            snapshot_response = snapshot_tool(next_id("capture_viewer_snapshot"), dict(args or {}))
            payload = snapshot_response
            image_data_url = None
            if isinstance(snapshot_response, dict):
                payload = snapshot_response.get("payload", snapshot_response)
                image_data_url = snapshot_response.get("image_data_url")

            content = [
                {
                    "type": "text",
                    "text": json.dumps(payload, ensure_ascii=False),
                }
            ]
            image_data, mime_type = _decode_data_url_image(str(image_data_url or ""))
            if image_data:
                content.append(
                    {
                        "type": "image",
                        "data": image_data,
                        "mimeType": mime_type or "image/png",
                    }
                )
            return {
                "content": content
            }

        # Agent SDK custom tool registration is done through an in-process MCP server
        # config returned by create_sdk_mcp_server(..., tools=[...]).
        return create_sdk_mcp_server(
            name=self.SERVER_NAME,
            version="1.0.0",
            tools=[run_pymol_command, capture_viewer_snapshot],
        )

    async def _run_turn_async(
        self,
        *,
        prompt: str,
        model: str,
        system_prompt: str,
        max_turns: int,
        max_buffer_size: Optional[int],
        resume_session_id: Optional[str],
        on_text_chunk: Callable[[str], None],
        on_reasoning_chunk: Optional[Callable[[str], None]],
        should_cancel: Optional[Callable[[], bool]],
        run_command_tool: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        snapshot_tool: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    ) -> SdkTurnResult:
        symbols = _import_sdk_symbols()
        ClaudeAgentOptions = symbols["ClaudeCodeOptions"]
        ClaudeSDKClient = symbols["ClaudeSDKClient"]
        create_sdk_mcp_server = symbols["create_sdk_mcp_server"]
        tool = symbols["tool"]
        sdk_package = symbols.get("sdk_package", "unknown")

        mapped_env = self.map_openrouter_env()
        mcp_server = self.build_tool_server(
            create_sdk_mcp_server=create_sdk_mcp_server,
            tool=tool,
            run_command_tool=run_command_tool,
            snapshot_tool=snapshot_tool,
        )

        options = ClaudeAgentOptions(
            model=str(model or ""),
            system_prompt=system_prompt,
            max_turns=max(1, int(max_turns)),
            permission_mode="bypassPermissions",
            include_partial_messages=True,
            continue_conversation=True,
            resume=resume_session_id or None,
            mcp_servers={self.SERVER_NAME: mcp_server},
            allowed_tools=[
                "run_pymol_command",
                "capture_viewer_snapshot",
            ],
            env=mapped_env,
            cwd=os.getcwd(),
            max_buffer_size=max_buffer_size if max_buffer_size and max_buffer_size > 0 else None,
        )
        self._log(
            "starting sdk turn",
            sdk_package=sdk_package,
            model=model,
            max_turns=max_turns,
            max_buffer_size=max_buffer_size if max_buffer_size else "",
            resume_session_id=resume_session_id or "",
        )

        interrupted = False
        final_text = ""
        session_id = resume_session_id or None
        in_tool_use_block = False

        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt=prompt, session_id="default")

            async for message in client.receive_response():
                if should_cancel and should_cancel() and not interrupted:
                    interrupted = True
                    self._log("interrupt requested; calling sdk interrupt", level="WARNING")
                    await client.interrupt()

                cls_name = type(message).__name__
                if cls_name == "StreamEvent":
                    event = getattr(message, "event", message)
                    event_data = _to_mapping(event)
                    event_type = str(event_data.get("type") or "")
                    delta_data = _to_mapping(event_data.get("delta"))
                    delta_type = str(delta_data.get("type") or "")
                    if self._trace_stream:
                        self._log(
                            "sdk stream event",
                            level="DEBUG",
                            event_type=event_type,
                            delta_type=delta_type,
                            in_tool_use_block=in_tool_use_block,
                        )

                    if event_type == "content_block_start":
                        block_data = _to_mapping(event_data.get("content_block"))
                        if str(block_data.get("type") or "") == "tool_use":
                            in_tool_use_block = True
                            if self._trace_stream:
                                self._log("sdk tool_use block started", level="DEBUG")
                        continue

                    if event_type == "content_block_stop":
                        if in_tool_use_block:
                            in_tool_use_block = False
                            if self._trace_stream:
                                self._log("sdk tool_use block ended", level="DEBUG")
                        continue

                    text, reasoning = _extract_stream_chunks(message)
                    if text:
                        if self._trace_stream:
                            self._log(
                                "sdk text chunk",
                                level="DEBUG",
                                chars=len(text),
                                preview=text[:120],
                                in_tool_use_block=in_tool_use_block,
                            )
                        on_text_chunk(text)
                    if reasoning and on_reasoning_chunk:
                        on_reasoning_chunk(reasoning)
                    continue

                if cls_name == "AssistantMessage":
                    a_text, a_reasoning = _extract_assistant_text(message)
                    if self._trace_stream:
                        self._log(
                            "sdk assistant message",
                            level="DEBUG",
                            text_chars=len(a_text),
                            reasoning_chars=len(a_reasoning),
                        )
                    if a_text:
                        final_text = a_text
                    if a_reasoning and on_reasoning_chunk:
                        on_reasoning_chunk(a_reasoning)
                    continue

                if cls_name == "ResultMessage":
                    sid = getattr(message, "session_id", None)
                    if sid:
                        session_id = str(sid)
                    if self._trace_stream:
                        self._log(
                            "sdk result message",
                            level="DEBUG",
                            session_id=session_id or "",
                            is_error=bool(getattr(message, "is_error", False)),
                        )
                    if bool(getattr(message, "is_error", False)):
                        err_text = str(getattr(message, "result", "") or "SDK turn failed")
                        self._log("sdk result message reported error", level="ERROR", error=err_text)
                        return SdkTurnResult(
                            assistant_text=final_text,
                            session_id=session_id,
                            error=err_text,
                            error_class=_classify_error(err_text),
                            interrupted=interrupted,
                        )
        self._log(
            "sdk turn finished",
            interrupted=interrupted,
            session_id=session_id or "",
            final_text_chars=len(final_text or ""),
        )

        return SdkTurnResult(
            assistant_text=final_text,
            session_id=session_id,
            error=None,
            error_class=None,
            interrupted=interrupted,
        )

    def run_turn(self, **kwargs) -> SdkTurnResult:
        try:
            return asyncio.run(self._run_turn_async(**kwargs))
        except ClaudeSdkLoopError as exc:
            self._log("sdk unavailable", level="ERROR", error=exc)
            return SdkTurnResult(error=str(exc), error_class=exc.error_class)
        except Exception as exc:  # noqa: BLE001
            self._log("sdk runtime exception", level="ERROR", error=exc)
            return SdkTurnResult(error=str(exc), error_class=_classify_error(str(exc)))
