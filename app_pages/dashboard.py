# Change log:
# Fri Nov 20 01:39:30 CET 2026 - Initial version: dashboard showing data returned by GCM MCP tools
# Fri Nov 20 02:46:01 CET 2026 - Use multipage scrollable paginated table for dashboard data results
import hashlib
import json
import time
from datetime import datetime

import streamlit as st

from utils.logging_setup import debug_enabled
from utils.mcp_client import MCPClient, MCPError, is_read_only, required_args, test_connection
from utils.render import (
    categorical_columns, count_by, csv_safe, label_value_frame, numeric_columns, parse_payload,
    render_paginated_table,
)
from utils.validation import build_mcp_endpoint, validate_mcp

MAX_ARGS_CHARS = 10_000
MAX_METRICS = 8
DEFAULT_SELECTED_TOOLS = 3

cfg = st.session_state.config["mcp"]
_, cfg_errors, _ = validate_mcp(cfg["server_url"], cfg["bearer_token"], cfg["verify_ssl"])
if cfg_errors:
    st.info(
        "Configure and save the Guardium Cryptography Manager MCP server first.",
        title="MCP server not configured",
        icon=":material/settings:",
    )
    st.page_link("app_pages/configuration.py", label="Open configuration", icon=":material/settings:")
    st.stop()

endpoint = build_mcp_endpoint(cfg["server_url"])
token, verify = cfg["bearer_token"], cfg["verify_ssl"]
fingerprint = hashlib.sha256(f"{endpoint}|{token}|{verify}".encode()).hexdigest()

# --- Header controls -------------------------------------------------------
head_left, head_right = st.columns([4, 1], vertical_alignment="center")
head_left.caption(f"Server: `{endpoint}`" + ("" if verify else " - SSL verification off"))
refresh = head_right.button("Refresh tools", icon=":material/refresh:", width="stretch", key="dash_refresh")

# --- Tool discovery (cached per configuration in the session) ---------------
if refresh or st.session_state.get("dash_fp") != fingerprint:
    for key in ("dash_tools", "dash_error", "dash_results", "dash_server"):
        st.session_state.pop(key, None)
    st.session_state.dash_fp = fingerprint
    with st.spinner("Discovering tools on the MCP server..."):
        discovery = test_connection(endpoint, token, verify)
    if discovery.ok:
        found = discovery.tools or []
        st.session_state.dash_tools = found
        st.session_state.dash_server = f"{discovery.server_name} {discovery.server_version}".strip()
        defaults = [t["name"] for t in found if is_read_only(t) and not required_args(t)]
        st.session_state.dash_selected = defaults[:DEFAULT_SELECTED_TOOLS]
        st.session_state.dash_autofetch = bool(st.session_state.dash_selected)
    else:
        st.session_state.dash_error = (discovery.message, discovery.detail)

if "dash_error" in st.session_state:
    message, detail = st.session_state.dash_error
    st.error(message, title="Cannot reach the MCP server", icon=":material/error:")
    if debug_enabled() and detail:
        st.code(detail)
    st.page_link("app_pages/configuration.py", label="Check configuration", icon=":material/settings:")
    st.stop()

tools: list[dict] = st.session_state.dash_tools
tools_by_name = {t["name"]: t for t in tools}

# --- Tool selection ----------------------------------------------------------
with st.container(border=True):
    st.subheader("Data sources")
    only_read = st.toggle(
        "Show read-only tools only",
        value=True,
        key="dash_only_read",
        persist_state="session",
        help="Hides tools that may change data on the GCM server (based on tool annotations and names).",
    )
    options = [t["name"] for t in tools if is_read_only(t) or not only_read]
    selected = st.multiselect(
        "Tools to query",
        options,
        key="dash_selected",
        persist_state="session",
        placeholder="Select one or more tools",
        help="Each selected tool is called on the MCP server and its result is shown below.",
    )
    selected = [s for s in selected if s in tools_by_name]  # re-validate on the server side
    args_by_tool: dict[str, str] = {}
    arg_tools = [n for n in selected if (tools_by_name[n].get("inputSchema") or {}).get("properties")]
    if arg_tools:
        with st.expander("Tool arguments (JSON)", icon=":material/tune:"):
            for name in arg_tools:
                template = json.dumps({k: "" for k in required_args(tools_by_name[name])})
                args_by_tool[name] = st.text_area(
                    name,
                    value=template,
                    key=f"dash_args_{name}",
                    height=80,
                    help=tools_by_name[name].get("description", "")[:300] or None,
                )
    fetch = st.button("Fetch data", type="primary", icon=":material/download:", key="dash_fetch", disabled=not selected)

