"""Provider-independent extraction adapter interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.domain import ExtractionRequest, ExtractionResult


@dataclass(frozen=True, slots=True)
class DocumentInput:
    """In-memory document payload for extraction adapters."""

    content: bytes
    mime_type: str
    filename: str | None = None


class ExtractionAdapter(Protocol):
    """Common interface implemented by extraction providers."""

    provider_name: str

    def extract(self, request: ExtractionRequest, document: DocumentInput) -> ExtractionResult:
        """Extract canonical fields from a single logical-form document."""
