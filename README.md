> [!WARNING]
> **Work in progress.** This application is under active development and not every prompt produces a correct or complete answer. The agent relies on an AI model that can choose wrong tools, parameters or filters, and some GCM server calls fail with HTTP 400/500 errors.
> **Before showing it to a client, test every question you plan to ask** against your own GCM server and verify the results (compare them with the GCM UI or the *Explorer* page). Do not treat the output as authoritative without checking it.

# Guardium Cryptography Manager Agent & Console

A Streamlit application designed to help users interact with **Guardium Cryptography Manager (GCM)** like an autonomous agent. Users make natural language requests for cryptographic data, key management, certificates, or security operations, and the agent autonomously determines, coordinates, and executes the required GCM MCP tools without requiring users to specify endpoints or technical parameters.

**repository url: https://github.com/ncee-dp-tech-sme/gcmagentstreamlit**
** hosted version: https://gcmagentapp.streamlit.app/**
---

## Agent Operational Protocol

The application operates according to five core agent principles:

1. **Natural Language Intent Analysis**:
   - Treats user queries regarding keys, certificates, cryptographic assets, policies, compliance, or security operations as natural language requests.
   - Autonomously determines the necessary operations and data domains without requiring manual tool selection or technical parameter entry from the user.

2. **Autonomous Tool Discovery & Multi-Step Execution**:
   - Dynamically discovers available GCM tools via the Model Context Protocol (MCP) endpoint (`/ibm/mcp/mcp`).
   - Converts MCP tool definitions to standard tool calling schemas and coordinates single or multi-step tool invocations across GCM endpoints.
   - **Dynamic tool discovery mode** (optional, **off by default**; enable with `GCM_DYNAMIC_TOOLS=true` in `.env` or the "Dynamic tool discovery" toggle under Configuration → GCM MCP server; when off, the `x-mcp-enable-discovery` header is not sent and the real tools are listed directly). When on (`x-mcp-enable-discovery: true`), the server exposes only the meta-tools `search`, `get_schema`, `tags`, `list_tools` and `execute`; the ~60 real GCM tools are called through `execute` (`await call_tool(name, params)`). The agent receives the real tool-name catalog in its prompt, follows search → `get_schema` → `execute`, and the engine refuses to run any tool in `execute` until its schema has been inspected (the schema is returned instead, so the model corrects its parameters).
   - Handles Bearer authentication, session management, and HTTP/SSE transport.

3. **Data Transformation & Clear Actionable Presentation**:
   - Normalizes arbitrary JSON/structured payloads into KPI summary metric cards, paginated dataframes, category/value bar charts, raw JSON viewers, and formula-injection-safe CSV exports.

4. **Transparent Attribution & Explanations**:
   - Always explains which GCM tools and endpoints were called, what data was retrieved, and why it is relevant to fulfilling the user's request.

5. **Proactive Clarification on Ambiguity**:
   - When requests are ambiguous, lack required operational scope (e.g., specific key aliases, date ranges, or server targets), or have multiple interpretations, the agent asks targeted clarifying questions before executing speculative operations.

---

## Agent Safeguards & Known Limitations

The agent engine (`utils/agent_engine.py`) corrects known GCM server quirks before calling a tool, because AI models often get them wrong:

- **Column names**: `crypto_object_name` is rewritten to `name`, `key_usage` is dropped, and `expiry` becomes `not_after` for certificate-only requests (the server returns HTTP 500 for the originals).
- **Paging**: `page_number` of the asset list is 1-based (0 returns HTTP 400); it is corrected to 1. `search_policies` is the exception: its `page` is 0-based, `size` defaults to 10 (max 100), and it reports `totalItems` / `totalPages` for the page size requested.
- **Date filters**: the server rejects date comparisons such as `not_after=lt=2026-12-31` (HTTP 500). The engine removes them from the request, fetches the full list (`page_size` 1000) and applies the condition to the returned rows, so tables and totals only show matching items. If the model forgets the date, a request such as "certificates expiring before 31-12-2026" is parsed and the condition is added automatically (only for certificate lookups through `fetch_detailed_asset_list_by_crypto_objects`).
- **Dynamic discovery mode**: model-written `execute` code is only partly corrected (column names); date filtering must be done in the generated Python.

Known limitations:

- Answers depend on the selected AI provider and model; results can differ between runs.
- Only the question types above have been verified; other prompts may fail or return partial data. Use the *Explorer* page to cross-check results.
- Date-restriction detection only recognizes phrases like "expiring/valid until/not after ... before/until/by/after <date>".

