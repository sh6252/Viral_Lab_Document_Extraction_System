"""Extraction orchestration independent of FastAPI request objects."""

from __future__ import annotations

from dataclasses import dataclass

from backend.domain import ExtractionRequest, ExtractionResult
from backend.services.extraction.base import DocumentInput, ExtractionAdapter
from backend.services.extraction.schema_validation import validate_extraction_result_schema


@dataclass(frozen=True, slots=True)
class ExtractionOrchestrator:
    adapter: ExtractionAdapter

    def extract(self, *, request: ExtractionRequest, document: DocumentInput) -> ExtractionResult:
        result = self.adapter.extract(request, document)
        validate_extraction_result_schema(result)
        return result
