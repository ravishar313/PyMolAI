from pymol.ai.models import (
    DEFAULT_MODEL,
    canonical_default_model,
    is_supported_model,
    model_menu_entries,
    supported_model_ids,
)


def test_supported_model_ids_order_and_default():
    assert supported_model_ids() == [
        "google/gemini-3.1-pro-preview",
        "anthropic/claude-sonnet-4.6",
        "z-ai/glm-5",
        "minimax/minimax-m2.5",
        "moonshotai/kimi-k2.5",
        "google/gemini-3-flash-preview",
        "anthropic/claude-haiku-4.5",
    ]
    assert DEFAULT_MODEL == "anthropic/claude-sonnet-4.6"
    assert canonical_default_model() == DEFAULT_MODEL


def test_model_menu_entries_are_friendly_plus_id_pairs():
    entries = model_menu_entries()
    assert entries[0] == ("google/gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview")
    assert entries[1] == ("anthropic/claude-sonnet-4.6", "Claude Sonnet 4.6")
    assert len(entries) == 7


def test_is_supported_model():
    assert is_supported_model("anthropic/claude-sonnet-4.6") is True
    assert is_supported_model("openai/gpt-4o-mini") is False
    assert is_supported_model("") is False

