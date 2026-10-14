"""Domain contracts for backend services."""

from backend.domain.models import (
    BoundingBox,
    Evidence,
    ExtractionFields,
    ExtractionPage,
    ExtractionRequest,
    ExtractionResult,
    FieldResult,
    FieldValue,
    LogicalFormResult,
    ProviderPolicySnapshot,
    ProviderMetadata,
)

__all__ = [
    "BoundingBox",
    "Evidence",
    "ExtractionFields",
    "ExtractionPage",
    "ExtractionRequest",
    "ExtractionResult",
    "FieldResult",
    "FieldValue",
    "LogicalFormResult",
    "ProviderPolicySnapshot",
    "ProviderMetadata",
]
