# Change log:
# Fri Nov 20 01:39:30 CET 2026 - Initial version: minimal MCP streamable-HTTP client (JSON-RPC over requests)
# Fri Nov 20 01:39:30 CET 2026 - Added tool helpers is_read_only() and required_args()
# Fri Nov 20 02:59:39 CET 2026 - Prefix MCP HTTP errors with GCM MCP server identifier for clear attribution
# Fri Nov 20 03:55:54 CET 2026 - Dynamic tool discovery header (x-mcp-enable-discovery) is now optional, off by default
"""Minimal MCP client for the Guardium Cryptography Manager (streamable HTTP transport).

Implements initialize -> notifications/initialized -> tools/list / tools/call over a single
endpoint, accepting either JSON or SSE responses. Uses `requests` so SSL verification is controllable.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import requests
import urllib3

from utils.logging_setup import get_logger
from utils.net import TIMEOUT, describe_request_error, http_status_message

logger = get_logger()

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "gcm-streamlit", "version": "1.0.0"}
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MAX_TOOL_PAGES = 20


class MCPError(Exception):
    """Raised for any MCP failure. `message` is safe to show; `detail` only under DEBUG."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


_READ_ONLY_NAME_RE = re.compile(r"^(get|list|read|search|find|describe|query|show|count|fetch|view)([_\-A-Z]|$)")


# Fri Nov 20 01:39:30 CET 2026 - True if a tool is declared (readOnlyHint) or named like a read-only operation
def is_read_only(tool: dict) -> bool:
    annotations = tool.get("annotations") or {}
    if isinstance(annotations, dict) and "readOnlyHint" in annotations:
        return annotations["readOnlyHint"] is True
    return bool(_READ_ONLY_NAME_RE.match(str(tool.get("name", ""))))


# Fri Nov 20 01:39:30 CET 2026 - Names of required input arguments of a tool
def required_args(tool: dict) -> list[str]:
    schema = tool.get("inputSchema") or {}
    required = schema.get("required") if isinstance(schema, dict) else None
    return [str(r) for r in required] if isinstance(required, list) else []


@dataclass
class ConnectionResult:
    ok: bool
    message: str
    detail: str = ""
    server_name: str = ""
    server_version: str = ""
    tools: list[dict] | None = None


# Fri Nov 20 01:39:30 CET 2026 - Headers required by the GCM MCP server (token masked when requested)
def build_headers(token: str, *, mask: bool = False, dynamic_tools: bool = False) -> dict[str, str]:
    shown = "Bearer ********" if mask else f"Bearer {token}"
    headers = {"Authorization": shown, "token_type": "api_key"}
    if dynamic_tools:
        headers["x-mcp-enable-discovery"] = "true"
    return headers


# Fri Nov 20 01:39:30 CET 2026 - MCP server configuration structure (as used by MCP clients)
def build_server_config(
    endpoint: str, token: str, verify_ssl: bool, *, mask: bool = True, dynamic_tools: bool = False
) -> dict:
    return {
        "mcpServers": {
            "guardium-cryptography-manager": {
                "type": "streamable-http",
                "url": endpoint,
                "headers": build_headers(token, mask=mask, dynamic_tools=dynamic_tools),
                "verify_ssl": verify_ssl,
            }
        }
    }


# Fri Nov 20 01:39:30 CET 2026 - Parse a JSON or SSE body and return the JSON-RPC message for `req_id`
def parse_rpc_body(body: str, content_type: str, req_id: int) -> dict:
    candidates: list[dict] = []
    if "text/event-stream" in content_type:
        for event in body.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(line[5:].lstrip() for line in event.split("\n") if line.startswith("data:"))
            if data:
                try:
                    candidates.append(json.loads(data))
                except ValueError:
                    continue
    else:
        try:
            parsed = json.loads(body)
        except ValueError as exc:
            raise MCPError("The server returned an invalid response.", str(exc)) from exc
        candidates = parsed if isinstance(parsed, list) else [parsed]
    for msg in candidates:
        if isinstance(msg, dict) and msg.get("id") == req_id and ("result" in msg or "error" in msg):
            return msg
    raise MCPError("The server returned no response for the request.")


