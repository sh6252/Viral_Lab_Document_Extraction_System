"""Experimental Gemini generation schema; local contracts remain authoritative."""

from __future__ import annotations

import hashlib
import json

from pydantic.json_schema import JsonSchemaValue

from backend.services.extraction.canonical import ProviderPayload


GEMINI_RESPONSE_SCHEMA_VERSION = "gemini-provider-output-v1"


def _generation_subset(schema: JsonSchemaValue) -> JsonSchemaValue:
    """Compact schema nodes without rewriting property names or adding defaults.

    Defaults and titles are unnecessary for generation. minLength is enforced
    locally because it is absent from the SDK's documented JSON Schema subset.
    All required lists, types, nullable unions and closed objects are retained.
    """
    result: JsonSchemaValue = {}
    for key, value in schema.items():
        if key in {"default", "title", "minLength"}:
            continue
        if key in {"$defs", "properties"}:
            result[key] = {name: _generation_subset(node) for name, node in value.items()}
        elif key in {"items", "additionalProperties"} and isinstance(value, dict):
            result[key] = _generation_subset(value)
        elif key == "anyOf":
            result[key] = [_generation_subset(node) for node in value]
        else:
            result[key] = value
    return result


def build_gemini_response_schema() -> JsonSchemaValue:
    """Return a fresh schema derived from provider Pydantic contracts.

    Generation uses the prompt's logical_forms envelope. The existing parser
    retains its legacy single-fields representation and all local validation.
    Backend-owned IDs/provider metadata are never requested from the model.
    """
    schema = ProviderPayload.model_json_schema()
    forms = next(
        node
        for node in schema["properties"]["logical_forms"]["anyOf"]
        if node.get("type") == "array"
    )
    schema["properties"] = {"logical_forms": {**forms, "minItems": 1}}
    schema["required"] = ["logical_forms"]

    # Generation may emit candidate warnings, never human review decisions.
    review = schema["$defs"]["FieldResult"]["properties"]["review_status"]
    for alternative in review["anyOf"]:
        if "enum" in alternative:
            alternative["enum"] = [
                status for status in alternative["enum"]
                if status in {"unreviewed", "needs_review", "needs_clarification"}
            ]
    return _generation_subset(schema)


def gemini_response_schema_sha256() -> str:
    """SHA-256 of UTF-8 JSON with sorted keys, compact separators and no NaN.

    Version identifies the generation policy; the hash identifies the exact
    schema produced by the installed Pydantic contracts/version. Experiments
    must record both, plus their SDK/model/prompt/pipeline versions.
    """
    content = json.dumps(
        build_gemini_response_schema(), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()
