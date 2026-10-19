"""Versioned exact JSON storage with mandatory domain replay validation."""

from hashlib import sha256
import json
from typing import TypeVar

from pydantic import TypeAdapter

from backend.services.business_review import (
    BusinessReviewAuditEntry, BusinessReviewResult, deterministic_json, validate_review_result,
)
from backend.services.excel_export import ProcessingFailure

CODEC_VERSION = "business-storage-v1"
Stored = TypeVar("Stored", BusinessReviewResult, BusinessReviewAuditEntry, ProcessingFailure)


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def encode(value: BusinessReviewResult | BusinessReviewAuditEntry | ProcessingFailure) -> str:
    if isinstance(value, BusinessReviewResult):
        validate_review_result(value)
    return deterministic_json({"version": CODEC_VERSION,
        "payload": TypeAdapter(type(value)).dump_python(value, mode="json", warnings="error")})


def decode(value: str, expected_type: type[Stored]) -> Stored:
    data = json.loads(value)
    if data.get("version") != CODEC_VERSION:
        raise ValueError("UNKNOWN_BUSINESS_STORAGE_VERSION")
    result = TypeAdapter(expected_type).validate_json(deterministic_json(data["payload"]), strict=True)
    if encode(result) != value:
        raise ValueError("INVALID_BUSINESS_STORAGE_PAYLOAD")
    return result
