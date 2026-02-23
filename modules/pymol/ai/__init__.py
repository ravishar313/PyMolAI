try:
    from .runtime import AiRuntime, get_ai_runtime
except Exception:
    AiRuntime = None  # type: ignore[assignment,misc]
    get_ai_runtime = None  # type: ignore[assignment]

__all__ = ["AiRuntime", "get_ai_runtime"]
