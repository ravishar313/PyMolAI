from __future__ import annotations

from pymol.ai.models import (
    DEFAULT_MODEL,
    canonical_default_model,
    is_supported_model,
    is_zhipuai_model,
    get_zhipuai_model_name,
)


class TestZhipuaiModelRouting:
    def test_zhipuai_model_detection(self):
        assert is_zhipuai_model("zhipuai/GLM-5") is True
        assert is_zhipuai_model("zhipuai/GLM-4.5") is True
        assert is_zhipuai_model("zhipuai/GLM-4.5-Air") is True
        assert is_zhipuai_model("zhipuai/GLM-4.6") is True
        assert is_zhipuai_model("zhipuai/GLM-4.7") is True
        assert is_zhipuai_model("zhipuai/GLM-5-Turbo") is True

    def test_non_zhipuai_models_not_detected(self):
        assert is_zhipuai_model("anthropic/claude-sonnet-4.6") is False
        assert is_zhipuai_model("google/gemini-3.1-pro-preview") is False
        assert is_zhipuai_model("z-ai/glm-5") is False
        assert is_zhipuai_model("openai/gpt-4o-mini") is False
        assert is_zhipuai_model("") is False
        assert is_zhipuai_model("custom-model") is False

    def test_get_zhipuai_model_name(self):
        assert get_zhipuai_model_name("zhipuai/GLM-5") == "GLM-5"
        assert get_zhipuai_model_name("zhipuai/GLM-4.5") == "GLM-4.5"
        assert get_zhipuai_model_name("zhipuai/GLM-4.5-Air") == "GLM-4.5-Air"
        assert get_zhipuai_model_name("zhipuai/GLM-4.6") == "GLM-4.6"
        assert get_zhipuai_model_name("zhipuai/GLM-4.7") == "GLM-4.7"
        assert get_zhipuai_model_name("zhipuai/GLM-5-Turbo") == "GLM-5-Turbo"

    def test_get_zhipuai_model_name_fallback(self):
        assert (
            get_zhipuai_model_name("anthropic/claude-sonnet-4.6")
            == "anthropic/claude-sonnet-4.6"
        )
        assert get_zhipuai_model_name("unknown-model") == "unknown-model"
        assert get_zhipuai_model_name("") == ""

    def test_zhipuai_models_are_supported(self):
        assert is_supported_model("zhipuai/GLM-5") is True
        assert is_supported_model("zhipuai/GLM-4.5") is True
        assert is_supported_model("zhipuai/GLM-4.5-Air") is True
        assert is_supported_model("zhipuai/GLM-4.6") is True
        assert is_supported_model("zhipuai/GLM-4.7") is True
        assert is_supported_model("zhipuai/GLM-5-Turbo") is True
