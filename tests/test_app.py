# Change log:
# Fri Nov 20 01:39:30 CET 2026 - Initial version: validation, encrypted storage and MCP client tests
# Fri Nov 20 02:04:10 CET 2026 - Added autonomous AI agent engine tests (tool conversion, completions, multi-step turn)
# Fri Nov 20 02:46:01 CET 2026 - Added tests for large datasets and render_paginated_table
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

import pytest
import requests

from utils import config_store
from utils.agent_engine import (
    _unseen_tools, chat_completion, fetch_tool_catalog, run_agent_turn, tools_to_openai_schema,
)
from utils.mcp_client import MCPClient, MCPError, extract_payload, parse_rpc_body
from utils.mcp_client import test_connection as check_connection
from utils.render import csv_safe, parse_payload, render_paginated_table
from utils.validation import build_mcp_endpoint, validate_ai, validate_mcp, validate_token, validate_url
from utils.ai_providers import PROVIDERS

import pandas as pd


# ---- validation ------------------------------------------------------------
@pytest.mark.parametrize(
    "raw", ["", "ftp://host", "https://", "https://user:pw@host", "https://h/p?x=1", "https://h\n/x", "javascript:alert(1)"]
)
def test_validate_url_rejects(raw):
    assert validate_url(raw)[1]


def test_validate_url_normalizes():
    assert validate_url(" https://gcm.example.com:8443/ ") == ("https://gcm.example.com:8443", [])
    assert validate_url("http://x", require_https=True)[1]


def test_endpoint_not_duplicated():
    assert build_mcp_endpoint("https://h/") == "https://h/ibm/mcp/mcp"
    assert build_mcp_endpoint("https://h/ibm/mcp/mcp") == "https://h/ibm/mcp/mcp"


@pytest.mark.parametrize("raw", ["", "short", "abc def ghijk", "tok\r\nX-Evil: 1abc", "a" * 5000])
def test_validate_token_rejects_header_injection(raw):
    assert validate_token(raw)[1]


def test_validate_token_strips_bearer_prefix():
    assert validate_token("Bearer abcdefgh12345") == ("abcdefgh12345", [])


def test_validate_mcp_warnings():
    _, errors, warnings = validate_mcp("http://h", "abcdefgh12345", False)
    assert not errors and len(warnings) == 2


def test_validate_ai_ibm_requires_project():
    values = {"model": "ibm/granite-3-8b-instruct", "api_key": "k" * 20, "base_url": "https://x.example.com"}
    assert validate_ai("IBM", PROVIDERS["IBM"], values)[1]
    values["project_id"] = "abcd-1234-efgh"
    assert not validate_ai("IBM", PROVIDERS["IBM"], values)[1]


def test_validate_ai_rejects_http_and_bad_model():
    values = {"model": "bad model;", "api_key": "k" * 20, "base_url": "http://x"}
    assert len(validate_ai("OpenAI", PROVIDERS["OpenAI"], values)[1]) == 2


# ---- encrypted storage -----------------------------------------------------
@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GCM_APP_ENCRYPTION_KEY", raising=False)
    return tmp_path


def test_secrets_encrypted_at_rest_and_roundtrip(store):
    cfg = config_store.default_config()
    cfg["mcp"].update(server_url="https://h", bearer_token="super-secret-token-123")
    cfg["ai"]["providers"]["OpenAI"]["api_key"] = "sk-test-secret-456"
    config_store.save_config(cfg)
    raw = (store / "config.json").read_text()
    assert "super-secret-token-123" not in raw and "sk-test-secret-456" not in raw
    assert oct((store / "config.json").stat().st_mode & 0o777) == "0o600"
    assert oct((store / ".secret.key").stat().st_mode & 0o777) == "0o600"
    loaded = config_store.load_config()
    assert loaded["mcp"]["bearer_token"] == "super-secret-token-123"
    assert loaded["ai"]["providers"]["OpenAI"]["api_key"] == "sk-test-secret-456"


def test_plaintext_secret_on_disk_is_not_accepted(store):
    config_store.save_config(config_store.default_config())
    path = store / "config.json"
    data = json.loads(path.read_text())
    data["mcp"]["bearer_token"] = "plaintext-token-123"
    path.write_text(json.dumps(data))
    assert config_store.load_config()["mcp"]["bearer_token"] == ""


def test_corrupt_config_falls_back_to_defaults(store):
    (store / "config.json").write_text("{not json")
    assert config_store.load_config()["mcp"]["verify_ssl"] is True


def test_backup_restore_and_delete(store):
    cfg = config_store.default_config()
    cfg["mcp"]["server_url"] = "https://one"
    config_store.save_config(cfg)
    cfg["mcp"]["server_url"] = "https://two"
    config_store.save_config(cfg)
    assert config_store.restore_backup()
    assert config_store.load_config()["mcp"]["server_url"] == "https://one"
    config_store.clear_config()
    assert not (store / "config.json").exists()


