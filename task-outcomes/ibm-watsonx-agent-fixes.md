# IBM watsonx.ai Agent Fixes

**Prompt:** Fix IBM watsonx.ai LLM integration errors in the GCM Streamlit agent.
**Timestamp:** Fri Nov 20 05:17:41 CET 2026

---

## Fix 1 — 404 on first request (`/v1/chat/completions` not found)

**File:** `utils/agent_engine.py`

The IBM branch was building the wrong URL path. watsonx.ai does not use the OpenAI-style `/v1/chat/completions`. Fixed:
- URL: `{base_url}/ml/v1/text/chat?version=2024-10-08`
- Payload field: `model_id` instead of `model`

---

## Fix 2 — "Your input is lacking necessary details" (wrong model + wrong API fields)

**Files:** `utils/agent_engine.py`, `utils/ai_providers.py`

Three IBM-specific API incompatibilities and a wrong default model:

1. **`tool_choice_option: "auto"`** — watsonx.ai uses this instead of OpenAI's `tool_choice: "auto"`
2. **`max_tokens: 4096`** — watsonx.ai chat API does not accept `temperature`; uses `max_tokens`
3. **Default model `ibm/granite-3-3-8b-instruct`** does not support tool calling per IBM docs table — changed default to `meta-llama/llama-3-3-70b-instruct`

---

## Fix 3 — 404 for `ibm/granite-3-8b-instruct` (deprecated model)

**File:** `utils/ai_providers.py`

`granite-3-8b-instruct` is deprecated and returns 404. Replaced with `ibm/granite-3-3-8b-instruct` (active, chat-only). Updated model list:
- `meta-llama/llama-3-3-70b-instruct` (default, tool calling ✅)
- `mistralai/mistral-large` (tool calling ✅)
- `ibm/granite-3-3-8b-instruct` (chat only, no tool calling)

---

## Fix 4 — "Your function definitions are not comprehensive enough" (tool schema too large)

**File:** `utils/agent_engine.py`

Llama 3.3 70B on watsonx.ai rejects the full GCM tool payload (~60 tools with verbose schemas). Added `slim=True` mode to `tools_to_openai_schema()`:
- Strips `description`, `examples`, `default` from each tool property
- Truncates tool descriptions to 256 chars (vs 1024 for other providers)
- Reduces payload size ~70-80% while preserving all functional schema info
- Activated automatically for `provider == "IBM"`