# --- Fetch -------------------------------------------------------------------
if (fetch or st.session_state.pop("dash_autofetch", False)) and selected:
    results: dict[str, dict] = {}
    parsed_args: dict[str, dict] = {}
    for name in selected:
        raw = args_by_tool.get(name, "{}").strip() or "{}"
        try:
            args = json.loads(raw) if len(raw) <= MAX_ARGS_CHARS else None
        except ValueError:
            args = None
        missing = [r for r in required_args(tools_by_name[name]) if not isinstance(args, dict) or r not in args]
        if not isinstance(args, dict) or missing:
            results[name] = {"error": "Invalid arguments: provide a JSON object with all required fields."}
        else:
            parsed_args[name] = args
    try:
        with st.spinner("Querying the MCP server..."), MCPClient(endpoint, token, verify) as client:
            for name, args in parsed_args.items():
                started = time.monotonic()
                try:
                    payload, is_error = client.call_tool(name, args)
                    results[name] = {"payload": payload, "is_error": is_error}
                except MCPError as exc:
                    results[name] = {"error": exc.message, "detail": exc.detail}
                results[name]["seconds"] = time.monotonic() - started
    except MCPError as exc:
        for name in parsed_args:
            results[name] = {"error": exc.message, "detail": exc.detail}
    st.session_state.dash_results = results
    st.session_state.dash_fetched_at = datetime.now().strftime("%H:%M:%S")

results = st.session_state.get("dash_results", {})
shown = {n: results[n] for n in selected if n in results}

# --- KPIs --------------------------------------------------------------------
parsed_by_tool = {
    n: parse_payload(r["payload"]) for n, r in shown.items() if "payload" in r and not r.get("is_error")
}
total_rows = sum(len(df) for p in parsed_by_tool.values() for df in p.tables.values())
failed = sum(1 for r in shown.values() if "error" in r or r.get("is_error"))
with st.container(horizontal=True):
    st.metric("Tools discovered", len(tools), border=True)
    st.metric("Tools queried", len(shown), border=True)
    st.metric("Records returned", f"{total_rows:,}", border=True)
    st.metric("Failed calls", failed, border=True)
    st.metric("Last fetch", st.session_state.get("dash_fetched_at", "-"), border=True)

if not shown:
    st.info("Select tools and choose Fetch data to display results.", icon=":material/info:")

# --- Results -----------------------------------------------------------------
for name, result in shown.items():
    with st.container(border=True):
        st.subheader(name)
        description = tools_by_name[name].get("description", "")
        if description:
            st.caption(description[:300])
        if "error" in result:
            st.error(result["error"], icon=":material/error:")
            if debug_enabled() and result.get("detail"):
                st.code(result["detail"])
            continue
        if result.get("is_error"):
            st.error("The tool reported an error.", icon=":material/error:")
            st.code(json.dumps(result["payload"], indent=2, default=str)[:2000])
            continue

        parsed = parsed_by_tool[name]
        if parsed.metrics:
            items = list(parsed.metrics.items())
            with st.container(horizontal=True):
                for key, value in items[:MAX_METRICS]:
                    is_num = isinstance(value, int | float) and not isinstance(value, bool)
                    st.metric(str(key), value if is_num else str(value), border=True)
            if len(items) > MAX_METRICS:
                with st.expander(f"All fields ({len(items)})"):
                    st.dataframe(
                        [{"field": k, "value": str(v)} for k, v in items],
                        hide_index=True,
                        alt=f"All fields returned by {name}",
                    )
        if parsed.text:
            st.text(parsed.text[:5000])

        for table_name, df in parsed.tables.items():
            key = f"{name}_{table_name}"
            st.markdown(f"**{table_name}** - {len(df):,} rows")
            view = st.segmented_control(
                "View", ["Table", "Chart", "Raw JSON"], default="Table", key=f"dash_view_{key}",
                label_visibility="collapsed",
            )
            if view == "Chart":
                cats, nums = categorical_columns(df), numeric_columns(df)
                kinds = (["Count by category"] if cats else []) + (["Values by label"] if nums else [])
                if not kinds:
                    st.info("No suitable columns for a chart in this table.")
                else:
                    kind = st.segmented_control("Chart type", kinds, default=kinds[0], key=f"dash_kind_{key}")
                    if kind == "Count by category":
                        col = st.selectbox("Category column", cats, key=f"dash_cat_{key}")
                        st.bar_chart(count_by(df, col), x=col, y="count", alt=f"Count of {name} records by {col}")
                    elif kind == "Values by label":
                        left, right = st.columns(2)
                        value_col = right.selectbox("Value column", nums, key=f"dash_val_{key}")
                        label_cols = [c for c in df.columns if c != value_col]
                        label_col = left.selectbox("Label column", label_cols, key=f"dash_lbl_{key}")
                        st.bar_chart(
                            label_value_frame(df, label_col, value_col), x=label_col, y=value_col,
                            alt=f"{value_col} by {label_col} for {name} (first 25 rows)",
                        )
            elif view == "Raw JSON":
                st.json(json.loads(df.head(2000).to_json(orient="records", default_handler=str)), expanded=1)
            else:
                render_paginated_table(df, f"dash_{key}", name, table_name)
            st.download_button(
                "Download CSV", csv_safe(df).to_csv(index=False), file_name=f"{name}_{table_name}.csv",
                mime="text/csv", icon=":material/download:", key=f"dash_dl_{key}",
            )

        if not (parsed.metrics or parsed.tables or parsed.text):
            st.info("The tool returned no data.")
        with st.expander("Raw response", icon=":material/data_object:"):
            st.json(result["payload"], expanded=1)
        st.caption(f"Completed in {result.get('seconds', 0):.1f}s")