# ---- MCP client ------------------------------------------------------------
def test_parse_sse_and_json():
    sse = 'event: message\ndata: {"jsonrpc":"2.0","id":3,"result":{"ok":true}}\n\n'
    assert parse_rpc_body(sse, "text/event-stream", 3)["result"] == {"ok": True}
    assert parse_rpc_body('{"jsonrpc":"2.0","id":1,"result":{}}', "application/json", 1)["id"] == 1
    with pytest.raises(MCPError):
        parse_rpc_body("not json", "application/json", 1)
    with pytest.raises(MCPError):
        parse_rpc_body(sse, "text/event-stream", 99)


def test_extract_payload():
    assert extract_payload({"structuredContent": {"a": 1}}) == {"a": 1}
    assert extract_payload({"content": [{"type": "text", "text": '[{"x":1}]'}]}) == [{"x": 1}]
    assert extract_payload({"content": [{"type": "text", "text": "hello"}]}) == "hello"


class _FakeMCP(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def log_message(self, *args):
        pass

    def do_DELETE(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeMCP.seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
        if self.headers.get("Authorization") != "Bearer good-token-123":
            self.send_response(401)
            self.end_headers()
            return
        method, rid = body.get("method"), body.get("id")
        if rid is None:
            self.send_response(202)
            self.end_headers()
            return
        results = {
            "initialize": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "fake", "version": "1"}},
            "tools/list": {"tools": [{"name": "list_keys", "description": "d"}]},
            "tools/call": {"content": [{"type": "text", "text": json.dumps([{"k": "a"}, {"k": "b"}])}]},
        }
        payload = json.dumps({"jsonrpc": "2.0", "id": rid, "result": results[method]})
        if method == "tools/call":  # exercise the SSE path
            data, ctype = f"event: message\ndata: {payload}\n\n", "text/event-stream"
        else:
            data, ctype = payload, "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Mcp-Session-Id", "sess-1")
        self.end_headers()
        self.wfile.write(data.encode())


@pytest.fixture
def fake_server():
    _FakeMCP.seen = []
    srv = HTTPServer(("127.0.0.1", 0), _FakeMCP)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/ibm/mcp/mcp"
    srv.shutdown()


def test_client_flow_and_required_headers(fake_server):
    result = check_connection(fake_server, "good-token-123", True)
    assert result.ok and result.server_name == "fake" and result.tools[0]["name"] == "list_keys"
    first = _FakeMCP.seen[0]["headers"]
    assert first["Authorization"] == "Bearer good-token-123"
    assert first["token_type"] == "api_key" and first["x-mcp-enable-discovery"] == "true"
    assert any(m["headers"].get("Mcp-Session-Id") == "sess-1" for m in _FakeMCP.seen[1:])
    with MCPClient(fake_server, "good-token-123") as client:
        payload, is_error = client.call_tool("list_keys")
    assert payload == [{"k": "a"}, {"k": "b"}] and not is_error


def test_client_bad_token_gives_generic_message(fake_server):
    result = check_connection(fake_server, "bad-token-12345", True)
    assert not result.ok and "401" in result.message and "bad-token" not in result.message


def test_unreachable_server_is_reported():
    result = check_connection("http://127.0.0.1:9/ibm/mcp/mcp", "good-token-123", True)
    assert not result.ok and "127.0.0.1" not in result.message


# ---- rendering -------------------------------------------------------------
def test_parse_payload_shapes():
    p = parse_payload({"total": 2, "items": [{"a": 1, "n": {"x": 2}, "l": [1]}], "meta": {"v": "1"}})
    assert p.metrics == {"total": 2, "meta.v": "1"}
    assert sorted(p.tables["items"].columns) == ["a", "l", "n.x"]
    assert len(parse_payload([1, 2]).tables["Records"]) == 2
    assert parse_payload("hi").text == "hi"


def test_csv_formula_injection_neutralized():
    out = csv_safe(pd.DataFrame({"a": ["=1+1", "ok", "@x"]}))
    assert list(out["a"]) == ["'=1+1", "ok", "'@x"]


# Fri Nov 20 02:46:01 CET 2026 - Verify parse_payload preserves all records in a large 407-item dataset
def test_parse_payload_preserves_large_dataset():
    records = [{"id": f"asset_{i}", "name": f"Business Asset {i}", "type": "App"} for i in range(407)]
    p = parse_payload({"business_applications": records, "total_count": 407})
    assert p.metrics["total_count"] == 407
    assert "business_applications" in p.tables
    assert len(p.tables["business_applications"]) == 407


