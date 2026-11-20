# Change log:
# Fri Nov 20 02:03:28 CET 2026 - Initial version: autonomous Guardium Cryptography Manager AI agent chat interface
# Fri Nov 20 02:31:11 CET 2026 - Hide "Data from <tool>" headings and discovery meta-tool panels; show only results
# Fri Nov 20 02:46:01 CET 2026 - Use multipage scrollable paginated table for data results
"""Guardium Cryptography Manager Autonomous AI Agent page."""
from __future__ import annotations

import hashlib
import json

import streamlit as st

from utils.agent_engine import run_agent_turn
from utils.logging_setup import debug_enabled, get_logger
from utils.mcp_client import test_connection
from utils.render import (
    categorical_columns, count_by, csv_safe, label_value_frame, numeric_columns, parse_payload,
    render_paginated_table,
)
from utils.validation import build_mcp_endpoint, validate_mcp

MAX_METRICS = 6
# Discovery meta-tools return tool catalogs/schemas, not user data: never render them as result tables
DISCOVERY_META_TOOLS = {"search", "get_schema", "tags", "list_tools"}
logger = get_logger()

# --- Check GCM configuration -----------------------------------------------
mcp_cfg = st.session_state.config.get("mcp", {})
_, mcp_errors, _ = validate_mcp(mcp_cfg.get("server_url", ""), mcp_cfg.get("bearer_token", ""), mcp_cfg.get("verify_ssl", True))
if mcp_errors:
    st.info(
        "Configure and save the Guardium Cryptography Manager MCP server to start using the Agent.",
        title="GCM MCP server not configured",
        icon=":material/settings:",
    )
    st.page_link("app_pages/configuration.py", label="Open configuration", icon=":material/settings:")
    st.stop()

# --- Check AI Provider configuration ---------------------------------------
ai_cfg = st.session_state.config.get("ai", {})
active_provider = ai_cfg.get("provider", "1min.ai")
provider_settings = ai_cfg.get("providers", {}).get(active_provider, {})
if not provider_settings.get("api_key"):
    st.warning(
        f"API key for {active_provider} is missing. Please configure credentials to enable autonomous agent execution.",
        title="AI provider not configured",
        icon=":material/smart_toy:",
    )
    st.page_link("app_pages/configuration.py", label="Open AI configuration", icon=":material/settings:")
    st.stop()

endpoint = build_mcp_endpoint(mcp_cfg["server_url"])
token, verify = mcp_cfg["bearer_token"], mcp_cfg["verify_ssl"]
dynamic_tools = bool(mcp_cfg.get("dynamic_tools", False))
fingerprint = hashlib.sha256(f"{endpoint}|{token}|{verify}|{dynamic_tools}".encode()).hexdigest()

# --- Tool discovery cache ---------------------------------------------------
if "agent_tools" not in st.session_state or st.session_state.get("agent_mcp_fp") != fingerprint:
    st.session_state.agent_mcp_fp = fingerprint
    with st.spinner("Discovering tools on Guardium Cryptography Manager..."):
        discovery = test_connection(endpoint, token, verify, dynamic_tools)
    if discovery.ok:
        st.session_state.agent_tools = discovery.tools or []
        st.session_state.agent_server_info = f"{discovery.server_name} {discovery.server_version}".strip()
    else:
        st.session_state.agent_tools = []
        st.error(discovery.message, title="Cannot connect to GCM MCP server", icon=":material/error:")
        if debug_enabled() and discovery.detail:
            st.code(discovery.detail)
        st.stop()

tools: list[dict] = st.session_state.agent_tools

# --- Header Bar -------------------------------------------------------------
header_left, header_right = st.columns([4, 1], vertical_alignment="center")
header_left.caption(
    f"Connected to GCM: `{endpoint}` | AI: **{active_provider}** (`{provider_settings.get('model')}`)"
)
if header_right.button("Clear chat", icon=":material/delete_sweep:", width="stretch", key="agent_clear_chat"):
    st.session_state.agent_chat_messages = []
    st.session_state.agent_turn_data = {}
    st.rerun()

# --- Chat History Initialization --------------------------------------------
if "agent_chat_messages" not in st.session_state:
    st.session_state.agent_chat_messages = []
if "agent_turn_data" not in st.session_state:
    st.session_state.agent_turn_data = {}

# --- Starter Suggestions (when chat is empty) --------------------------------
if not st.session_state.agent_chat_messages:
    st.subheader("How can I assist you with Guardium Cryptography Manager?")
    st.caption("Ask natural language questions about cryptographic data, key management, certificates, or security operations.")
    suggestions = [
        "List all cryptographic keys and their status",
        "Check expiring certificates and algorithms",
        "Summarize cryptographic inventory and assets",
        "Audit active security policies and compliance",
    ]
    cols = st.columns(len(suggestions))
    for idx, (col, suggestion) in enumerate(zip(cols, suggestions)):
        if col.button(suggestion, key=f"starter_chip_{idx}", width="stretch", icon=":material/search:"):
            st.session_state.agent_chat_messages.append({"role": "user", "content": suggestion})
            st.session_state.pending_user_query = suggestion
            st.rerun()

