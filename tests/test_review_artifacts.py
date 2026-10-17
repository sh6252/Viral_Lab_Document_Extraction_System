"""Descriptor-only test backends; no file access, URLs, storage or streaming."""

from dataclasses import replace

import pytest

from backend.core.review_access import AuthenticatedPrincipal, ReviewResourceScope
from backend.services.review_artifacts import ArtifactBinding, ReviewArtifactService
from tests.test_ocr_candidates import IMAGE_HASH, candidate

PRINCIPAL = AuthenticatedPrincipal("synthetic", "reviewer")
SCOPE = ReviewResourceScope("session-1", "doc-1", "form-1")


class SyntheticResolver:
    def __init__(self, binding):
        self.binding = binding
        self.calls = 0

    def resolve(self, principal, scope, token):
        self.calls += 1
        return self.binding


def binding(**changes):
    values = dict(artifact_reference="opaque-image-1", document_id="doc-1", form_id="form-1",
                  page_number=1, image_sha256=IMAGE_HASH, visible_form_ids=("form-1",))
    values.update(changes)
    return ArtifactBinding(**values)


def test_default_source_is_unavailable_and_contains_no_locator():
    token = candidate()
    result = ReviewArtifactService().describe(PRINCIPAL, SCOPE, token, source_authorized=True)
    assert result.availability == "unavailable"
    assert result.artifact_reference is result.bbox is result.coordinate_system is None
    assert result.image_sha256 == IMAGE_HASH and result.page_number == 1


@pytest.mark.parametrize("allowed", [False, None, "yes", 1])
def test_source_capability_is_separate_and_default_is_closed(allowed):
    resolver = SyntheticResolver(binding())
    descriptor = ReviewArtifactService(resolver).describe(PRINCIPAL, SCOPE, candidate(), source_authorized=allowed)
    assert descriptor.availability == "unavailable"
    assert resolver.calls == 0


@pytest.mark.parametrize("changes", [
    {"artifact_reference": "C:/private/evaluation/artifacts/image.png"},
    {"artifact_reference": "../../private"}, {"artifact_reference": "https://example.invalid/image"},
    {"document_id": "other"}, {"form_id": "other"}, {"page_number": 2},
    {"image_sha256": "b" * 64}, {"visible_form_ids": ("form-1", "form-2")},
])
def test_unbound_shared_or_arbitrary_references_are_not_exposed(changes):
    resolver = SyntheticResolver(binding(**changes))
    descriptor = ReviewArtifactService(resolver).describe(PRINCIPAL, SCOPE, candidate(), source_authorized=True)
    assert descriptor.availability == "unavailable" and descriptor.artifact_reference is None


def test_explicit_single_form_test_backend_can_supply_descriptor_only():
    token = candidate(bbox=(1, 2, 3, 4), bbox_coordinate_system="synthetic-pixels")
    descriptor = ReviewArtifactService(SyntheticResolver(binding())).describe(
        PRINCIPAL, SCOPE, token, source_authorized=True,
    )
    assert descriptor.availability == "available" and descriptor.artifact_reference == "opaque-image-1"
    assert descriptor.bbox == token.bbox


def test_coordinate_locator_and_wrong_candidate_scope_fail_closed():
    token = candidate(bbox=(1, 2, 3, 4), bbox_coordinate_system="C:/private/path")
    service = ReviewArtifactService(SyntheticResolver(binding()))
    assert service.describe(PRINCIPAL, SCOPE, token, source_authorized=True).availability == "unavailable"
    assert service.describe(PRINCIPAL, SCOPE, replace(token, document_id="other"), source_authorized=True).availability == "unavailable"


def test_resolver_failure_is_explicit_unavailability_without_error_text():
    class BrokenResolver:
        def resolve(self, *args):
            raise RuntimeError("PRIVATE_FILESYSTEM_PATH")
    descriptor = ReviewArtifactService(BrokenResolver()).describe(PRINCIPAL, SCOPE, candidate(), source_authorized=True)
    assert descriptor.availability == "unavailable"
    assert "PRIVATE_FILESYSTEM_PATH" not in repr(descriptor)
