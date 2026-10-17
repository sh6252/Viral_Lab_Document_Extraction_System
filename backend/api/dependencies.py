"""Explicit review dependency injection; no header auth, policy or DB defaults."""

from dataclasses import replace
from uuid import uuid4

from fastapi import Request

from backend.core.review_access import AuthenticatedPrincipal, AuthorizationPolicy, ReviewBoundaryError
from backend.services.review_api import ReviewAPIService


async def require_authenticated_principal(request: Request) -> AuthenticatedPrincipal:
    cached = getattr(request.state, "review_verified_principal", None)
    if isinstance(cached, AuthenticatedPrincipal):
        return cached
    authenticator = getattr(request.app.state, "review_authenticator", None)
    if authenticator is None:
        raise ReviewBoundaryError("AUTHENTICATION_REQUIRED")
    try:
        principal = await authenticator.authenticate(request)
    except Exception:
        raise ReviewBoundaryError("AUTHENTICATION_REQUIRED") from None
    if not isinstance(principal, AuthenticatedPrincipal):
        raise ReviewBoundaryError("AUTHENTICATION_REQUIRED")
    principal = replace(principal, correlation_id=uuid4().hex)
    request.state.review_verified_principal = principal
    return principal


def get_review_authorization_policy(request: Request) -> AuthorizationPolicy:
    policy = getattr(request.app.state, "review_authorization_policy", None)
    if policy is None:
        raise ReviewBoundaryError("REVIEW_RESOURCE_NOT_FOUND")
    return policy


def get_review_api_service(request: Request) -> ReviewAPIService:
    service = getattr(request.app.state, "review_api_service", None)
    if service is None:
        raise ReviewBoundaryError("REVIEW_SERVICE_UNAVAILABLE")
    return service
