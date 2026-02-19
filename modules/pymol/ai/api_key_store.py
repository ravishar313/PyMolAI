from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Optional, Tuple

from .openrouter_client import DEFAULT_BASE_URL, DEFAULT_MODEL

SERVICE_NAME = "pymol.ai"
ACCOUNT_NAME = "openrouter_api_key"

_ENV_OPENROUTER_KEY = "OPENROUTER_API_KEY"
_ENV_ANTHROPIC_TOKEN = "ANTHROPIC_AUTH_TOKEN"
_ENV_KEY_SOURCE = "PYMOL_AI_OPENROUTER_KEY_SOURCE"
_ENV_KEY_SOURCE_SAVED = "saved_keyring"


class ApiKeyStoreError(RuntimeError):
    pass


class ApiKeyValidationError(ApiKeyStoreError):
    pass


@dataclass
class ApiKeyStatus:
    has_key: bool
    source: Literal["env", "saved", "none"]
    masked_key: str
    keyring_available: bool


def _sanitize_error_message(text: str, key: str) -> str:
    message = str(text or "").strip() or "Unknown error"
    if key:
        message = message.replace(key, "***")
    return message


def _mask_key(key: str) -> str:
    raw = str(key or "").strip()
    if not raw:
        return ""
    suffix = raw[-4:] if len(raw) >= 4 else raw
    return "****%s" % (suffix,)


def _env_key() -> str:
    return str(os.getenv(_ENV_OPENROUTER_KEY) or os.getenv(_ENV_ANTHROPIC_TOKEN) or "").strip()


def _load_keyring() -> Tuple[Optional[object], bool]:
    try:
        import keyring  # type: ignore[import-not-found]
    except Exception:
        return None, False

    try:
        backend = keyring.get_keyring()
        priority = float(getattr(backend, "priority", 0.0))
    except Exception:
        priority = 0.0
    return keyring, bool(priority > 0.0)


def _get_saved_key() -> str:
    keyring_mod, available = _load_keyring()
    if keyring_mod is None or not available:
        return ""
    try:
        value = keyring_mod.get_password(SERVICE_NAME, ACCOUNT_NAME)
    except Exception:
        return ""
    return str(value or "").strip()


def _require_keyring() -> object:
    keyring_mod, available = _load_keyring()
    if keyring_mod is None:
        raise ApiKeyStoreError(
            "Secure key storage requires the 'keyring' package, but it is unavailable."
        )
    if not available:
        raise ApiKeyStoreError(
            "No system keyring backend is available. Configure an OS keychain and retry."
        )
    return keyring_mod


def get_status() -> ApiKeyStatus:
    env_key = _env_key()
    _, keyring_available = _load_keyring()
    if env_key:
        return ApiKeyStatus(
            has_key=True,
            source="env",
            masked_key=_mask_key(env_key),
            keyring_available=keyring_available,
        )

    saved_key = _get_saved_key()
    if saved_key:
        return ApiKeyStatus(
            has_key=True,
            source="saved",
            masked_key=_mask_key(saved_key),
            keyring_available=keyring_available,
        )

    return ApiKeyStatus(
        has_key=False,
        source="none",
        masked_key="",
        keyring_available=keyring_available,
    )


def save_key(key: str) -> None:
    value = str(key or "").strip()
    if not value:
        raise ApiKeyStoreError("API key cannot be empty.")

    keyring_mod = _require_keyring()
    try:
        keyring_mod.set_password(SERVICE_NAME, ACCOUNT_NAME, value)
    except Exception as exc:  # noqa: BLE001
        raise ApiKeyStoreError("Failed to save API key to system keychain.") from exc


def clear_saved_key() -> None:
    keyring_mod = _require_keyring()
    try:
        keyring_mod.delete_password(SERVICE_NAME, ACCOUNT_NAME)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc or "").lower()
        if "not found" in msg or "no such password" in msg:
            return
        raise ApiKeyStoreError("Failed to clear API key from system keychain.") from exc


def load_saved_key_into_env_if_needed() -> ApiKeyStatus:
    env_key = _env_key()
    _, keyring_available = _load_keyring()
    if env_key:
        return ApiKeyStatus(
            has_key=True,
            source="env",
            masked_key=_mask_key(env_key),
            keyring_available=keyring_available,
        )

    saved_key = _get_saved_key()
    if not saved_key:
        os.environ.pop(_ENV_KEY_SOURCE, None)
        return ApiKeyStatus(
            has_key=False,
            source="none",
            masked_key="",
            keyring_available=keyring_available,
        )

    os.environ[_ENV_OPENROUTER_KEY] = saved_key
    os.environ[_ENV_KEY_SOURCE] = _ENV_KEY_SOURCE_SAVED
    return ApiKeyStatus(
        has_key=True,
        source="saved",
        masked_key=_mask_key(saved_key),
        keyring_available=keyring_available,
    )


def clear_saved_key_and_loaded_env_if_needed() -> bool:
    saved_key = _get_saved_key()
    clear_saved_key()

    current = str(os.getenv(_ENV_OPENROUTER_KEY) or "").strip()
    source = str(os.getenv(_ENV_KEY_SOURCE) or "").strip()
    if saved_key and current and source == _ENV_KEY_SOURCE_SAVED and current == saved_key:
        os.environ.pop(_ENV_OPENROUTER_KEY, None)
        os.environ.pop(_ENV_KEY_SOURCE, None)
        return True
    return False


def validate_key_live(key: str, model: str, timeout_sec: float = 10.0) -> None:
    value = str(key or "").strip()
    if not value:
        raise ApiKeyValidationError("API key is empty.")
    model_id = str(model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    client = None

    try:
        try:
            from openai import OpenAI
        except Exception as exc:  # noqa: BLE001
            raise ApiKeyValidationError(
                "Live validation requires the 'openai' package, which is unavailable."
            ) from exc

        base_url = str(os.getenv("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
        client = OpenAI(api_key=value, base_url=base_url, timeout=float(max(0.1, timeout_sec)))
        client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001
        raise ApiKeyValidationError(_sanitize_error_message(str(exc), value)) from exc
    finally:
        closer = getattr(client, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass
