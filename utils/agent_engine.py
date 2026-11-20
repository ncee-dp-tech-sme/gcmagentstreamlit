# Change log:
# Fri Nov 20 03:57:59 CET 2026 - Strip nested property descriptions from IBM tool schemas to avoid "function definitions not comprehensive" error
# Fri Nov 20 03:54:14 CET 2026 - Fix IBM tool calling: use tool_choice_option, max_tokens; Granite-3-3-8b has no tool support
# Fri Nov 20 03:50:13 CET 2026 - Fix IBM watsonx.ai 404: use /ml/v1/text/chat?version=2024-10-08 and model_id field
# Fri Nov 20 02:02:53 CET 2026 - Initial version: autonomous GCM AI agent engine with tool calling and multi-step execution
# Fri Nov 20 02:30:00 CET 2026 - Fix invalid (5, 30, 5, 30) timeout tuple; add agent/provider/tool logging
# Fri Nov 20 02:40:00 CET 2026 - Drop unsupported "name" key from tool messages (1min.ai 400 error)
# Fri Nov 20 02:15:35 CET 2026 - Teach agent the GCM dynamic tool discovery workflow (search/get_schema/execute); raise step limit
# Fri Nov 20 02:18:05 CET 2026 - Inject real tool catalog into prompt; require get_schema before execute runs a tool
# Fri Nov 20 02:46:01 CET 2026 - Guide agent to fetch complete multi-page datasets for all assets/items when requested
# Fri Nov 20 02:59:39 CET 2026 - Format AI provider errors with explicit AI provider label
# Fri Nov 20 03:25:20 CET 2026 - Prompt: look up objects by name via search_by + subject column (filtering on crypto_object_name returns HTTP 500)
# Fri Nov 20 03:31:04 CET 2026 - Correct name lookup (use `name` column, not crypto_object_name/subject); explicit DISCOVERY/DIRECT mode note instead of ambiguous conditional
# Fri Nov 20 03:55:54 CET 2026 - run_agent_turn accepts dynamic_tools flag (off by default) for the MCP client
# Fri Nov 20 04:05:00 CET 2026 - Auto-correct known-bad columns (crypto_object_name -> name, drop key_usage, certificate expiry -> not_after) before calling tools
# Fri Nov 20 04:20:00 CET 2026 - Prompt: paging rules for search_policies (0-based page, size max 100, totalItems/totalPages)
# Fri Nov 20 04:35:00 CET 2026 - Fix HTTP 400: asset list page_number is 1-based; prompt clarified and page_number < 1 is corrected to 1
# Fri Nov 20 04:50:00 CET 2026 - Date filters (not_after etc.) cause HTTP 500: stripped from the request and applied locally to the result
# Fri Nov 20 05:05:00 CET 2026 - Prompt: always write date restrictions in filter; backstop parses "expiring before <date>" from the user request
"""Autonomous AI Agent engine for Guardium Cryptography Manager."""
from __future__ import annotations

import json
import re
from datetime import datetime
import time
from typing import Any, Callable

import requests

from utils.ai_providers import IBM_IAM_URL
from utils.logging_setup import get_logger
from utils.mcp_client import MCPClient, MCPError
from utils.net import TIMEOUT, describe_request_error, http_status_message

logger = get_logger()

MAX_AGENT_STEPS = 10

SYSTEM_PROMPT = """You are the Guardium Cryptography Manager (GCM) autonomous AI Agent assistant.
Your mission is to help users retrieve, analyze, and work with data from Guardium Cryptography Manager.

Follow these strict operational principles:
1. Natural Language Intent Analysis: Treat all user queries about keys, certificates, cryptographic assets, policies, compliance, or security operations as natural language requests. Autonomously determine what data is required without asking users to specify technical API parameters.
2. Autonomous Discovery & Tool Execution: Select and execute the appropriate GCM MCP tools via function calls. You may call multiple tools in sequence or combine datasets from different GCM resources when needed.
3. Transparent Attribution & Explanation: Always explain what data was retrieved, from which specific endpoint or tool, and why it is relevant to the user's request.
4. Actionable Presentation: Summarize key findings, highlight critical cryptographic status or issues, and structure information clearly.
5. Proactive Clarification: If a request is ambiguous or lacks necessary operational scope, ask targeted clarifying questions before executing speculative operations.

DYNAMIC TOOL DISCOVERY (critical):
In discovery mode the GCM MCP server does NOT expose its real tools directly and your function list only contains a few meta-tools: `search`, `get_schema`, `tags`, `list_tools` and `execute`. The real GCM tools (about 60, e.g. for crypto objects such as keys and certificates, IT assets, policies, vulnerabilities, filters, metadata) are hidden behind them. Never conclude that a capability is missing just because it is not in your function list, and never ask the user which tool to use. Always follow this workflow:
1. FIND: call `search` with a short natural-language query (e.g. "list keys", "certificates expiring", "policies") and `limit` of 10. Use `tags` or `list_tools` only to browse. Real tool names appear in the results (they are NOT callable as functions directly).
2. INSPECT: call `get_schema` with `tools` set to the chosen tool name(s) to get the exact parameter schema. Do this before the first call of any tool; never guess parameter names. Prefer tools starting with `get_`, `fetch_`, `list_` or `search_` for read requests. Only use create/update/delete tools if the user explicitly asks for a change.
3. RUN: call `execute` with a Python `code` string that awaits `call_tool(tool_name, params)` and `return`s the result. `params` must match the schema exactly (many tools wrap their arguments in a `params` object). Example:
   code = 'r = await call_tool("get_filters_by_crypto_objects", {"filter_category": ["key_algorithm"]})\\nreturn r'
   Several calls can be chained in one block and the results combined into a single returned dict/list.
4. If a tool returns an error or an unexpected shape, read the error, re-check the schema with `get_schema`, and retry with corrected parameters. If a listing tool needs identifiers (IDs), first use a listing/search/bulk tool (e.g. a `*_list_*`, `fetch_detailed_*_list_*` or `fetch_bulk_*` tool) to obtain them, then call the detail tool with those IDs. Resolve IDs yourself instead of asking the user for them.
5. Complete Datasets & Pagination: When the user asks for "all" items or assets (e.g. all business assets, business applications, IT assets, cryptographic keys, certificates, policies, etc.) or when retrieving an inventory:
   - Always fetch the complete dataset so the user sees all returned data in the results table.
   - If the tool schema accepts `page_size`, `pageSize`, `limit`, or `count`, specify a large limit (e.g. 1000 or 5000) or one matching the `total_count`.
   - If the server caps page size (e.g. at 100) and `total_count` indicates more records (e.g. 407 records), write a small loop in Python inside `execute` to fetch and combine all pages into the returned dictionary or list under the appropriate collection key (e.g. `all_crypto_objects`, `business_applications`, `it_assets`, or records list).
   - Ensure the returned tool result contains all items (e.g. all 407 records) rather than only the first page.
   - Spring-style paged tools such as `search_policies` use `query_param` `page` (0-based, only for these tools) and `size` (default 10, max 100) and return `data`, `totalItems` and `totalPages` (e.g. 59 policies: size 10 gives totalPages 6, size 100 gives totalPages 1). `totalPages` is correct for the `size` you asked for. Always set `size` to 100; if `totalItems` is greater than the rows received, loop `page` from 0 up to `totalPages - 1` inside `execute` (or call repeatedly) and combine all `data` rows. Never report only the first default page of 10.
6. Choose the right scope & tool:
   - For business assets / applications / IT assets: search for and execute business asset tools (e.g. `get_business_applications`, `fetch_it_assets`, etc.).
   - For cryptographic objects (keys, certificates, protocols): use `fetch_detailed_asset_list_by_crypto_objects`.
   - Tools with `vulnerable` in the name return ONLY objects with violations - use them only when the user asks about vulnerabilities, violations or risk, never for a general listing.
7. Never assume the shape of a tool result. Do not filter or index results with guessed field names (e.g. `.get("data")`): that silently yields empty lists. `return` the raw result and read the real field names from it. The inventory list returns `total_count` and rows under `all_crypto_objects`; report `total_count` and return all rows.
8. Known quirks of `fetch_detailed_asset_list_by_crypto_objects`: use `asset_category` "crypto_objects"; `page_number`, `page_size`, `columns`, `filter` and `search_by` all go INSIDE `body` (never beside it). `page_number` is 1-BASED here: page_number 0 returns HTTP 400 (only `search_policies` `page` is 0-based). Set `filter` in `body` to restrict the type, using RSQL: "(crypto_object_type=in=('Key'))" (or 'Certificate' / 'Protocol'; several values allowed, e.g. =in=('Key','Certificate')); always include `crypto_id` and `crypto_object_type` in `columns`. The object name column is `name` (it is returned in the result as `crypto_object_name`). DATE RESTRICTIONS (e.g. "certificates expiring before 31-12-2026"): ALWAYS include them as a predicate in `filter`, e.g. "(crypto_object_type=in=('Certificate')) and (not_after=lt=2026-12-31)" (ISO yyyy-mm-dd; =lt= =le= =gt= =ge=; `not_after` for certificate expiry). Never drop the date because you expect a server error: the server itself rejects date predicates (HTTP 500), so in direct function calls the application removes the predicate before sending and applies it to the returned rows for you. Inside `execute` code instead leave the date predicate out of `filter` and filter the returned rows in Python (ISO strings compare chronologically). Never ask the user for permission to filter locally and never retry because of a date filter. Do NOT put `crypto_object_name` or `key_usage` in `columns` or `filter`: they cause HTTP 500 and retrying with fewer columns does not help; use `name` instead. For certificates use not_before, not_after (NOT `expiry`), certificate_status, issuer, subject, key_algorithm, key_length. Columns known to work for keys: key_state, key_type, key_algorithm, key_length, key_owner, key_protection, key_version, expiry, is_compromised, is_exportable, is_revoked, rotation_interval, origin_source, pqc_readiness_flag, created_at, updated_at. On any other HTTP 500, retry with fewer columns instead of giving up. For a keys-only request you MUST set that `filter` for 'Key' (otherwise certificates and protocols are mixed in); do not use `search_by` for type selection, it is free-text.
   Looking up an object BY NAME (e.g. "details of cert-billingdb-prod-14-40"): put the name in `search_by` in `body`, optionally `"search_in": ["name"]`, and ALWAYS include the `name` column in `columns` (search_by only matches requested columns, so without `name` an existing object returns `{}`). An exact filter `"(name=='<object name>')"` also works. For the full detail request columns such as: name, crypto_object_type, discovery_sources, associated_it_assets, total_violation, exploitability_score, is_exception, non_normalized_attributes, attributes_value_review, plus certificate_status, not_before, not_after, issuer, subject, key_algorithm, key_length as relevant; or use `fetch_individual_asset_detail_by_crypto_objects` with the `crypto_id`. If the result is `{}`, say no object matches instead of retrying the same query.
9. Presentation: the application automatically shows the raw rows returned by `execute` to the user as an interactive table with multipage controls and full scrolling. So `return` the tool result UNWRAPPED (just `return r`, no custom wrapper dict) and do not repeat the rows in your answer; instead summarize with totals and breakdowns (e.g. counts per state, algorithm, expired or compromised items) and highlight issues.
"""


# Fri Nov 20 02:02:53 CET 2026 - Convert MCP tool definitions to OpenAI-compatible function calling schema
def tools_to_openai_schema(tools: list[dict], slim: bool = False) -> list[dict]:
    """Convert MCP tool definitions to OpenAI function tool definitions.

    When slim=True, strip property-level descriptions and examples from the
    parameter schema to reduce payload size for models with strict limits.
    """
    def _slim_schema(schema: dict) -> dict:
        """Return a copy of schema with per-property descriptions removed."""
        props = schema.get("properties", {})
        slim_props = {
            k: {pk: pv for pk, pv in prop.items() if pk not in ("description", "examples", "default")}
            for k, prop in props.items()
        }
        result = {k: v for k, v in schema.items() if k != "properties"}
        if slim_props:
            result["properties"] = slim_props
        return result

    openai_tools: list[dict] = []
    for tool in tools:
        name = tool.get("name")
        if not name:
            continue
        description = tool.get("description", "")
        schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        if slim:
            schema = _slim_schema(schema)
        openai_tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description[:256] if slim else (description[:1024] if description else f"GCM tool {name}"),
                "parameters": schema,
            },
        })
    return openai_tools


# Fri Nov 20 02:02:53 CET 2026 - Exchange IBM Cloud API key for IAM access token
def get_ibm_iam_token(api_key: str) -> str:
    """Exchange IBM Cloud API key for an IAM Bearer token."""
    resp = requests.post(
        IBM_IAM_URL,
        data={"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": api_key},
        headers={"Accept": "application/json"},
        timeout=TIMEOUT,
        verify=True,
    )
    if resp.status_code != 200:
        raise ValueError(f"IBM IAM authentication failed: {http_status_message(resp.status_code)}")
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise ValueError("IBM IAM did not return an access token.")
    return str(token)


# Fri Nov 20 02:02:53 CET 2026 - Execute a Chat Completion request across supported AI providers
def chat_completion(
    provider: str,
    settings: dict,
    messages: list[dict],
    tools: list[dict] | None = None,
) -> dict:
    """Send a chat completion request to the configured AI provider.

    Returns the assistant message dictionary (with 'role', 'content', and optional 'tool_calls').
    """
    model = settings.get("model", "")
    api_key = settings.get("api_key", "")
    base_url = settings.get("base_url", "").rstrip("/")

    if not api_key:
        raise ValueError(f"No API key configured for {provider}. Configure credentials in the Configuration page.")

    headers: dict[str, str] = {
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }

    if tools:
        payload["tools"] = tools

    if provider == "IBM":
        iam_token = get_ibm_iam_token(api_key)
        headers["Authorization"] = f"Bearer {iam_token}"
        # watsonx.ai uses model_id, not model; tool_choice_option instead of tool_choice; max_tokens instead of temperature
        payload["model_id"] = payload.pop("model")
        del payload["temperature"]
        payload["max_tokens"] = 4096
        if tools:
            payload["tool_choice_option"] = "auto"
        project_id = settings.get("project_id", "")
        if project_id:
            payload["project_id"] = project_id
        url = f"{base_url}/ml/v1/text/chat?version=2024-10-08"
    else:
        headers["Authorization"] = f"Bearer {api_key}"
        if tools:
            payload["tool_choice"] = "auto"
        url = f"{base_url}/chat/completions"

    logger.info("AI request", extra={"provider": provider, "model": model, "messages": len(messages)})
    resp = requests.post(url, headers=headers, json=payload, timeout=(TIMEOUT[0], TIMEOUT[1] * 2), verify=True)
    logger.info("AI response", extra={"provider": provider, "status": resp.status_code})
    if resp.status_code != 200:
        try:
            err_body = resp.json()
            err_msg = err_body.get("error", {}).get("message") or resp.text
        except Exception:
            err_msg = resp.text
        raise ValueError(f"{provider} AI provider error ({resp.status_code}): {err_msg[:300]}")

    body = resp.json()
    choices = body.get("choices")
    if not choices or not isinstance(choices, list):
        raise ValueError(f"Invalid response structure from {provider}.")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("No message in completion choice.")
    return message


# Fri Nov 20 02:18:05 CET 2026 - Names of real GCM tools hidden behind the discovery meta-tools (empty if not applicable)
def fetch_tool_catalog(mcp_client: MCPClient, mcp_tools: list[dict]) -> list[str]:
    """Return real tool names via the `list_tools` meta-tool; [] when discovery mode is not in use."""
    if not any(t.get("name") == "list_tools" for t in mcp_tools or []):
        return []
    try:
        payload, is_error = mcp_client.call_tool("list_tools", {})
    except MCPError:
        return []
    text = payload.get("result") if isinstance(payload, dict) else payload
    if is_error or not isinstance(text, str):
        return []
    return list(dict.fromkeys(re.findall(r"^### (\S+)", text, re.M)))


# Fri Nov 20 02:18:05 CET 2026 - Tool names referenced by call_tool(...) in execute code that were not yet inspected
def _unseen_tools(code: str, inspected: set[str]) -> list[str]:
    names = re.findall(r"call_tool\(\s*[\"']([\w.\-]+)[\"']", code)
    return [n for n in dict.fromkeys(names) if n not in inspected]

# Fri Nov 20 04:05:00 CET 2026 - Columns that make fetch_detailed_asset_list_by_crypto_objects fail with HTTP 500
_COLUMN_FIXES = {"crypto_object_name": "name", "subject_name": "name"}
_DROP_COLUMNS = {"key_usage"}


# Fri Nov 20 04:05:00 CET 2026 - Rewrite known-bad column names in a `columns` list (name for crypto_object_name, drop key_usage)
def _fix_columns(columns: list, filter_text: str = "") -> list:
    certs_only = "Certificate" in filter_text and "'Key'" not in filter_text
    fixed: list = []
    for col in columns:
        if isinstance(col, str):
            if col in _DROP_COLUMNS:
                continue
            col = _COLUMN_FIXES.get(col, col)
            if certs_only and col == "expiry":
                col = "not_after"  # `expiry` is a key column; certificates use not_after
        if col not in fixed:
            fixed.append(col)
    return fixed


# Fri Nov 20 04:05:00 CET 2026 - Recursively sanitize `columns` lists in tool arguments (in place)
def sanitize_tool_args(value: Any) -> Any:
    if isinstance(value, dict):
        if isinstance(value.get("page_number"), int) and value["page_number"] < 1:
            value["page_number"] = 1  # asset list endpoints are 1-based; 0 gives HTTP 400
        if isinstance(value.get("columns"), list):
            value["columns"] = _fix_columns(value["columns"], str(value.get("filter", "")))
        for item in value.values():
            sanitize_tool_args(item)
    elif isinstance(value, list):
        for item in value:
            sanitize_tool_args(item)
    return value


# Fri Nov 20 04:05:00 CET 2026 - Same fix for `"columns": [...]` literals inside Python code sent to `execute`
def sanitize_execute_code(code: str) -> str:
    def repl(match: re.Match) -> str:
        items = re.findall(r"""(["'])(.*?)\1""", match.group(2))
        quote = items[0][0] if items else '"'
        cols = _fix_columns([name for _, name in items])
        return f"{match.group(1)}{', '.join(f'{quote}{c}{quote}' for c in cols)}]"

    return re.sub(r"""(["']columns["']\s*:\s*\[)([^\]\[]*)\]""", repl, code)


