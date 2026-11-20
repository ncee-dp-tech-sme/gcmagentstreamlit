# Change log:
# Fri Nov 20 01:39:30 CET 2026 - Initial version: map network/HTTP failures to safe user messages
"""Translate network and HTTP errors into generic, non-revealing user messages."""
from __future__ import annotations

import requests

CONNECT_TIMEOUT = 5
READ_TIMEOUT = 30
TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)


# Fri Nov 20 01:39:30 CET 2026 - Message for an unexpected HTTP status code
def http_status_message(status: int) -> str:
    if status == 401:
        return "Authentication failed (HTTP 401). Check the token or API key."
    if status == 403:
        return "Access denied (HTTP 403). The credential lacks permission."
    if status == 404:
        return "Endpoint not found (HTTP 404). Check the server URL."
    if status == 429:
        return "Rate limit or quota reached (HTTP 429). Try again later."
    if 500 <= status < 600:
        return f"The server reported an internal error (HTTP {status})."
    return f"Unexpected response from the server (HTTP {status})."


# Fri Nov 20 01:39:30 CET 2026 - Convert a requests exception to a (generic message, detail) tuple
def describe_request_error(exc: Exception) -> tuple[str, str]:
    detail = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, requests.exceptions.SSLError):
        return (
            "TLS certificate verification failed. Use a trusted certificate or, "
            "for a trusted test system only, disable verification.",
            detail,
        )
    if isinstance(exc, requests.exceptions.ConnectTimeout | requests.exceptions.ReadTimeout):
        return "The server did not respond in time.", detail
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "Could not connect to the server. Check the URL, DNS and network access.", detail
    if isinstance(exc, requests.exceptions.RequestException):
        return "The request failed.", detail
    return "An unexpected error occurred.", detail