# Fri Nov 20 01:39:30 CET 2026 - Convert a tools/call result into a Python payload (JSON when possible)
def extract_payload(result: dict) -> Any:
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    parts: list[Any] = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text = item.get("text", "")
            try:
                parts.append(json.loads(text))
            except ValueError:
                parts.append(text)
        else:
            parts.append({"type": item.get("type"), "note": "non-text content omitted"})
    return parts[0] if len(parts) == 1 else parts


class MCPClient:
    """Context-managed MCP session: `with MCPClient(...) as c: c.list_tools()`."""

    def __init__(self, endpoint: str, token: str, verify_ssl: bool = True, dynamic_tools: bool = False):
        self.endpoint = endpoint
        self.verify = verify_ssl
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **build_headers(token, dynamic_tools=dynamic_tools),
        }
        self._http = requests.Session()
        self._next_id = 0
        self.server_info: dict = {}
        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def __enter__(self) -> "MCPClient":
        self.initialize()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # Fri Nov 20 01:39:30 CET 2026 - POST one JSON-RPC message and return the raw response
    def _post(self, payload: dict) -> requests.Response:
        try:
            resp = self._http.post(
                self.endpoint, json=payload, headers=self._headers, timeout=TIMEOUT,
                verify=self.verify, allow_redirects=False,
            )
        except requests.exceptions.RequestException as exc:
            message, detail = describe_request_error(exc)
            raise MCPError(message, detail) from exc
        if 300 <= resp.status_code < 400:
            raise MCPError("The server redirected the request; use the final server URL.")
        if resp.status_code >= 400:
            raise MCPError(f"GCM MCP server: {http_status_message(resp.status_code)}", resp.text[:500])
        if len(resp.content) > MAX_RESPONSE_BYTES:
            raise MCPError("The server response is too large.")
        return resp

    # Fri Nov 20 01:39:30 CET 2026 - Send a JSON-RPC request and return its `result`
    def _request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}
        resp = self._post(payload)
        msg = parse_rpc_body(resp.text, resp.headers.get("Content-Type", ""), self._next_id)
        if "error" in msg:
            err = msg["error"] if isinstance(msg["error"], dict) else {}
            raise MCPError("The MCP server rejected the request.", f"{method}: {err.get('code')} {err.get('message')}")
        session_id = resp.headers.get("Mcp-Session-Id")
        if session_id:
            self._headers["Mcp-Session-Id"] = session_id
        return msg.get("result") or {}

    # Fri Nov 20 01:39:30 CET 2026 - MCP handshake (initialize + initialized notification)
    def initialize(self) -> dict:
        result = self._request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
        )
        self.server_info = result.get("serverInfo") or {}
        self._headers["MCP-Protocol-Version"] = str(result.get("protocolVersion") or PROTOCOL_VERSION)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    # Fri Nov 20 01:39:30 CET 2026 - List all tools (follows pagination cursors)
    def list_tools(self) -> list[dict]:
        tools: list[dict] = []
        cursor = None
        for _ in range(MAX_TOOL_PAGES):
            result = self._request("tools/list", {"cursor": cursor} if cursor else {})
            tools += [t for t in result.get("tools", []) if isinstance(t, dict) and t.get("name")]
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    # Fri Nov 20 01:39:30 CET 2026 - Call a tool; returns (payload, is_error)
    def call_tool(self, name: str, arguments: dict | None = None) -> tuple[Any, bool]:
        result = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        return extract_payload(result), bool(result.get("isError"))

    # Fri Nov 20 01:39:30 CET 2026 - End the session (best effort)
    def close(self) -> None:
        try:
            if "Mcp-Session-Id" in self._headers:
                self._http.delete(self.endpoint, headers=self._headers, timeout=TIMEOUT, verify=self.verify)
        except requests.exceptions.RequestException:
            pass
        finally:
            self._http.close()


# Fri Nov 20 01:39:30 CET 2026 - Test the connection: handshake plus tool discovery
def test_connection(endpoint: str, token: str, verify_ssl: bool, dynamic_tools: bool = False) -> ConnectionResult:
    try:
        with MCPClient(endpoint, token, verify_ssl, dynamic_tools) as client:
            tools = client.list_tools()
            info = client.server_info
    except MCPError as exc:
        logger.warning("MCP connection test failed", extra={"endpoint": endpoint})
        return ConnectionResult(False, exc.message, exc.detail)
    return ConnectionResult(
        True,
        f"Connected. {len(tools)} tool(s) discovered.",
        server_name=str(info.get("name", "")),
        server_version=str(info.get("version", "")),
        tools=tools,
    )
