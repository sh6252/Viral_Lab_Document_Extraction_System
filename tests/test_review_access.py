"""Synthetic principal/policy tests; no production authentication mechanism."""

from dataclasses import FrozenInstanceError

import pytest

from backend.core.review_access import AuthenticatedPrincipal, ReviewResourceScope, permitted


def test_principal_scope_and_permissions_are_immutable():
    principal = AuthenticatedPrincipal("verified", "subject", ("read_review",))
    scope = ReviewResourceScope("session", "document", "form")
    for target, field in ((principal, "subject"), (scope, "document_id")):
        with pytest.raises(FrozenInstanceError):
            setattr(target, field, "forged")
    assert not permitted(None, principal, scope, "read_review")


@pytest.mark.parametrize("changes", [{"issuer": ""}, {"subject": " "}, {"roles": ["admin"]}])
def test_invalid_verified_principal_is_rejected(changes):
    args = dict(issuer="verified", subject="subject")
    args.update(changes)
    with pytest.raises(ValueError):
        AuthenticatedPrincipal(**args)


@pytest.mark.parametrize("result", [False, None, 1, "yes"])
def test_only_explicit_true_policy_result_is_a_grant(result):
    class SyntheticPolicy:
        def allows(self, principal, scope, operation):
            return result
    assert not permitted(SyntheticPolicy(), AuthenticatedPrincipal("issuer", "subject"),
                         ReviewResourceScope("s", "d", "f"), "read_review")
