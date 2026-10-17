"""Injected review security boundaries; no credentials, ACL store or default grants."""

from dataclasses import dataclass
from typing import Literal, Protocol

ReviewOperation = Literal["read_review", "submit_review_decision", "read_source_artifact"]


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Verified adapter output, never a public request model."""

    issuer: str
    subject: str
    permissions: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not all(isinstance(v, str) and v.strip() for v in (self.issuer, self.subject)):
            raise ValueError("INVALID_VERIFIED_PRINCIPAL")
        if any(not isinstance(values, tuple) or any(not isinstance(v, str) for v in values)
               for values in (self.permissions, self.roles)):
            raise ValueError("IMMUTABLE_VERIFIED_PERMISSIONS_REQUIRED")


@dataclass(frozen=True, slots=True)
class ReviewResourceScope:
    session_id: str
    document_id: str
    form_id: str

    def __post_init__(self) -> None:
        if not all(isinstance(v, str) and v.strip() for v in
                   (self.session_id, self.document_id, self.form_id)):
            raise ValueError("INVALID_REVIEW_RESOURCE_SCOPE")


class Authenticator(Protocol):
    async def authenticate(self, request: object) -> AuthenticatedPrincipal | None:
        """Verify transport credentials; no mechanism is selected by this protocol."""
        ...


class AuthorizationPolicy(Protocol):
    def allows(self, principal: AuthenticatedPrincipal, scope: ReviewResourceScope,
               operation: ReviewOperation) -> bool: ...


_ERRORS = {
    "AUTHENTICATION_REQUIRED": (401, "Authentication required.", False),
    "REVIEW_RESOURCE_NOT_FOUND": (404, "Review resource unavailable.", False),
    "REVIEW_OPERATION_FORBIDDEN": (403, "Review operation forbidden.", False),
    "REVIEW_STALE_REVISION": (409, "Review revision conflict.", False),
    "REVIEW_DECISION_CONFLICT": (409, "Review decision conflict.", False),
    "INVALID_REVIEW_DECISION": (422, "Invalid review decision.", False),
    "REVIEW_INTEGRITY_FAILURE": (500, "Review integrity failure.", False),
    "REVIEW_SERVICE_UNAVAILABLE": (503, "Review service unavailable.", True),
}


class ReviewBoundaryError(Exception):
    def __init__(self, code: str):
        self.code = code if code in _ERRORS else "REVIEW_SERVICE_UNAVAILABLE"
        self.status, self.message, self.retryable = _ERRORS[self.code]
        super().__init__(self.code)


def permitted(policy: AuthorizationPolicy | None, principal: AuthenticatedPrincipal,
              scope: ReviewResourceScope, operation: ReviewOperation) -> bool:
    if policy is None:
        return False
    try:
        return policy.allows(principal, scope, operation) is True
    except Exception:
        raise ReviewBoundaryError("REVIEW_SERVICE_UNAVAILABLE") from None
