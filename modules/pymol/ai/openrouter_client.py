from __future__ import annotations

import asyncio
import json
import os
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from .message_types import ToolCall

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-sonnet-4"


class OpenRouterClientError(RuntimeError):
    pass


class ChatParseError(ValueError):
    pass


def _delta_text(delta) -> str:
    if not delta:
        return ""

    content = getattr(delta, "content", None)
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        chunks = []
        for item in content:
            text = getattr(item, "text", None)
            if text:
                chunks.append(text)
        return "".join(chunks)

    return ""


def build_multimodal_user_content(text: str, image_data_url: Optional[str]) -> List[Dict[str, object]]:
    parts: List[Dict[str, object]] = [{"type": "text", "text": text}]
    if image_data_url:
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": image_data_url},
            }
        )
    return parts


class OpenRouterClient:
    def __init__(self, api_key: str, base_url: Optional[str] = None):
        if not api_key:
            raise OpenRouterClientError("OPENROUTER_API_KEY is required")
        self.api_key = api_key
        self.base_url = base_url or os.getenv("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL

    async def _stream_chat_completion(
        self,
        *,
        model: str,
        messages: Iterable[Dict[str, object]],
        tools: Optional[List[Dict[str, object]]],
        on_text_chunk: Callable[[str], None],
        on_reasoning_chunk: Optional[Callable[[str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, object]:
        try:
            from openai import AsyncOpenAI
        except Exception as exc:  # noqa: BLE001
            raise OpenRouterClientError(
                "Missing dependency 'openai'. Install with: uv pip install openai"
            ) from exc

        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

        req: Dict[str, object] = {
            "model": model,
            "messages": list(messages),
            "temperature": 0.2,
            "stream": True,
        }
        if tools:
            req["tools"] = tools
            req["parallel_tool_calls"] = False

        try:
            stream = await client.chat.completions.create(**req)
        except Exception as exc:  # noqa: BLE001
            raise OpenRouterClientError(str(exc)) from exc

        text_chunks: List[str] = []
        reasoning_chunks: List[str] = []
        tool_calls_accum: Dict[int, Dict[str, object]] = {}

        async for event in stream:
            if should_cancel and should_cancel():
                break

            choices = getattr(event, "choices", None) or []
            if not choices:
                continue

            choice = choices[0]
            delta = getattr(choice, "delta", None)
            if not delta:
                continue

            text = _delta_text(delta)
            if text:
                text_chunks.append(text)
                on_text_chunk(text)

            reasoning = getattr(delta, "reasoning", None)
            if isinstance(reasoning, str) and reasoning:
                reasoning_chunks.append(reasoning)
                if on_reasoning_chunk:
                    on_reasoning_chunk(reasoning)

            delta_tool_calls = getattr(delta, "tool_calls", None) or []
            for tc in delta_tool_calls:
                idx = getattr(tc, "index", None)
                if idx is None:
                    continue

                existing = tool_calls_accum.get(
                    idx,
                    {
                        "id": None,
                        "type": "function",
                        "function": {"name": None, "arguments": ""},
                    },
                )

                tc_id = getattr(tc, "id", None)
                if tc_id:
                    existing["id"] = tc_id

                fn = getattr(tc, "function", None)
                if fn is not None:
                    fn_name = getattr(fn, "name", None)
                    if fn_name:
                        existing["function"]["name"] = fn_name

                    fn_args = getattr(fn, "arguments", None)
                    if isinstance(fn_args, str) and fn_args:
                        existing["function"]["arguments"] += fn_args

                tool_calls_accum[idx] = existing

        tool_calls: List[ToolCall] = []
        for idx in sorted(tool_calls_accum.keys()):
            call = tool_calls_accum[idx]
            fn = call.get("function", {})
            name = str(fn.get("name") or "")
            arguments_json = str(fn.get("arguments") or "{}")
            try:
                arguments = json.loads(arguments_json)
                if not isinstance(arguments, dict):
                    arguments = {"value": arguments}
            except Exception:
                arguments = {"raw": arguments_json}

            call_id = str(call.get("id") or ("tool_%d" % idx))
            if name:
                tool_calls.append(
                    ToolCall(
                        tool_call_id=call_id,
                        name=name,
                        arguments=arguments,
                        arguments_json=arguments_json,
                    )
                )

        return {
            "assistant_text": "".join(text_chunks),
            "reasoning": "".join(reasoning_chunks),
            "tool_calls": tool_calls,
        }

    def stream_assistant_turn(
        self,
        *,
        model: str,
        messages: Iterable[Dict[str, object]],
        tools: Optional[List[Dict[str, object]]],
        on_text_chunk: Callable[[str], None],
        on_reasoning_chunk: Optional[Callable[[str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, object]:
        return asyncio.run(
            self._stream_chat_completion(
                model=model,
                messages=messages,
                tools=tools,
                on_text_chunk=on_text_chunk,
                on_reasoning_chunk=on_reasoning_chunk,
                should_cancel=should_cancel,
            )
        )
