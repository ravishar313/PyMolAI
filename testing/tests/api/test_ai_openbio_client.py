import io
import json
import sys
from pathlib import Path
from urllib import error as urlerror

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "modules" / "pymol" / "ai"))
import openbio_client  # noqa: E402


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.setenv("OPENBIO_API_KEY", "ob-test-key")
    monkeypatch.setenv("OPENBIO_BASE_URL", "https://api.openbio.tech")


def test_list_tools_builds_request_and_parses_json(monkeypatch, clean_env):
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["method"] = req.get_method()
        captured["timeout"] = timeout
        return _Response({"tools": [{"name": "search_pubmed"}], "total": 1})

    monkeypatch.setattr(openbio_client.urlrequest, "urlopen", fake_urlopen)
    result = openbio_client.execute_openbio_api_gateway_tool(
        "openbio_api_list_tools",
        {"category": "pubmed", "limit": 5, "offset": 0},
        working_dir="/tmp",
    )

    assert result["ok"] is True
    assert captured["method"] == "GET"
    assert "/api/v1/tools" in captured["url"]
    assert "category=pubmed" in captured["url"]
    assert "limit=5" in captured["url"]
    assert captured["headers"].get("X-api-key") == "ob-test-key"
    assert "Authorization" not in captured["headers"]
    assert captured["headers"].get("User-agent")


def test_invoke_tool_rejects_upload_outside_workspace(monkeypatch, clean_env):
    def fake_urlopen(_req, timeout=0):
        raise AssertionError("network call should not happen for invalid local path")

    monkeypatch.setattr(openbio_client.urlrequest, "urlopen", fake_urlopen)
    result = openbio_client.execute_openbio_api_gateway_tool(
        "openbio_api_invoke_tool",
        {
            "tool_name": "analyze_pdb_file",
            "params": {},
            "upload_files": [{"field_name": "pdb_file", "path": "/etc/hosts"}],
        },
        working_dir="/Users/ravi/pymol",
    )
    assert result["ok"] is False
    assert result["error_type"] == "upload_path_outside_workspace"


def test_invoke_tool_builds_multipart_payload(monkeypatch, clean_env, tmp_path):
    upload_file = tmp_path / "sample.pdb"
    upload_file.write_text("ATOM\n", encoding="utf-8")
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["method"] = req.get_method()
        captured["content_type"] = req.get_header("Content-type")
        body = req.data or b""
        captured["body"] = body.decode("utf-8", errors="ignore")
        return _Response({"success": True, "data": {"ok": True}})

    monkeypatch.setattr(openbio_client.urlrequest, "urlopen", fake_urlopen)
    result = openbio_client.execute_openbio_api_gateway_tool(
        "openbio_api_invoke_tool",
        {
            "tool_name": "analyze_pdb_file",
            "params": {"mode": "fast"},
            "upload_files": [{"field_name": "pdb_file", "path": str(upload_file)}],
        },
        working_dir=str(tmp_path),
    )

    assert result["ok"] is True
    assert captured["method"] == "POST"
    assert "multipart/form-data" in str(captured["content_type"] or "")
    assert 'name="tool_name"' in captured["body"]
    assert "analyze_pdb_file" in captured["body"]
    assert 'name="params"' in captured["body"]
    assert '"mode": "fast"' in captured["body"]
    assert 'name="pdb_file"; filename="sample.pdb"' in captured["body"]


def test_invoke_tool_accepts_json_string_params_and_upload_files_placeholder(monkeypatch, clean_env):
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["method"] = req.get_method()
        captured["body"] = (req.data or b"").decode("utf-8", errors="ignore")
        return _Response({"success": True, "data": {"ok": True}})

    monkeypatch.setattr(openbio_client.urlrequest, "urlopen", fake_urlopen)
    result = openbio_client.execute_openbio_api_gateway_tool(
        "openbio_api_invoke_tool",
        {
            "tool_name": "run_struct_similarity_query",
            "params": '{"entry_id":"5DEL","max_results":5}',
            "upload_files": "[]",
        },
        working_dir="/tmp",
    )
    assert result["ok"] is True
    assert captured["method"] == "POST"
    assert '"entry_id": "5DEL"' in captured["body"]
    assert '"max_results": 5' in captured["body"]


def test_invoke_tool_treats_empty_or_null_upload_files_as_absent(monkeypatch, clean_env):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        return _Response({"success": True, "data": {"ok": True}})

    monkeypatch.setattr(openbio_client.urlrequest, "urlopen", fake_urlopen)
    for upload_value in ("", "null", "none", "[{}]"):
        result = openbio_client.execute_openbio_api_gateway_tool(
            "openbio_api_invoke_tool",
            {
                "tool_name": "run_text_query",
                "params": {"query": "dhodh"},
                "upload_files": upload_value,
            },
            working_dir="/tmp",
        )
        assert result["ok"] is True
    assert calls["n"] == 4


def test_validate_params_accepts_json_string_params(monkeypatch, clean_env):
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["method"] = req.get_method()
        captured["body"] = (req.data or b"").decode("utf-8", errors="ignore")
        return _Response({"valid": True})

    monkeypatch.setattr(openbio_client.urlrequest, "urlopen", fake_urlopen)
    result = openbio_client.execute_openbio_api_gateway_tool(
        "openbio_api_validate_params",
        {
            "tool_name": "run_struct_similarity_query",
            "params": '{"entry_id":"5DEL"}',
        },
        working_dir="/tmp",
    )
    assert result["ok"] is True
    assert captured["method"] == "POST"
    assert '"entry_id": "5DEL"' in captured["body"]


def test_http_error_is_normalized(monkeypatch, clean_env):
    payload = {"detail": "Parameter validation failed"}

    def fake_urlopen(req, timeout=0):
        raise urlerror.HTTPError(
            req.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(json.dumps(payload).encode("utf-8")),
        )

    monkeypatch.setattr(openbio_client.urlrequest, "urlopen", fake_urlopen)
    result = openbio_client.execute_openbio_api_gateway_tool(
        "openbio_api_validate_params",
        {"tool_name": "search_pubmed", "params": {}},
        working_dir="/tmp",
    )

    assert result["ok"] is False
    assert result["status_code"] == 400
    assert "validation failed" in str(result["error"]).lower()


def test_cloudflare_1010_is_reported_as_network_block(monkeypatch, clean_env):
    html = "<html><title>Access denied</title>Cloudflare Error 1010</html>"

    def fake_urlopen(req, timeout=0):
        raise urlerror.HTTPError(
            req.full_url,
            403,
            "Forbidden",
            hdrs=None,
            fp=io.BytesIO(html.encode("utf-8")),
        )

    monkeypatch.setattr(openbio_client.urlrequest, "urlopen", fake_urlopen)
    result = openbio_client.execute_openbio_api_gateway_tool(
        "openbio_api_list_tools",
        {"limit": 1},
        working_dir="/tmp",
    )
    assert result["ok"] is False
    assert result["status_code"] == 403
    assert "cloudflare error 1010" in str(result["error"]).lower()
