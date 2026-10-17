"""Descriptor-only private evidence boundary; no files, URLs or streaming."""

import re
from dataclasses import dataclass
from typing import Protocol

from backend.core.review_access import AuthenticatedPrincipal, ReviewResourceScope
from backend.services.extraction.ocr_candidates import OCRCandidate


@dataclass(frozen=True, slots=True)
class ArtifactBinding:
    artifact_reference: str
    document_id: str
    form_id: str
    page_number: int
    image_sha256: str
    visible_form_ids: tuple[str, ...]


class PrivateEvidenceResolver(Protocol):
    def resolve(self, principal: AuthenticatedPrincipal, scope: ReviewResourceScope,
                candidate: OCRCandidate) -> ArtifactBinding | None: ...


@dataclass(frozen=True, slots=True)
class EvidenceDescriptor:
    artifact_reference: str | None
    page_number: int
    image_sha256: str
    bbox: tuple[float, float, float, float] | None
    coordinate_system: str | None
    availability: str


class ReviewArtifactService:
    def __init__(self, resolver: PrivateEvidenceResolver | None = None):
        self.resolver = resolver

    def describe(self, principal: AuthenticatedPrincipal, scope: ReviewResourceScope,
                 candidate: OCRCandidate, *, source_authorized: bool) -> EvidenceDescriptor:
        unavailable = EvidenceDescriptor(None, candidate.page_number, candidate.source_image_sha256,
                                         None, None, "unavailable")
        if source_authorized is not True or self.resolver is None:
            return unavailable
        if (candidate.document_id, candidate.form_id) != (scope.document_id, scope.form_id):
            return unavailable
        try:
            binding = self.resolver.resolve(principal, scope, candidate)
            if (not isinstance(binding, ArtifactBinding)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", binding.artifact_reference)
                    or (binding.document_id, binding.form_id, binding.page_number, binding.image_sha256)
                    != (scope.document_id, scope.form_id, candidate.page_number, candidate.source_image_sha256)
                    or binding.visible_form_ids != (scope.form_id,)):
                return unavailable
            system = candidate.bbox_coordinate_system
            if system is not None and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", system):
                return unavailable
            return EvidenceDescriptor(binding.artifact_reference, candidate.page_number,
                                      candidate.source_image_sha256, candidate.bbox, system, "available")
        except Exception:
            # An unavailable optional source is explicit, never a public path fallback.
            return unavailable
