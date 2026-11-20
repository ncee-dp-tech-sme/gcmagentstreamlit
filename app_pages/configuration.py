# Change log:
# Fri Nov 20 01:39:30 CET 2026 - Initial version: AI provider + GCM MCP server configuration page
# Fri Nov 20 03:55:54 CET 2026 - Added "Dynamic tool discovery" toggle (off by default)
import copy
import json

import streamlit as st

from utils.ai_providers import PROVIDERS, test_provider
from utils.config_store import clear_config, data_dir, restore_backup, save_config
from utils.logging_setup import debug_enabled
from utils.mcp_client import build_server_config, test_connection
from utils.validation import MCP_PATH, build_mcp_endpoint, validate_ai, validate_mcp

config = st.session_state.config
st.caption(
    "Settings are kept in this session and saved on request. "
    "Secrets are encrypted at rest and never shown again after saving."
)
tab_ai, tab_mcp, tab_store = st.tabs(
    [":material/smart_toy: AI provider", ":material/lock: GCM MCP server", ":material/storage: Saved settings"]
)

# ---------------------------------------------------------------------------
# AI provider
# ---------------------------------------------------------------------------
with tab_ai:
    providers = list(PROVIDERS)
    provider = st.selectbox(
        "AI provider",
        providers,
        index=providers.index(config["ai"]["provider"]),
        key="cfg_ai_provider",
        help="Provider used for AI features. Settings are stored per provider.",
    )
    spec = PROVIDERS[provider]
    stored = config["ai"]["providers"][provider]
    st.caption(spec["help"])
    if provider == "1min.ai":
        st.caption("Endpoint: OpenAI-compatible Chat Completions, base URL `https://api.1min.ai/openai/v1`.")

    with st.container(border=True):
        models = list(spec["models"])
        if stored["model"] and stored["model"] not in models:
            models.insert(0, stored["model"])
        model = st.selectbox(
            "Model",
            models,
            index=models.index(stored["model"]) if stored["model"] in models else 0,
            accept_new_options=True,
            key=f"cfg_ai_model_{provider}",
            help="Pick a model or type a custom model ID and press Enter.",
        )
        api_key_in = st.text_input(
            spec["key_label"],
            type="password",
            autocomplete="off",
            key=f"cfg_ai_key_{provider}",
            placeholder="Saved - leave blank to keep" if stored["api_key"] else "Paste your API key",
            help="Never displayed after saving. Leave blank to keep the saved key.",
        )
        base_url = st.text_input(
            "watsonx.ai URL" if provider == "IBM" else "Endpoint base URL",
            value=stored["base_url"],
            key=f"cfg_ai_url_{provider}",
            help="HTTPS only.",
        )
        project_id = ""
        if spec.get("needs_project"):
            project_id = st.text_input(
                "watsonx.ai project ID", value=stored.get("project_id", ""), key=f"cfg_ai_project_{provider}"
            )

        ai_values = {
            "model": model or "",
            "api_key": api_key_in or stored["api_key"],
            "base_url": base_url,
            "project_id": project_id,
        }
        with st.container(horizontal=True):
            save_ai = st.button("Save AI settings", type="primary", icon=":material/save:", key="cfg_ai_save")
            test_ai = st.button("Test credentials", icon=":material/network_check:", key="cfg_ai_test")

    if save_ai or test_ai:
        cleaned, errors = validate_ai(provider, spec, ai_values)
        if errors:
            for err in errors:
                st.error(err, icon=":material/error:")
        elif save_ai:
            new_cfg = copy.deepcopy(config)
            new_cfg["ai"]["provider"] = provider
            new_cfg["ai"]["providers"][provider].update(cleaned)
            try:
                save_config(new_cfg)
            except OSError:
                st.error("Could not save the configuration. Check permissions of the data directory.")
            else:
                st.session_state.config = new_cfg
                st.success(f"{provider} settings saved.", icon=":material/check_circle:")
        else:
            with st.spinner("Contacting provider..."):
                result = test_provider(provider, cleaned)
            if result.ok:
                st.success(result.message, icon=":material/check_circle:")
                for warning in result.warnings:
                    st.warning(warning)
            else:
                st.error(result.message, icon=":material/error:")
                if debug_enabled() and result.detail:
                    st.code(result.detail)