# --- Render Chat Messages ---------------------------------------------------
for idx, msg in enumerate(st.session_state.agent_chat_messages):
    role = msg["role"]
    content = msg.get("content", "")
    with st.chat_message(role, avatar=":material/person:" if role == "user" else ":material/smart_toy:"):
        if role == "assistant":
            turn_key = f"turn_{idx}"
            executed = st.session_state.agent_turn_data.get(turn_key, [])
            if executed:
                with st.expander(f"GCM tools executed ({len(executed)})", icon=":material/terminal:"):
                    for item in executed:
                        tool_name = item.get("tool", "")
                        args = item.get("args", {})
                        duration = item.get("seconds", 0)
                        is_err = item.get("is_error") or "error" in item
                        status_icon = "❌" if is_err else "✅"
                        st.markdown(f"**{status_icon} `{tool_name}`** ({duration:.2f}s)")
                        if args:
                            st.caption(f"Arguments: `{json.dumps(args)}`")
                        if "error" in item:
                            st.error(item["error"])

            st.markdown(content)

            # Render structured tables, metrics, and charts for executed tools
            for t_idx, item in enumerate(executed):
                payload = item.get("payload")
                if payload and not item.get("is_error") and item.get("tool") not in DISCOVERY_META_TOOLS:
                    tool_name = item.get("tool", f"tool_{t_idx}")
                    parsed = parse_payload(payload)
                    if parsed.metrics or parsed.tables:
                        with st.container(border=True):
                            if parsed.metrics:
                                m_items = list(parsed.metrics.items())
                                with st.container(horizontal=True):
                                    for key, value in m_items[:MAX_METRICS]:
                                        is_num = isinstance(value, int | float) and not isinstance(value, bool)
                                        st.metric(str(key), value if is_num else str(value), border=True)
                            for table_name, df in parsed.tables.items():
                                item_key = f"t_{idx}_{t_idx}_{table_name}"
                                st.markdown(f"**{table_name}** - {len(df):,} records")
                                view = st.segmented_control(
                                    "View", ["Table", "Chart", "Raw JSON"], default="Table", key=f"view_{item_key}",
                                    label_visibility="collapsed",
                                )
                                if view == "Chart":
                                    cats, nums = categorical_columns(df), numeric_columns(df)
                                    kinds = (["Count by category"] if cats else []) + (["Values by label"] if nums else [])
                                    if not kinds:
                                        st.info("No suitable categorical/numeric columns for charting.")
                                    else:
                                        kind = st.segmented_control("Chart type", kinds, default=kinds[0], key=f"kind_{item_key}")
                                        if kind == "Count by category":
                                            col = st.selectbox("Category column", cats, key=f"cat_{item_key}")
                                            st.bar_chart(count_by(df, col), x=col, y="count", alt=f"Count by {col}")
                                        elif kind == "Values by label":
                                            c_left, c_right = st.columns(2)
                                            val_col = c_right.selectbox("Value column", nums, key=f"val_{item_key}")
                                            lbl_cols = [c for c in df.columns if c != val_col]
                                            lbl_col = c_left.selectbox("Label column", lbl_cols, key=f"lbl_{item_key}")
                                            st.bar_chart(label_value_frame(df, lbl_col, val_col), x=lbl_col, y=val_col, alt=f"{val_col} by {lbl_col}")
                                elif view == "Raw JSON":
                                    st.json(json.loads(df.head(2000).to_json(orient="records", default_handler=str)), expanded=1)
                                else:
                                    render_paginated_table(df, item_key, tool_name, table_name)
                                st.download_button(
                                    "Download CSV",
                                    csv_safe(df).to_csv(index=False),
                                    file_name=f"{tool_name}_{table_name}.csv",
                                    mime="text/csv",
                                    icon=":material/download:",
                                    key=f"dl_{item_key}",
                                )
        else:
            st.markdown(content)

# --- Handle User Query ------------------------------------------------------
user_input = st.chat_input("Ask about keys, certificates, cryptographic assets, policies, compliance...")
query_to_run = user_input or st.session_state.pop("pending_user_query", None)

if query_to_run:
    if user_input:
        st.session_state.agent_chat_messages.append({"role": "user", "content": user_input})
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(query_to_run)

    with st.chat_message("assistant", avatar=":material/smart_toy:"):
        with st.status("Agent analyzing request and selecting GCM tools...", expanded=True) as status_box:
            def on_agent_step(desc: str, detail: dict | None = None) -> None:
                status_box.write(desc)

            try:
                final_answer, executed_tools, _ = run_agent_turn(
                    endpoint=endpoint,
                    token=token,
                    verify=verify,
                    dynamic_tools=dynamic_tools,
                    provider=active_provider,
                    ai_settings=provider_settings,
                    mcp_tools=tools,
                    conversation_history=st.session_state.agent_chat_messages,
                    on_step=on_agent_step,
                )
                status_box.update(label="Request fulfilled", state="complete", expanded=False)
            except Exception as exc:
                logger.error("Agent turn failed", exc_info=True)
                status_box.update(label="Execution failed", state="error", expanded=True)
                final_answer = f"I encountered an error executing your request: {exc}"
                executed_tools = []
                st.error(final_answer, icon=":material/error:")

        turn_index = len(st.session_state.agent_chat_messages)
        st.session_state.agent_turn_data[f"turn_{turn_index}"] = executed_tools
        st.session_state.agent_chat_messages.append({"role": "assistant", "content": final_answer})
        st.rerun()
