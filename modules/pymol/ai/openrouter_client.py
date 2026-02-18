from __future__ import annotations

import asyncio
import json
import os
from typing import Callable, Dict, Iterable, List

from .protocol import AiPlan

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-sonnet-4"
PLAN_JSON_START = "<PLAN_JSON>"
PLAN_JSON_END = "</PLAN_JSON>"


class OpenRouterClientError(RuntimeError):
    pass


class PlanParseError(ValueError):
    pass


def _delta_to_text(delta) -> str:
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


def _extract_json_block(text: str) -> Dict[str, object]:
    if PLAN_JSON_START in text and PLAN_JSON_END in text:
        s = text.split(PLAN_JSON_START, 1)[1].split(PLAN_JSON_END, 1)[0].strip()
        return json.loads(s)

    # fallback: best effort, parse last fenced/raw object
    start = text.rfind("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise PlanParseError("no JSON plan found in model response")
    return json.loads(text[start : end + 1])


def parse_plan_text(text: str) -> AiPlan:
    try:
        data = _extract_json_block(text)
    except Exception as exc:  # noqa: BLE001
        raise PlanParseError(str(exc)) from exc

    try:
        return AiPlan.from_dict(data)
    except Exception as exc:  # noqa: BLE001
        raise PlanParseError(str(exc)) from exc


class OpenRouterClient:
    def __init__(self, api_key: str, base_url: str | None = None):
        if not api_key:
            raise OpenRouterClientError("OPENROUTER_API_KEY is required")
        self.api_key = api_key
        self.base_url = base_url or os.getenv("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL

    async def _stream_chat(
        self,
        *,
        model: str,
        messages: Iterable[Dict[str, str]],
        on_chunk: Callable[[str], None],
    ) -> str:
        try:
            from openai import AsyncOpenAI
        except Exception as exc:  # noqa: BLE001
            raise OpenRouterClientError(
                "Missing dependency 'openai'. Install with: uv pip install openai"
            ) from exc

        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        response_stream = await client.chat.completions.create(
            model=model,
            temperature=0.2,
            stream=True,
            messages=list(messages),
        )

        chunks: List[str] = []
        async for event in response_stream:
            choices = getattr(event, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            text = _delta_to_text(delta)
            if text:
                chunks.append(text)
                on_chunk(text)

        return "".join(chunks)

    def stream_plan(
        self,
        *,
        model: str,
        messages: Iterable[Dict[str, str]],
        on_chunk: Callable[[str], None],
    ) -> AiPlan:
        text = asyncio.run(
            self._stream_chat(model=model, messages=messages, on_chunk=on_chunk)
        )
        return parse_plan_text(text)

    def stream_text(
        self,
        *,
        model: str,
        messages: Iterable[Dict[str, str]],
        on_chunk: Callable[[str], None],
    ) -> str:
        return asyncio.run(
            self._stream_chat(model=model, messages=messages, on_chunk=on_chunk)
        )