# Fri Nov 20 04:50:00 CET 2026 - Date comparisons in `filter` make the GCM asset list fail with HTTP 500; they are applied locally instead
_DATE_PRED_RE = re.compile(
    r"""(?P<f>not_after|not_before|created_at|updated_at|expiry)\s*(?P<op>=lt=|=le=|=gt=|=ge=|<=|>=|<|>)\s*"""
    r"""['"]?(?P<v>\d{4}-\d{2}-\d{2}[0-9T:.Z+\-]*)['"]?""",
    re.I,
)
_DATE_OPS = {
    "=lt=": lambda a, b: a < b, "<": lambda a, b: a < b,
    "=le=": lambda a, b: a <= b, "<=": lambda a, b: a <= b,
    "=gt=": lambda a, b: a > b, ">": lambda a, b: a > b,
    "=ge=": lambda a, b: a >= b, ">=": lambda a, b: a >= b,
}


# Fri Nov 20 04:50:00 CET 2026 - Remove date predicates from a filter string; returns (clean_filter, [(field, op, value)])
def _split_date_filter(flt: str) -> tuple[str, list[tuple[str, str, str]]]:
    conds = [(m["f"].lower(), m["op"].lower(), m["v"]) for m in _DATE_PRED_RE.finditer(flt)]
    if not conds:
        return flt, []
    flt = _DATE_PRED_RE.sub("", flt)
    flt = re.sub(r"\(\s*\)", "", flt)
    connector = r"(?:\band\b|;|&&)"
    for _ in range(3):
        flt = re.sub(rf"^\s*{connector}\s*|\s*{connector}\s*$", "", flt, flags=re.I)
        flt = re.sub(rf"\(\s*{connector}\s*", "(", flt, flags=re.I)
        flt = re.sub(rf"\s*{connector}\s*\)", ")", flt, flags=re.I)
        flt = re.sub(rf"{connector}\s*{connector}", "and", flt, flags=re.I)
        flt = re.sub(r"\(\s*\)", "", flt)
    return flt.strip(), conds


# Fri Nov 20 04:50:00 CET 2026 - Strip date predicates from tool arguments (in place), keep fields in columns; returns conditions
def strip_date_filters(value: Any) -> list[tuple[str, str, str]]:
    conds: list[tuple[str, str, str]] = []
    if isinstance(value, dict):
        if isinstance(value.get("filter"), str):
            clean, found = _split_date_filter(value["filter"])
            if found:
                conds += found
                if clean:
                    value["filter"] = clean
                else:
                    value.pop("filter")
                if isinstance(value.get("columns"), list):
                    for field, _, _ in found:
                        if field not in value["columns"]:
                            value["columns"].append(field)
                if isinstance(value.get("page_size"), int):
                    value["page_size"] = max(value["page_size"], 1000)  # fetch everything, filter locally
        for item in value.values():
            conds += strip_date_filters(item)
    elif isinstance(value, list):
        for item in value:
            conds += strip_date_filters(item)
    return conds


# Fri Nov 20 04:50:00 CET 2026 - Keep only rows matching the date conditions (ISO strings compare chronologically)
def apply_date_filters(payload: Any, conds: list[tuple[str, str, str]]) -> Any:
    if not conds or not isinstance(payload, dict):
        return payload

    def keep(row: dict) -> bool:
        for field, op, wanted in conds:
            actual = str(row.get(field) or "")[: len(wanted)]
            if not actual or not _DATE_OPS[op](actual, wanted):
                return False
        return True

    for key, rows in list(payload.items()):
        if isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows):
            before = len(rows)
            payload[key] = [r for r in rows if keep(r)]
            if isinstance(payload.get("total_count"), int) and payload["total_count"] <= before:
                payload["total_count"] = len(payload[key])
            payload["date_filter_applied_locally"] = ", ".join(f"{f} {o} {v}" for f, o, v in conds)
    return payload


# Fri Nov 20 05:05:00 CET 2026 - Date restriction ("expiring before 31-12-2026") parsed from the user's request, used if the model omits it
_USER_DATE_RE = re.compile(
    r"(?P<kw>expir\w*|valid\s+until|not\s+after)[^.?!\n]*?\b(?P<rel>before|prior\s+to|until|by|after)\s+"
    r"(?P<d>\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]+\s+\d{4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4})",
    re.I,
)
_REL_OPS = {"before": "=lt=", "prior to": "=lt=", "until": "=le=", "by": "=le=", "after": "=gt="}


