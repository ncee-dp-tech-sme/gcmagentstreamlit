# Change log:
# Fri Nov 20 01:39:30 CET 2026 - Initial version: encrypted JSON config persistence with .env defaults
# Fri Nov 20 03:55:54 CET 2026 - Added optional mcp.dynamic_tools setting (GCM_DYNAMIC_TOOLS, default false)
"""Persist configuration across sessions. Secrets are encrypted with Fernet (AES-128-CBC + HMAC).

The key comes from GCM_APP_ENCRYPTION_KEY (.env) or, if unset, a generated 0600 key file in DATA_DIR.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

from utils.ai_providers import PROVIDERS, provider_defaults
from utils.logging_setup import get_logger

load_dotenv()
logger = get_logger()

ENC_PREFIX = "enc:v1:"
CONFIG_NAME = "config.json"
KEY_NAME = ".secret.key"


# Fri Nov 20 01:39:30 CET 2026 - Directory holding the persisted config (DATA_DIR, default ./data)
def data_dir() -> Path:
    path = Path(os.environ.get("DATA_DIR", "data")).expanduser()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


# Fri Nov 20 01:39:30 CET 2026 - Write bytes atomically with owner-only permissions
def _write_private(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# Fri Nov 20 01:39:30 CET 2026 - Load the Fernet key from env or key file (created on first use)
def _fernet() -> Fernet:
    env_key = os.environ.get("GCM_APP_ENCRYPTION_KEY", "").strip()
    if env_key:
        return Fernet(env_key.encode())
    key_path = data_dir() / KEY_NAME
    if not key_path.exists():
        _write_private(key_path, Fernet.generate_key())
    return Fernet(key_path.read_bytes().strip())


# Fri Nov 20 01:39:30 CET 2026 - Encrypt a secret string
def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return ENC_PREFIX + _fernet().encrypt(value.encode()).decode()


# Fri Nov 20 01:39:30 CET 2026 - Decrypt a stored secret; returns "" if it cannot be decrypted
def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(ENC_PREFIX):
        return ""  # never accept plaintext secrets from disk
    try:
        return _fernet().decrypt(value[len(ENC_PREFIX):].encode()).decode()
    except (InvalidToken, ValueError):
        logger.warning("Stored secret could not be decrypted (encryption key changed?)")
        return ""


# Fri Nov 20 01:39:30 CET 2026 - Apply fn to every secret field of a config dict (in place)
def _map_secrets(cfg: dict, fn) -> dict:
    cfg["mcp"]["bearer_token"] = fn(cfg["mcp"].get("bearer_token", ""))
    for settings in cfg["ai"]["providers"].values():
        settings["api_key"] = fn(settings.get("api_key", ""))
    return cfg


def _truthy(value: str, default: bool = True) -> bool:
    return default if not value.strip() else value.strip().lower() in {"1", "true", "yes", "on"}


# Fri Nov 20 01:39:30 CET 2026 - Default configuration, seeded from environment variables (.env)
def default_config() -> dict:
    providers = {}
    for name, spec in PROVIDERS.items():
        settings = provider_defaults(name)
        settings["api_key"] = os.environ.get(spec["env_key"], "").strip()
        if name == "IBM":
            settings["base_url"] = os.environ.get("IBM_URL", settings["base_url"]).strip()
            settings["project_id"] = os.environ.get("IBM_PROJECT_ID", "").strip()
        providers[name] = settings
    provider = os.environ.get("AI_PROVIDER", "1min.ai").strip()
    return {
        "ai": {"provider": provider if provider in PROVIDERS else "1min.ai", "providers": providers},
        "mcp": {
            "server_url": os.environ.get("GCM_SERVER_URL", "").strip(),
            "bearer_token": os.environ.get("GCM_MCP_TOKEN", "").strip(),
            "verify_ssl": _truthy(os.environ.get("GCM_VERIFY_SSL", "")),
            "dynamic_tools": _truthy(os.environ.get("GCM_DYNAMIC_TOOLS", ""), default=False),
        },
    }


# Fri Nov 20 01:39:30 CET 2026 - Merge saved values over defaults, keeping only known keys
def _merge(defaults: dict, saved: dict) -> dict:
    merged = copy.deepcopy(defaults)
    ai = saved.get("ai", {}) if isinstance(saved.get("ai"), dict) else {}
    if ai.get("provider") in PROVIDERS:
        merged["ai"]["provider"] = ai["provider"]
    for name, settings in (ai.get("providers") or {}).items():
        if name in merged["ai"]["providers"] and isinstance(settings, dict):
            for key in merged["ai"]["providers"][name]:
                if isinstance(settings.get(key), str):
                    merged["ai"]["providers"][name][key] = settings[key]
    mcp = saved.get("mcp", {}) if isinstance(saved.get("mcp"), dict) else {}
    for key in ("server_url", "bearer_token"):
        if isinstance(mcp.get(key), str):
            merged["mcp"][key] = mcp[key]
    for key in ("verify_ssl", "dynamic_tools"):
        if isinstance(mcp.get(key), bool):
            merged["mcp"][key] = mcp[key]
    return merged


# Fri Nov 20 01:39:30 CET 2026 - Decrypt the secret fields of a saved config dict (in place)
def _decrypt_saved(saved: dict) -> None:
    mcp = saved.get("mcp")
    if isinstance(mcp, dict) and isinstance(mcp.get("bearer_token"), str):
        mcp["bearer_token"] = decrypt_secret(mcp["bearer_token"])
    ai = saved.get("ai")
    providers = ai.get("providers") if isinstance(ai, dict) else None
    for settings in (providers or {}).values():
        if isinstance(settings, dict) and isinstance(settings.get("api_key"), str):
            settings["api_key"] = decrypt_secret(settings["api_key"])


# Fri Nov 20 01:39:30 CET 2026 - Load the persisted configuration (defaults if none or unreadable)
def load_config() -> dict:
    defaults = default_config()
    path = data_dir() / CONFIG_NAME
    if not path.exists():
        return defaults
    try:
        saved = json.loads(path.read_text())
        if not isinstance(saved, dict):
            raise ValueError("config root is not an object")
        _decrypt_saved(saved)
        return _merge(defaults, saved)
    except (OSError, ValueError) as exc:
        logger.error("Could not read saved configuration", extra={"error": type(exc).__name__})
        return defaults


# Fri Nov 20 01:39:30 CET 2026 - Persist the configuration (secrets encrypted, previous file kept as .bak)
def save_config(cfg: dict) -> None:
    path = data_dir() / CONFIG_NAME
    if path.exists():
        shutil.copy2(path, path.with_suffix(".json.bak"))
        os.chmod(path.with_suffix(".json.bak"), 0o600)
    encrypted = _map_secrets(copy.deepcopy(cfg), encrypt_secret)
    _write_private(path, json.dumps(encrypted, indent=2).encode())
    logger.info("Configuration saved")


# Fri Nov 20 01:39:30 CET 2026 - Restore the previous config from the .bak file; True on success
def restore_backup() -> bool:
    path = data_dir() / CONFIG_NAME
    backup = path.with_suffix(".json.bak")
    if not backup.exists():
        return False
    shutil.copy2(backup, path)
    os.chmod(path, 0o600)
    return True


# Fri Nov 20 01:39:30 CET 2026 - Delete the persisted configuration
def clear_config() -> None:
    (data_dir() / CONFIG_NAME).unlink(missing_ok=True)
    logger.info("Saved configuration deleted")