# Fri Nov 20 02:46:01 CET 2026 - Verify render_paginated_table renders dataframe for small and large datasets
def test_render_paginated_table_execution():
    df_small = pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})
    df_large = pd.DataFrame({"id": list(range(407)), "name": [f"item_{i}" for i in range(407)]})

    with patch("streamlit.dataframe") as mock_df, \
         patch("streamlit.selectbox", return_value=50) as mock_sb, \
         patch("streamlit.columns", return_value=[MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()]):
        # Small table renders directly
        render_paginated_table(df_small, "test_small", "tool_a", "Records")
        assert mock_df.called

        mock_df.reset_mock()
        # Large table renders with pagination
        render_paginated_table(df_large, "test_large", "tool_b", "Business Assets", default_page_size=50)
        assert mock_df.called
        # Verify rendered page size is sliced to 50
        rendered_df = mock_df.call_args[0][0]
        assert len(rendered_df) == 50


# ---- agent engine ----------------------------------------------------------
def test_tools_to_openai_schema():
    mcp_tools = [
        {"name": "list_keys", "description": "List all active cryptographic keys", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
        {"name": "bad_tool_no_schema"},
    ]
    schemas = tools_to_openai_schema(mcp_tools)
    assert len(schemas) == 2
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "list_keys"
    assert "limit" in schemas[0]["function"]["parameters"]["properties"]
    assert schemas[1]["function"]["parameters"] == {"type": "object", "properties": {}}


# Fri Nov 20 02:21:25 CET 2026 - Dynamic discovery helpers: catalog parsing and schema-before-execute detection
def test_fetch_tool_catalog_parses_names_only_in_discovery_mode():
    client = MagicMock()
    client.call_tool.return_value = ({"result": "### get_a\n\ndesc\n\n- `x` (string)\n\n### fetch_b\n\ndesc"}, False)
    assert fetch_tool_catalog(client, [{"name": "list_tools"}, {"name": "execute"}]) == ["get_a", "fetch_b"]
    assert fetch_tool_catalog(client, [{"name": "list_keys"}]) == []  # direct-tool server: no catalog
    client.call_tool.side_effect = MCPError("boom")
    assert fetch_tool_catalog(client, [{"name": "list_tools"}]) == []


def test_unseen_tools_detects_call_tool_names():
    code = 'a = await call_tool("get_a", {})\nb = await call_tool( \'fetch_b\', {"p": 1})\nreturn [a, b]'
    assert _unseen_tools(code, set()) == ["get_a", "fetch_b"]
    assert _unseen_tools(code, {"get_a"}) == ["fetch_b"]
    assert _unseen_tools("return 1", set()) == []


def test_chat_completion_requires_api_key():
    with pytest.raises(ValueError, match="No API key configured"):
        chat_completion("OpenAI", {"model": "gpt-4.1-mini", "api_key": ""}, [{"role": "user", "content": "hi"}])


def test_chat_completion_openai_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "Here is the key list."}}]
    }
    with patch("requests.post", return_value=mock_resp) as mock_post:
        msg = chat_completion(
            "1min.ai",
            {"model": "gpt-4.1-mini", "api_key": "k123", "base_url": "https://api.1min.ai/openai/v1"},
            [{"role": "user", "content": "Show keys"}],
        )
        assert msg["content"] == "Here is the key list."
        mock_post.assert_called_once()
        sent_body = mock_post.call_args[1]["json"]
        assert sent_body["model"] == "gpt-4.1-mini"


def test_run_agent_turn_with_tool_call(fake_server):
    # Step 1: Model requests tool call
    step1_resp = MagicMock()
    step1_resp.status_code = 200
    step1_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {"name": "list_keys", "arguments": "{}"},
                        }
                    ],
                }
            }
        ]
    }

    # Step 2: Model receives tool result and produces final answer with attribution
    step2_resp = MagicMock()
    step2_resp.status_code = 200
    step2_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Retrieved 2 cryptographic keys via `list_keys`: key a and key b.",
                }
            }
        ]
    }

    steps_recorded = []

    def callback(desc, detail=None):
        steps_recorded.append(desc)

    with patch("requests.post", side_effect=[step1_resp, step2_resp]):
        answer, executed, messages = run_agent_turn(
            endpoint=fake_server,
            token="good-token-123",
            verify=True,
            provider="1min.ai",
            ai_settings={"model": "gpt-4.1-mini", "api_key": "k123", "base_url": "https://api.1min.ai/openai/v1"},
            mcp_tools=[{"name": "list_keys", "description": "List keys"}],
            conversation_history=[{"role": "user", "content": "What keys are available?"}],
            on_step=callback,
        )

    assert "Retrieved 2 cryptographic keys via `list_keys`" in answer
    assert len(executed) == 1
    assert executed[0]["tool"] == "list_keys"
    assert executed[0]["payload"] == [{"k": "a"}, {"k": "b"}]
    assert any("Calling GCM tool: `list_keys`" in s for s in steps_recorded)