---

## Application Pages

- **Agent (`app_pages/agent.py`)**:
  - Primary conversational interface using native Streamlit chat elements (`st.chat_message`, `st.chat_input`).
  - Quick-start starter suggestions for common GCM tasks (keys, certificates, inventory, policies).
  - Live execution progress indicator showing tool selection and invocation steps.
  - Expandable tool execution summary with duration and status attribution.
  - Interactive data tables, KPI metrics, charts, and CSV downloads rendered inline alongside the agent's explanation.

- **Explorer (`app_pages/dashboard.py`)**:
  - Interactive tool catalog and manual execution console for discovered GCM MCP tools.
  - Read-only filter toggle, tool multiselector, dynamic JSON argument inputs, and bulk fetch.
  - Rich views: Summary KPI cards, interactive tables, bar charts (count by category / values by label), raw JSON inspect, and CSV export.

- **Configuration (`app_pages/configuration.py`)**:
  - **AI Provider**: Configure 1min.ai (OpenAI-compatible endpoint `https://api.1min.ai/openai/v1`), IBM watsonx.ai (IAM token exchange + project ID), OpenAI, or Google Gemini. Includes connection and credential testing.
  - **GCM MCP Server**: Configure GCM server base URL (`/ibm/mcp/mcp` appended automatically), Bearer token, SSL verification toggle, and dynamic tool discovery toggle (off by default). Includes MCP handshake and tool discovery testing.
  - **Saved Settings**: View storage location, restore previous configuration versions (`config.json.bak`), or reset settings.

---

## Security, Persistence & Validation

- **Encryption at Rest**: Saved secrets (Bearer tokens, API keys) are encrypted using Fernet cryptography with 0600 file permissions in `data/config.json`. The key is sourced from `GCM_APP_ENCRYPTION_KEY` in `.env` or auto-generated at `data/.secret.key`.
- **Credential Masking**: Secrets are masked in configuration displays and are never displayed in plaintext after saving.
- **Input Validation**: Server-side URL validation, HTTP header injection prevention on tokens, and strict HTTPS enforcement on AI endpoints.
- **Localhost Binding**: Configured to bind strictly to `127.0.0.1` (`.streamlit/config.toml`).
- **Structured Logging**: JSON logging with configurable log levels (`LOG_LEVEL=INFO`/`DEBUG`). Technical stack traces and error details are concealed from users unless `DEBUG` is enabled.

---

## Quick Start

### 1. Environment Setup

```bash
# Activate virtual environment
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment variables (optional defaults)
cp .env.example .env
```

Relevant `.env` options (see `.env.example`): `LOG_LEVEL`, `DATA_DIR`, `GCM_APP_ENCRYPTION_KEY`, `GCM_SERVER_URL`, `GCM_MCP_TOKEN`, `GCM_VERIFY_SSL`, `GCM_DYNAMIC_TOOLS` (default `false`), `AI_PROVIDER` and the provider API keys.

### 2. Run Application

```bash
streamlit run streamlit_app.py
```
The application opens at `http://127.0.0.1:8501`.

### 3. Run Tests

```bash
python -m pytest -q
```

---

## Project Structure

```
├── streamlit_app.py          # Application entrypoint & st.navigation setup
├── app_pages/
│   ├── agent.py              # Autonomous GCM AI Agent conversational page
│   ├── dashboard.py          # Explorer / tool catalog & manual query page
│   └── configuration.py      # AI provider & GCM MCP server settings page
├── utils/
│   ├── agent_engine.py       # Agent tool calling loop & AI provider integration
│   ├── ai_providers.py       # AI provider specifications & credential test
│   ├── config_store.py       # Encrypted persistence (Fernet) & .env defaults
│   ├── logging_setup.py      # Structured JSON logging
│   ├── mcp_client.py         # Streamable HTTP/SSE JSON-RPC MCP client
│   ├── net.py                # Network timeout & error description utilities
│   ├── render.py             # Data normalizer (tables, metrics, charts, CSV)
│   └── validation.py         # Server-side input & configuration validation
├── tests/
│   └── test_app.py           # Unit tests (validation, encryption, MCP, agent loop)
├── requirements.txt          # Python dependencies
├── pytest.ini                # Pytest configuration
└── README.md                 # Project documentation
```
