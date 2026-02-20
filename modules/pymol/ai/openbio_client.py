from __future__ import annotations

import json
import mimetypes
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

DEFAULT_BASE_URL = "https://api.openbio.tech"
DEFAULT_TIMEOUT_SEC = 30.0
_ENV_OPENBIO_KEY = "OPENBIO_API_KEY"
DEFAULT_USER_AGENT = "curl/8.6.0"


def _safe_realpath(base: str, value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return os.path.realpath(base)
    if os.path.isabs(text):
        return os.path.realpath(text)
    return os.path.realpath(os.path.join(base, text))


def _is_within_root(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)
    except Exception:
        return False


def _base_url_candidates() -> List[str]:
    configured = str(os.getenv("OPENBIO_BASE_URL") or "").strip()
    if configured:
        return [configured.rstrip("/")]
    return [DEFAULT_BASE_URL.rstrip("/")]


def _json_or_text(raw: bytes) -> object:
    if not raw:
        return {}
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except Exception:
        return text


def _extract_error_message(payload: object, fallback: str) -> str:
    if isinstance(payload, dict):
        for key in ("message", "detail", "error", "reason"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(payload, str):
        low = payload.lower()
        if "cloudflare" in low and ("error 1010" in low or "access denied" in low):
            return (
                "Access denied by OpenBio edge protection (Cloudflare Error 1010). "
                "Your network/IP or client signature is blocked."
            )
        if "invalid or revoked api key" in low:
            return "Invalid or revoked API key."
    return str(fallback or "request failed").strip()


def _multipart_body(fields: Dict[str, str], files: List[Tuple[str, str, bytes, str]]) -> Tuple[bytes, str]:
    boundary = "----PyMolAIOpenBio%s" % (uuid.uuid4().hex,)
    body = bytearray()
    bnd = boundary.encode("utf-8")
    crlf = b"\r\n"

    for name, value in fields.items():
        body.extend(b"--" + bnd + crlf)
        body.extend(('Content-Disposition: form-data; name="%s"' % (name,)).encode("utf-8"))
        body.extend(crlf + crlf)
        body.extend(str(value).encode("utf-8"))
        body.extend(crlf)

    for field_name, filename, blob, mime_type in files:
        body.extend(b"--" + bnd + crlf)
        body.extend(
            ('Content-Disposition: form-data; name="%s"; filename="%s"' % (field_name, filename)).encode("utf-8")
        )
        body.extend(crlf)
        body.extend(("Content-Type: %s" % (mime_type,)).encode("utf-8"))
        body.extend(crlf + crlf)
        body.extend(blob)
        body.extend(crlf)

    body.extend(b"--" + bnd + b"--" + crlf)
    return bytes(body), boundary


def _query_string(params: Dict[str, object]) -> str:
    encoded = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            encoded[key] = "true" if value else "false"
            continue
        encoded[key] = str(value)
    return urlparse.urlencode(encoded)


def _request(
    *,
    method: str,
    path: str,
    api_key: str = "",
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    query: Optional[Dict[str, object]] = None,
    json_body: Optional[Dict[str, object]] = None,
    multipart_fields: Optional[Dict[str, str]] = None,
    multipart_files: Optional[List[Tuple[str, str, bytes, str]]] = None,
) -> Dict[str, object]:
    req_path = "/" + str(path or "").lstrip("/")
    headers = {"Accept": "application/json"}
    headers["User-Agent"] = str(os.getenv("OPENBIO_USER_AGENT") or DEFAULT_USER_AGENT).strip() or DEFAULT_USER_AGENT
    if api_key:
        headers["X-API-Key"] = api_key

    data: Optional[bytes] = None
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif multipart_fields is not None or multipart_files is not None:
        data, boundary = _multipart_body(dict(multipart_fields or {}), list(multipart_files or []))
        headers["Content-Type"] = "multipart/form-data; boundary=%s" % (boundary,)

    explicit_base = bool(str(os.getenv("OPENBIO_BASE_URL") or "").strip())
    candidates = _base_url_candidates()
    last_error: Optional[Dict[str, object]] = None
    for idx, base in enumerate(candidates):
        url = base + req_path
        if query:
            qs = _query_string(dict(query))
            if qs:
                url += ("&" if "?" in url else "?") + qs
        req = urlrequest.Request(url=url, method=method.upper(), headers=headers, data=data)
        try:
            with urlrequest.urlopen(req, timeout=float(max(0.1, timeout_sec))) as response:
                raw = response.read()
                status = int(getattr(response, "status", response.getcode()) or 200)
                parsed = _json_or_text(raw)
                return {
                    "ok": True,
                    "status_code": status,
                    "data": parsed,
                    "base_url": base,
                }
        except urlerror.HTTPError as exc:
            raw = b""
            try:
                raw = exc.read()
            except Exception:
                pass
            parsed = _json_or_text(raw)
            result = {
                "ok": False,
                "status_code": int(getattr(exc, "code", 0) or 0),
                "error": _extract_error_message(parsed, str(exc)),
                "data": parsed,
                "base_url": base,
            }
            last_error = result
            if explicit_base or idx >= len(candidates) - 1:
                return result
            status_code = int(result.get("status_code") or 0)
            if status_code not in (401, 403, 404):
                return result
        except Exception as exc:  # noqa: BLE001
            result = {
                "ok": False,
                "status_code": 0,
                "error": str(exc),
                "data": {},
                "base_url": base,
            }
            last_error = result
            if explicit_base or idx >= len(candidates) - 1:
                return result
            continue

    return last_error or {
        "ok": False,
        "status_code": 0,
        "error": "request failed",
        "data": {},
    }


def _normalize_upload_files(
    args: Dict[str, Any],
    *,
    working_dir: str,
) -> Tuple[List[Tuple[str, str, bytes, str]], Optional[Dict[str, object]]]:
    items = args.get("upload_files")
    if items is None:
        items = args.get("upload_paths")
    if items is None:
        return [], None
    if isinstance(items, str):
        text = str(items or "").strip()
        if not text or text.lower() in ("null", "none"):
            return [], None
        try:
            items = json.loads(text)
        except Exception:
            return [], {
                "ok": False,
                "error_type": "invalid_upload_files",
                "error": "upload_files must be a list (or a JSON list string).",
            }
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return [], {
            "ok": False,
            "error_type": "invalid_upload_files",
            "error": "upload_files must be a list (or a JSON list string).",
        }

    resolved: List[Tuple[str, str, bytes, str]] = []
    root_dir = os.path.realpath(str(working_dir or os.getcwd()))
    for idx, item in enumerate(items):
        if isinstance(item, str):
            field_name = "file_%d" % (idx + 1,)
            raw_path = str(item or "").strip()
        elif isinstance(item, dict):
            field_name = str(item.get("field_name") or item.get("name") or "file_%d" % (idx + 1,)).strip()
            raw_path = str(
                item.get("path")
                or item.get("file_path")
                or item.get("local_path")
                or item.get("file")
                or ""
            ).strip()
        else:
            return [], {
                "ok": False,
                "error_type": "invalid_upload_files",
                "error": "upload_files entries must be path strings or objects with path.",
            }

        if not raw_path:
            # Be lenient with placeholder entries like [{}] to avoid looping.
            continue
        resolved_path = _safe_realpath(root_dir, raw_path)
        if not _is_within_root(resolved_path, root_dir):
            return [], {
                "ok": False,
                "error_type": "upload_path_outside_workspace",
                "error": "Upload path is outside the current working directory.",
                "path": raw_path,
                "resolved_path": resolved_path,
                "working_dir": root_dir,
            }
        if not os.path.exists(resolved_path):
            return [], {
                "ok": False,
                "error_type": "upload_file_not_found",
                "error": "Upload file does not exist.",
                "path": raw_path,
                "resolved_path": resolved_path,
            }
        if not os.path.isfile(resolved_path):
            return [], {
                "ok": False,
                "error_type": "upload_not_a_file",
                "error": "Upload path must point to a regular file.",
                "path": raw_path,
                "resolved_path": resolved_path,
            }
        with open(resolved_path, "rb") as handle:
            blob = handle.read()
        mime = mimetypes.guess_type(resolved_path)[0] or "application/octet-stream"
        resolved.append((field_name, os.path.basename(resolved_path), blob, mime))
    return resolved, None


def _normalize_params(args: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Dict[str, object]]]:
    params = args.get("params")
    if params is None:
        return {}, None
    if isinstance(params, dict):
        return dict(params), None
    if isinstance(params, str):
        text = str(params or "").strip()
        if not text:
            return {}, None
        try:
            parsed = json.loads(text)
        except Exception:
            return {}, {
                "ok": False,
                "error_type": "invalid_params",
                "error": "params must be an object (or a JSON object string).",
            }
        if isinstance(parsed, dict):
            return dict(parsed), None
        return {}, {
            "ok": False,
            "error_type": "invalid_params",
            "error": "params JSON must decode to an object.",
        }
    return {}, {
        "ok": False,
        "error_type": "invalid_params",
        "error": "params must be an object (or a JSON object string).",
    }


def _require_api_key(explicit_api_key: str = "") -> str:
    value = str(explicit_api_key or os.getenv(_ENV_OPENBIO_KEY) or "").strip()
    if not value:
        raise RuntimeError("OPENBIO_API_KEY is not set.")
    return value


def validate_key_live(api_key: str, timeout_sec: float = 10.0) -> None:
    key = _require_api_key(api_key)
    result = _request(
        method="GET",
        path="/api/v1/tools",
        api_key=key,
        timeout_sec=timeout_sec,
        query={"limit": 1, "offset": 0},
    )
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error") or "OpenBio key validation failed."))


def execute_openbio_api_gateway_tool(
    tool_name: str,
    tool_args: Dict[str, Any],
    *,
    working_dir: str,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
) -> Dict[str, object]:
    name = str(tool_name or "").strip()
    args = dict(tool_args or {})

    try:
        api_key = _require_api_key("")
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error_type": "missing_api_key",
            "error": str(exc),
        }

    if name == "openbio_api_health":
        return _request(method="GET", path="/api/v1/tools/health", timeout_sec=timeout_sec)

    if name == "openbio_api_list_tools":
        return _request(
            method="GET",
            path="/api/v1/tools",
            api_key=api_key,
            timeout_sec=timeout_sec,
            query={
                "limit": args.get("limit"),
                "offset": args.get("offset"),
                "category": args.get("category"),
            },
        )

    if name == "openbio_api_search_tools":
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error_type": "invalid_args", "error": "query is required."}
        return _request(
            method="GET",
            path="/api/v1/tools/search",
            api_key=api_key,
            timeout_sec=timeout_sec,
            query={"q": query},
        )

    if name == "openbio_api_list_categories":
        return _request(
            method="GET",
            path="/api/v1/tools/categories",
            api_key=api_key,
            timeout_sec=timeout_sec,
        )

    if name == "openbio_api_get_category":
        category_name = str(args.get("category_name") or "").strip()
        if not category_name:
            return {"ok": False, "error_type": "invalid_args", "error": "category_name is required."}
        return _request(
            method="GET",
            path="/api/v1/tools/categories/%s" % (urlparse.quote(category_name),),
            api_key=api_key,
            timeout_sec=timeout_sec,
        )

    if name == "openbio_api_get_tool_schema":
        remote_tool = str(args.get("tool_name") or "").strip()
        if not remote_tool:
            return {"ok": False, "error_type": "invalid_args", "error": "tool_name is required."}
        return _request(
            method="GET",
            path="/api/v1/tools/%s" % (urlparse.quote(remote_tool),),
            api_key=api_key,
            timeout_sec=timeout_sec,
        )

    if name == "openbio_api_validate_params":
        remote_tool = str(args.get("tool_name") or "").strip()
        if not remote_tool:
            return {"ok": False, "error_type": "invalid_args", "error": "tool_name is required."}
        params, params_error = _normalize_params(args)
        if params_error:
            return params_error
        return _request(
            method="POST",
            path="/api/v1/tools/validate",
            api_key=api_key,
            timeout_sec=timeout_sec,
            json_body={"tool_name": remote_tool, "params": params},
        )

    if name == "openbio_api_invoke_tool":
        remote_tool = str(args.get("tool_name") or "").strip()
        if not remote_tool:
            return {"ok": False, "error_type": "invalid_args", "error": "tool_name is required."}
        params, params_error = _normalize_params(args)
        if params_error:
            return params_error
        files, file_error = _normalize_upload_files(args, working_dir=working_dir)
        if file_error:
            return file_error
        return _request(
            method="POST",
            path="/api/v1/tools",
            api_key=api_key,
            timeout_sec=timeout_sec,
            multipart_fields={
                "tool_name": remote_tool,
                "params": json.dumps(params, ensure_ascii=False),
            },
            multipart_files=files,
        )

    if name == "openbio_api_list_jobs":
        return _request(
            method="GET",
            path="/api/v1/jobs",
            api_key=api_key,
            timeout_sec=timeout_sec,
            query={
                "limit": args.get("limit"),
                "offset": args.get("offset"),
                "status": args.get("status"),
                "tool": args.get("tool"),
                "compact": args.get("compact"),
            },
        )

    if name == "openbio_api_get_job_status":
        job_id = str(args.get("job_id") or "").strip()
        if not job_id:
            return {"ok": False, "error_type": "invalid_args", "error": "job_id is required."}
        return _request(
            method="GET",
            path="/api/v1/jobs/%s/status" % (urlparse.quote(job_id),),
            api_key=api_key,
            timeout_sec=timeout_sec,
        )

    if name == "openbio_api_get_job_result":
        job_id = str(args.get("job_id") or "").strip()
        if not job_id:
            return {"ok": False, "error_type": "invalid_args", "error": "job_id is required."}
        return _request(
            method="GET",
            path="/api/v1/jobs/%s" % (urlparse.quote(job_id),),
            api_key=api_key,
            timeout_sec=timeout_sec,
        )

    if name == "openbio_api_get_job_logs":
        job_id = str(args.get("job_id") or "").strip()
        if not job_id:
            return {"ok": False, "error_type": "invalid_args", "error": "job_id is required."}
        return _request(
            method="GET",
            path="/api/v1/jobs/%s/logs" % (urlparse.quote(job_id),),
            api_key=api_key,
            timeout_sec=timeout_sec,
        )

    return {
        "ok": False,
        "error_type": "unsupported_tool",
        "error": "Unsupported OpenBio gateway tool: %s" % (name or "<empty>",),
    }
