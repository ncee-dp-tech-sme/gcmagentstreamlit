# Change log:
# Fri Nov 20 01:39:30 CET 2026 - Initial version: allow-list validation for MCP and AI provider settings
"""Server-side validation of configuration inputs (fail closed, allow-list based)."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

MCP_PATH = "/ibm/mcp/mcp"
MAX_URL_LEN = 2048
MAX_TOKEN_LEN = 4096

_TOKEN_RE = re.compile(r"^[A-Za-z0-9\-._~+/=]{8,%d}$" % MAX_TOKEN_LEN)  # RFC 6750 token68 charset
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\-]{0,127}$")
_API_KEY_RE = re.compile(r"^[\x21-\x7e]{8,512}$")  # printable ASCII, no whitespace
_PROJECT_RE = re.compile(r"^[A-Za-z0-9\-]{8,64}$")
_CONTROL_RE = re.compile(r"[\x00-\x20\x7f]")


# Fri Nov 20 01:39:30 CET 2026 - Validate a base URL; returns (normalized URL, errors)
def validate_url(raw: str, *, require_https: bool = False, label: str = "URL") -> tuple[str, list[str]]:
    value = (raw or "").strip()
    if not value:
        return "", [f"{label} is required."]
    if len(value) > MAX_URL_LEN or _CONTROL_RE.search(value):
        return "", [f"{label} contains invalid characters or is too long."]
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        parts.port  # noqa: B018 - raises ValueError on an invalid port
    except ValueError:
        return "", [f"{label} is not a valid URL."]
    allowed = ("https",) if require_https else ("http", "https")
    if parts.scheme not in allowed:
        return "", [f"{label} must start with {' or '.join(s + '://' for s in allowed)}."]
    if not hostname:
        return "", [f"{label} must include a host name."]
    if parts.username or parts.password:
        return "", [f"{label} must not contain credentials."]
    if parts.query or parts.fragment:
        return "", [f"{label} must not contain a query string or fragment."]
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}", []


# Fri Nov 20 01:39:30 CET 2026 - Build the MCP endpoint (<GCMServerURL>/ibm/mcp/mcp) from a base URL
def build_mcp_endpoint(server_url: str) -> str:
    base = (server_url or "").strip().rstrip("/")
    return base if base.endswith(MCP_PATH) else base + MCP_PATH


# Fri Nov 20 01:39:30 CET 2026 - Validate a bearer token; strips an optional 'Bearer ' prefix
def validate_token(raw: str) -> tuple[str, list[str]]:
    value = (raw or "").strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    if not value:
        return "", ["Bearer token is required."]
    if not _TOKEN_RE.match(value):
        return "", ["Bearer token has an invalid format (8-4096 chars: letters, digits and -._~+/=)."]
    return value, []


# Fri Nov 20 01:39:30 CET 2026 - Validate the MCP server section
def validate_mcp(server_url: str, token: str, verify_ssl: bool) -> tuple[dict, list[str], list[str]]:
    url, errors = validate_url(server_url, label="GCM server URL")
    tok, token_errors = validate_token(token)
    errors += token_errors
    warnings: list[str] = []
    if url and url.startswith("http://"):
        warnings.append("The URL uses plain HTTP; the bearer token is sent unencrypted.")
    if not verify_ssl:
        warnings.append("SSL certificate verification is disabled; the server identity is not verified.")
    return {"server_url": url, "bearer_token": tok, "verify_ssl": bool(verify_ssl)}, errors, warnings


# Fri Nov 20 01:39:30 CET 2026 - Validate the settings of one AI provider
def validate_ai(provider: str, spec: dict, values: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    model = (values.get("model") or "").strip()
    if not _MODEL_RE.match(model):
        errors.append("Model is required (letters, digits and . _ : / - only, max 128 chars).")
    api_key = (values.get("api_key") or "").strip()
    if not _API_KEY_RE.match(api_key):
        errors.append(f"{spec['key_label']} is required (8-512 printable characters, no spaces).")
    base_url, url_errors = validate_url(values.get("base_url", ""), require_https=True, label="Endpoint URL")
    errors += url_errors
    cleaned = {"model": model, "api_key": api_key, "base_url": base_url}
    if spec.get("needs_project"):
        project = (values.get("project_id") or "").strip()
        if not _PROJECT_RE.match(project):
            errors.append("watsonx.ai project ID is required (8-64 letters, digits or hyphens).")
        cleaned["project_id"] = project
    return cleaned, errors
