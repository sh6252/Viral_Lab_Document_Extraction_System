"""Shared provider error diagnostics and classification helpers."""

from __future__ import annotations


# Only documented machine codes may enter reports; never copy arbitrary SDK text.
SAFE_PROVIDER_STATUSES = frozenset(
    {
        "INVALID_ARGUMENT", "PERMISSION_DENIED", "UNAUTHENTICATED", "NOT_FOUND",
        "RESOURCE_EXHAUSTED", "UNAVAILABLE", "INTERNAL", "DEADLINE_EXCEEDED",
        "insufficient_quota", "billing_hard_limit_reached", "quota_exceeded",
        "rate_limit_exceeded", "invalid_api_key", "invalid_request_error",
        "server_error", "model_not_found",
    }
)


def safe_provider_diagnostics(exc: Exception) -> dict[str, str | int]:
    diagnostics: dict[str, str | int] = {
        "exception_class": type(exc).__name__,
        "exception_module": type(exc).__module__,
    }
    status_code = extract_status_code(exc)
    if status_code is not None:
        diagnostics["http_status_code"] = status_code
    provider_status = extract_provider_status(exc)
    if provider_status:
        diagnostics["provider_status"] = provider_status
    return diagnostics


def extract_status_code(exc: Exception) -> int | None:
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(candidate, int):
            return candidate
        if isinstance(candidate, str) and candidate.isdecimal():
            return int(candidate)

    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    if isinstance(code, str) and code.isdecimal():
        return int(code)
    return None


def extract_provider_status(exc: Exception) -> str | None:
    body = getattr(exc, "body", None)
    error_body = body.get("error", body) if isinstance(body, dict) else {}
    if not isinstance(error_body, dict):
        error_body = {}
    for candidate in (
        getattr(exc, "status", None),
        getattr(exc, "code", None),
        getattr(exc, "type", None),
        error_body.get("code"),
        error_body.get("type"),
    ):
        if isinstance(candidate, str) and candidate in SAFE_PROVIDER_STATUSES:
            return candidate
    return None


def is_transport_error(exc: Exception) -> bool:
    if isinstance(exc, ConnectionError):
        return True
    name = type(exc).__name__.lower()
    module = type(exc).__module__.lower()
    transport_markers = (
        "connect",
        "connection",
        "network",
        "transport",
        "socket",
        "ssl",
        "tls",
        "proxy",
        "dns",
        "nameresolution",
    )
    return any(marker in name or marker in module for marker in transport_markers)
