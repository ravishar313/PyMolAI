from __future__ import annotations

from typing import List, Tuple

DEFAULT_MODEL = "anthropic/claude-sonnet-4.6"

SUPPORTED_MODELS: Tuple[Tuple[str, str], ...] = (
    ("google/gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview"),
    ("anthropic/claude-sonnet-4.6", "Claude Sonnet 4.6"),
    ("z-ai/glm-5", "GLM-5"),
    ("minimax/minimax-m2.5", "MiniMax M2.5"),
    ("moonshotai/kimi-k2.5", "Kimi K2.5"),
    ("google/gemini-3-flash-preview", "Gemini 3 Flash Preview"),
    ("anthropic/claude-haiku-4.5", "Claude Haiku 4.5"),
    ("openai/gpt-5.2", "GPT-5.2")
)


def supported_model_ids() -> List[str]:
    return [model_id for model_id, _ in SUPPORTED_MODELS]


def model_menu_entries() -> List[Tuple[str, str]]:
    return list(SUPPORTED_MODELS)


def is_supported_model(model_id: str) -> bool:
    candidate = str(model_id or "").strip()
    if not candidate:
        return False
    return candidate in supported_model_ids()


def canonical_default_model() -> str:
    return DEFAULT_MODEL