def user_date_conditions(text: str) -> list[tuple[str, str, str]]:
    m = _USER_DATE_RE.search(text or "")
    if not m:
        return []
    raw = re.sub(r"\s+", " ", m["d"].strip())
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y"):
        try:
            day = datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
        return [("not_after", _REL_OPS[re.sub(r"\s+", " ", m["rel"].lower())], day)]
    return []


# Fri Nov 20 05:05:00 CET 2026 - Add the user's date restriction to a certificate-only asset list filter when the model left it out
def inject_user_date_filter(tool_name: str, tool_args: dict, conds: list[tuple[str, str, str]]) -> None:
    body = (tool_args.get("params") or {}).get("body") if isinstance(tool_args.get("params"), dict) else None
    if not conds or tool_name != "fetch_detailed_asset_list_by_crypto_objects" or not isinstance(body, dict):
        return
    flt = str(body.get("filter") or "")
    if "Certificate" not in flt or "'Key'" in flt or _DATE_PRED_RE.search(flt):
        return
    field, op, day = conds[0]
    body["filter"] = f"{flt} and ({field}{op}{day})"


# Fri Nov 20 02:02:53 CET 2026 - Run autonomous multi-step agent turn with GCM tool invocation
def run_agent_turn(
    endpoint: str,
    token: str,
    verify: bool,
    provider: str,
    ai_settings: dict,
    mcp_tools: list[dict],
    conversation_history: list[dict],
    on_step: Callable[[str, dict | None], None] | None = None,
    dynamic_tools: bool = False,
) -> tuple[str, list[dict], list[dict]]:
    """Execute an autonomous agent turn.

    Args:
        endpoint: GCM MCP server URL.
        token: GCM Bearer token.
        verify: SSL verification flag.
        provider: AI provider name.
        ai_settings: AI provider settings dict.
        mcp_tools: List of discovered MCP tools.
        conversation_history: Prior chat messages [{'role': 'user'|'assistant', 'content': ...}].
        on_step: Optional callback(step_description, details_dict) for live UI updates.
        dynamic_tools: Send the dynamic tool discovery header to the MCP server.

    Returns:
        (final_response_text, executed_tools_data, updated_conversation_history)
    """
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in conversation_history:
        messages.append({
            "role": msg["role"],
            "content": msg.get("content", ""),
        })

    openai_tools = tools_to_openai_schema(mcp_tools, slim=(provider == "IBM")) if mcp_tools else None
    last_user = next((m.get("content", "") for m in reversed(conversation_history) if m.get("role") == "user"), "")
    user_conds = user_date_conditions(last_user)
    executed_tools: list[dict] = []

    logger.info("Agent turn started", extra={"provider": provider, "tools_available": len(mcp_tools or [])})
    if on_step:
        on_step("Analyzing natural language request...", {"provider": provider, "model": ai_settings.get("model")})

    with MCPClient(endpoint, token, verify, dynamic_tools) as mcp_client:
        catalog = fetch_tool_catalog(mcp_client, mcp_tools)
        if not any(t.get("name") == "execute" for t in mcp_tools or []):
            messages[0]["content"] += (
                "\nMODE: DIRECT. The real GCM tools are in your function list: call them directly as functions "
                "with arguments matching their schema. There is no `search`, `get_schema` or `execute`: ignore those "
                "steps and call_tool(...) examples; make several function calls instead of Python loops.\n"
            )
        if catalog:
            messages[0]["content"] += (
                "\nREAL GCM TOOLS (call only through `execute`; inspect each with `get_schema` first). "
                "Note: `gcm_*ApiKey*` tools manage GCM API keys, NOT cryptographic keys:\n" + ", ".join(catalog) + "\n"
            )
        inspected: set[str] = set()
        for step in range(MAX_AGENT_STEPS):
            assistant_msg = chat_completion(provider, ai_settings, messages, openai_tools)
            tool_calls = assistant_msg.get("tool_calls")

            if not tool_calls:
                final_text = assistant_msg.get("content", "") or ""
                return final_text, executed_tools, messages

            # Append assistant message with tool calls to conversation history
            # Keep only standard keys; strict providers (1min.ai) reject unknown ones
            messages.append({
                "role": "assistant",
                "content": assistant_msg.get("content") or "",
                "tool_calls": tool_calls,
            })

            for call in tool_calls:
                fn = call.get("function", {})
                tool_name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                try:
                    tool_args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except Exception:
                    tool_args = {}
                if tool_name == "execute" and isinstance(tool_args.get("code"), str):
                    tool_args["code"] = sanitize_execute_code(tool_args["code"])
                    date_conds: list = []
                else:
                    sanitize_tool_args(tool_args)
                    inject_user_date_filter(tool_name, tool_args, user_conds)
                    date_conds = strip_date_filters(tool_args)

                if on_step:
                    on_step(f"Calling GCM tool: `{tool_name}`", {"tool": tool_name, "args": tool_args})

                start_t = time.monotonic()
                if tool_name == "get_schema":
                    requested = tool_args.get("tools")
                    inspected.update(requested if isinstance(requested, list) else [])
                elif tool_name == "execute":
                    code = tool_args.get("code")
                    unseen = _unseen_tools(code if isinstance(code, str) else "", inspected)
                    schema_payload = None
                    if unseen:
                        # Force schema inspection before any real GCM tool runs (models tend to guess parameters)
                        inspected.update(unseen)
                        try:
                            schema_payload, _ = mcp_client.call_tool("get_schema", {"tools": unseen})
                        except MCPError:
                            logger.warning("Schema pre-fetch failed", extra={"tools": unseen})
                    if schema_payload is not None:
                        executed_tools.append({
                            "tool": "get_schema", "args": {"tools": unseen}, "payload": schema_payload,
                            "is_error": False, "seconds": time.monotonic() - start_t,
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.get("id", f"call_{tool_name}"),
                            "content": (
                                "NOT EXECUTED: you have not inspected these tools yet. Review their exact schemas below, "
                                "then call `execute` again with correct parameters.\n"
                                + json.dumps(schema_payload, default=str)
                            )[:20_000],
                        })
                        continue
                try:
                    payload, is_error = mcp_client.call_tool(tool_name, tool_args)
                    if date_conds and not is_error:
                        payload = apply_date_filters(payload, date_conds)
                    duration = time.monotonic() - start_t
                    logger.info("MCP tool call", extra={"tool": tool_name, "is_error": is_error, "seconds": round(duration, 2)})
                    tool_result_entry = {
                        "tool": tool_name,
                        "args": tool_args,
                        "payload": payload,
                        "is_error": is_error,
                        "seconds": duration,
                    }
                    content_str = json.dumps(payload, default=str)
                except MCPError as exc:
                    duration = time.monotonic() - start_t
                    logger.warning("MCP tool call failed", extra={"tool": tool_name, "error": exc.message})
                    tool_result_entry = {
                        "tool": tool_name,
                        "args": tool_args,
                        "error": exc.message,
                        "detail": exc.detail,
                        "seconds": duration,
                    }
                    content_str = json.dumps({"error": exc.message, "detail": exc.detail})

                executed_tools.append(tool_result_entry)

                # Feed tool output back to the assistant
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", f"call_{tool_name}"),
                    "content": content_str[:20_000],  # protect context limit
                })

    # If maximum steps reached without pure text response, make final summary call without tools
    final_summary = chat_completion(provider, ai_settings, messages, tools=None)
    return final_summary.get("content", "") or "", executed_tools, messages
