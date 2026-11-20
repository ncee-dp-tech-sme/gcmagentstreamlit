# Change log:
# Fri Nov 20 01:39:30 CET 2026 - Initial version: multipage entrypoint (Dashboard, Configuration)
# Fri Nov 20 02:03:54 CET 2026 - Added autonomous GCM Agent page navigation
"""Guardium Cryptography Manager console - Streamlit entrypoint."""
import streamlit as st

from utils.config_store import load_config
from utils.logging_setup import get_logger

st.set_page_config(page_title="GCM console", page_icon=":material/lock:", layout="wide")
get_logger()

# Per-user configuration, loaded once per session from the encrypted store (+ .env defaults)
if "config" not in st.session_state:
    st.session_state.config = load_config()

page = st.navigation(
    [
        st.Page(
            "app_pages/agent.py",
            title="Agent",
            icon=":material/smart_toy:",
            default=True,
            help="Autonomous Guardium Cryptography Manager AI agent: natural language queries, multi-step tool execution, and explanations.",
        ),
        st.Page(
            "app_pages/dashboard.py",
            title="Explorer",
            icon=":material/dashboard:",
            help="Data returned by the Guardium Cryptography Manager MCP server: tables, metrics and charts.",
        ),
        st.Page(
            "app_pages/configuration.py",
            title="Configuration",
            icon=":material/settings:",
            help="Configure the AI provider and the Guardium Cryptography Manager MCP server connection.",
        ),
    ],
    position="top",
)

st.title(page.title, icon=page.icon)
page.run()