# ---------------------------------------------------------------------------
# Guardium Cryptography Manager MCP server
# ---------------------------------------------------------------------------
with tab_mcp:
    mcp_cfg = config["mcp"]
    with st.container(border=True):
        server_url = st.text_input(
            "GCM server URL",
            value=mcp_cfg["server_url"],
            placeholder="https://gcm.example.com",
            key="cfg_mcp_url",
            help=f"Base URL of the GCM server. The MCP path `{MCP_PATH}` is appended automatically.",
        )
        token_in = st.text_input(
            "Bearer token",
            type="password",
            autocomplete="off",
            key="cfg_mcp_token",
            placeholder="Saved - leave blank to keep" if mcp_cfg["bearer_token"] else "Paste the GCM API key / token",
            help="Sent as 'Authorization: Bearer <token>'. Never displayed after saving.",
        )
        verify_ssl = st.toggle(
            "Verify SSL certificate",
            value=mcp_cfg["verify_ssl"],
            key="cfg_mcp_verify",
            help="Turn off only for test systems with self-signed certificates.",
        )
        if not verify_ssl:
            st.warning(
                "Certificate verification is off. Traffic can be intercepted; use only on trusted networks.",
                icon=":material/warning:",
            )
        dynamic_tools = st.toggle(
            "Dynamic tool discovery",
            value=mcp_cfg.get("dynamic_tools", False),
            key="cfg_mcp_dynamic",
            help=(
                "Sends the 'x-mcp-enable-discovery' header so the server exposes only meta-tools "
                "(search, get_schema, execute). Off by default: the real GCM tools are listed directly."
            ),
        )

        token_value = token_in or mcp_cfg["bearer_token"]
        mcp_values, mcp_errors, mcp_warnings = validate_mcp(server_url, token_value, verify_ssl)
        mcp_values["dynamic_tools"] = dynamic_tools
        endpoint = build_mcp_endpoint(mcp_values["server_url"] or server_url) if server_url.strip() else ""
        if endpoint:
            st.caption(f"MCP endpoint: `{endpoint}`")

        with st.container(horizontal=True):
            save_mcp = st.button("Save MCP settings", type="primary", icon=":material/save:", key="cfg_mcp_save")
            test_mcp = st.button("Test connection", icon=":material/network_check:", key="cfg_mcp_test")

    with st.expander("MCP server configuration structure", expanded=True, icon=":material/data_object:"):
        st.code(
            json.dumps(
                build_server_config(
                    endpoint or f"<GCMServerURL>{MCP_PATH}", token_value, verify_ssl,
                    mask=True, dynamic_tools=dynamic_tools,
                ),
                indent=2,
            ),
            language="json",
        )
        st.caption(
            "The token is masked. Required headers: Authorization, token_type"
            + (", x-mcp-enable-discovery." if dynamic_tools else ". x-mcp-enable-discovery is only sent when dynamic tool discovery is on.")
        )

    if save_mcp or test_mcp:
        if mcp_errors:
            for err in mcp_errors:
                st.error(err, icon=":material/error:")
        else:
            for warning in mcp_warnings:
                st.warning(warning)
            if save_mcp:
                new_cfg = copy.deepcopy(config)
                new_cfg["mcp"].update(mcp_values)
                try:
                    save_config(new_cfg)
                except OSError:
                    st.error("Could not save the configuration. Check permissions of the data directory.")
                else:
                    st.session_state.config = new_cfg
                    st.success("MCP settings saved.", icon=":material/check_circle:")
            else:
                with st.spinner("Connecting to the MCP server..."):
                    result = test_connection(
                        build_mcp_endpoint(mcp_values["server_url"]), mcp_values["bearer_token"], verify_ssl, dynamic_tools
                    )
                if result.ok:
                    st.success(result.message, icon=":material/check_circle:")
                    if result.server_name:
                        st.caption(f"Server: {result.server_name} {result.server_version}")
                    if result.tools:
                        with st.expander(f"Discovered tools ({len(result.tools)})"):
                            st.dataframe(
                                [{"name": t["name"], "description": t.get("description", "")} for t in result.tools],
                                hide_index=True,
                                alt="Tools discovered on the MCP server",
                            )
                else:
                    st.error(result.message, icon=":material/error:")
                    if debug_enabled() and result.detail:
                        st.code(result.detail)

# ---------------------------------------------------------------------------
# Saved settings management
# ---------------------------------------------------------------------------
with tab_store:
    st.write(f"Stored in `{data_dir()}` (owner-only permissions, secrets encrypted).")
    with st.container(horizontal=True):
        if st.button("Restore previous version", icon=":material/history:", key="cfg_restore"):
            if restore_backup():
                st.session_state.pop("config")
                st.rerun()
            else:
                st.info("No previous version available.")
        if st.button("Delete saved settings", icon=":material/delete:", key="cfg_delete"):
            clear_config()
            st.session_state.pop("config")
            st.rerun()
