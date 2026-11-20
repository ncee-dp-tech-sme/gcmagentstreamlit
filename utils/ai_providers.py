# Change log:
# Fri Nov 20 01:39:30 CET 2026 - Initial version: AI provider catalog and connection test
"""AI provider catalog (1min.ai, IBM watsonx.ai, OpenAI, Gemini) and credential test."""
from __future__ import annotations

from dataclasses import dataclass, field

import requests

from utils.logging_setup import get_logger
from utils.net import TIMEOUT, describe_request_error, http_status_message

logger = get_logger()

# 1min.ai models from the OpenAI-compatible endpoint specification (1minai-openai-api.txt)
ONEMIN_MODELS = [
    "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo",
    "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5.1", "gpt-5.2", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano",
    "gpt-5.5", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna",
    "o3", "o3-mini", "deepseek-flash", "deepseek-v4-pro",
    "qwen-flash", "qwen-plus", "qwen-max", "qwen3-max", "qwen3.6-plus", "qwen3.7-plus", "qwen3.7-max",
    "glm-5", "glm-5.1", "glm-5.2", "glm-5.3",
    "magistral-medium-latest", "magistral-small-latest", "mistral-large-latest", "mistral-medium-latest",
    "mistral-small-latest", "open-mistral-nemo",
    "moonshotai/kimi-k2.6", "moonshotai/kimi-k2.7-code",
    "meta/muse-spark-1.1", "meta/muse-spark-1.2", "meta/muse-spark-1.3",
]

IBM_IAM_URL = "https://iam.cloud.ibm.com/identity/token"

PROVIDERS: dict[str, dict] = {
    "1min.ai": {
        "base_url": "https://api.1min.ai/openai/v1",
        "models": ONEMIN_MODELS,
        "default_model": "gpt-4.1-mini",
        "env_key": "ONEMIN_API_KEY",
        "key_label": "1min.AI API key",
        "help": "OpenAI-compatible Chat Completions endpoint. Use your 1min.AI key, not a provider key.",
    },
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini", "o3-mini"],
        "default_model": "gpt-4.1-mini",
        "env_key": "OPENAI_API_KEY",
        "key_label": "OpenAI API key",
        "help": "Official OpenAI API.",
    },
    "Gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
        "default_model": "gemini-2.5-flash",
        "env_key": "GEMINI_API_KEY",
        "key_label": "Gemini API key",
        "help": "Google Gemini through its OpenAI-compatible endpoint.",
    },
    "IBM": {
        "base_url": "https://us-south.ml.cloud.ibm.com",
        "models": [
            "ibm/granite-3-3-8b-instruct",
            "ibm/granite-3-8b-instruct",
            "meta-llama/llama-3-3-70b-instruct",
            "mistralai/mistral-large",
        ],
        "default_model": "ibm/granite-3-3-8b-instruct",
        "env_key": "IBM_API_KEY",
        "key_label": "IBM Cloud API key",
        "help": "IBM watsonx.ai. Requires an IBM Cloud API key and a watsonx.ai project ID.",
        "needs_project": True,
    },
}


@dataclass
class ProviderTestResult:
    """Outcome of a provider test; `detail` is shown only when DEBUG logging is on."""

    ok: bool
    message: str
    detail: str = ""
    warnings: list[str] = field(default_factory=list)


# Fri Nov 20 01:39:30 CET 2026 - Return the built-in settings of a provider
def provider_defaults(provider: str) -> dict:
    spec = PROVIDERS[provider]
    out = {"model": spec["default_model"], "api_key": "", "base_url": spec["base_url"]}
    if spec.get("needs_project"):
        out["project_id"] = ""
    return out


# Fri Nov 20 01:39:30 CET 2026 - Validate provider credentials with a read-only request
def test_provider(provider: str, settings: dict) -> ProviderTestResult:
    try:
        if provider == "IBM":
            return _test_ibm(settings)
        return _test_openai_compatible(provider, settings)
    except requests.exceptions.RequestException as exc:
        message, detail = describe_request_error(exc)
        logger.warning("AI provider test failed", extra={"provider": provider, "error": type(exc).__name__})
        return ProviderTestResult(False, message, detail)


# Fri Nov 20 01:39:30 CET 2026 - GET {base}/models with Bearer auth (OpenAI-compatible)
def _test_openai_compatible(provider: str, settings: dict) -> ProviderTestResult:
    url = settings["base_url"].rstrip("/") + "/models"
    resp = requests.get(
        url, headers={"Authorization": f"Bearer {settings['api_key']}"}, timeout=TIMEOUT, verify=True
    )
    if resp.status_code != 200:
        return ProviderTestResult(False, http_status_message(resp.status_code))
    try:
        data = resp.json().get("data", [])
        ids = {str(m.get("id", "")).removeprefix("models/") for m in data if isinstance(m, dict)}
    except (ValueError, AttributeError):
        return ProviderTestResult(False, "The endpoint did not return a valid model list.")
    warnings = []
    if ids and settings["model"].removeprefix("models/") not in ids:
        warnings.append(f"Model '{settings['model']}' is not in the provider's current model list.")
    return ProviderTestResult(True, f"Connected to {provider}. {len(ids)} models available.", warnings=warnings)


# Fri Nov 20 01:39:30 CET 2026 - Exchange the IBM Cloud API key for an IAM token
def _test_ibm(settings: dict) -> ProviderTestResult:
    resp = requests.post(
        IBM_IAM_URL,
        data={"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": settings["api_key"]},
        headers={"Accept": "application/json"},
        timeout=TIMEOUT,
        verify=True,
    )
    if resp.status_code != 200:
        return ProviderTestResult(False, http_status_message(resp.status_code))
    return ProviderTestResult(True, "IBM Cloud API key accepted (IAM token issued).")
