"""Controlled extraction smoke-test runner.

The default path is fully offline and uses a deterministic mock adapter. Live
Provider execution requires explicit CLI authorization and the existing provider
policy checks; this module does not weaken or bypass the adapter policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from jsonschema import Draft202012Validator

from backend.core.config import AppConfig
from backend.domain import (
    ExtractionFields,
    ExtractionRequest,
    ExtractionResult,
    FieldResult,
    FieldValue,
    LogicalFormResult,
    ProviderMetadata,
    ProviderPolicySnapshot,
)
from backend.services.extraction.base import DocumentInput, ExtractionAdapter
from backend.services.extraction.errors import (
    ExtractionAdapterError,
    ProviderNotAuthorized,
    ProviderUnavailable,
)
from backend.services.extraction.orchestrator import ExtractionOrchestrator
from backend.services.extraction.prompts import EXTRACTION_PROMPT_VERSION
from backend.services.extraction.registry import build_extraction_adapter, registered_provider_names
from backend.services.extraction.schema_validation import validate_extraction_result_schema
from backend.services.extraction.upload_validation import detect_supported_mime_type
from evaluation.scoring import REQUIRED_FIELDS, score_extraction

ROOT = Path(__file__).resolve().parents[2]
SMOKE_FIXTURE_DIR = ROOT / "evaluation" / "fixtures" / "dev" / "smoke"
SYNTHETIC_PDF_PATH = SMOKE_FIXTURE_DIR / "synthetic_hebrew_commitment.pdf"
SYNTHETIC_GROUND_TRUTH_PATH = (
    SMOKE_FIXTURE_DIR / "synthetic_hebrew_commitment.ground_truth.json"
)
GROUND_TRUTH_SCHEMA_PATH = ROOT / "evaluation" / "schemas" / "ground_truth.schema.json"

SYNTHETIC_VALUES = {
    "first_name": "\u05e0\u05d5\u05e2\u05d4",
    "last_name": "\u05db\u05d4\u05df",
    "israeli_id": "001234567",
    "central_numbers": ["00012345", "00012346"],
    "sample_receipt_date": "2026-10-13",
    "sender": "\u05de\u05e8\u05db\u05d6 \u05e8\u05e4\u05d5\u05d0\u05d9 \u05d3\u05de\u05d5",
    "commitment_numbers": ["HT-00077", "HT-00078"],
}


@dataclass(frozen=True, slots=True)
class SmokeRunOptions:
    document_path: Path = SYNTHETIC_PDF_PATH
    ground_truth_path: Path = SYNTHETIC_GROUND_TRUTH_PATH
    provider: str = "offline_mock"
    live_gemini: bool = False
    authorize_external_processing: bool = False
    mock_scenario: str = "perfect"
    redact: bool = False


class OfflineSmokeExtractionAdapter:
    provider_name = "offline_smoke_mock"

    def __init__(self, ground_truth: dict[str, Any], *, scenario: str = "perfect") -> None:
        self._ground_truth = ground_truth
        self._scenario = scenario
        self.calls = 0

    def extract(self, request: ExtractionRequest, document: DocumentInput) -> ExtractionResult:
        self.calls += 1
        logical_forms = []
        for truth_form in self._ground_truth.get("logical_forms", []):
            fields = _truth_fields_to_extraction_fields(
                truth_form.get("fields", {}),
                scenario=self._scenario,
            )
            logical_forms.append(
                LogicalFormResult(
                    form_id=str(truth_form["form_id"]),
                    fields=fields,
                )
            )

        return ExtractionResult(
            extraction_id=request.request_id,
            provider=ProviderMetadata(
                name=self.provider_name,
                mode="mock",
                version=f"offline-{self._scenario}",
            ),
            logical_forms=logical_forms,
        )


def create_synthetic_fixture(
    *,
    pdf_path: Path = SYNTHETIC_PDF_PATH,
    ground_truth_path: Path = SYNTHETIC_GROUND_TRUTH_PATH,
) -> tuple[Path, Path]:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_bytes = build_synthetic_pdf_bytes()
    pdf_path.write_bytes(pdf_bytes)

    ground_truth = build_synthetic_ground_truth(
        filename=pdf_path.name,
        sha256=hashlib.sha256(pdf_bytes).hexdigest(),
    )
    ground_truth_path.write_text(
        json.dumps(ground_truth, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return pdf_path, ground_truth_path


def build_synthetic_pdf_bytes() -> bytes:
    lines = [
        "Central Virology Laboratory - Synthetic commitment form",
        "\u05d8\u05d5\u05e4\u05e1 \u05d4\u05ea\u05d7\u05d9\u05d9\u05d1\u05d5\u05ea \u05e1\u05d9\u05e0\u05ea\u05d8\u05d9",
        f"\u05e9\u05dd \u05e4\u05e8\u05d8\u05d9: {SYNTHETIC_VALUES['first_name']}",
        f"\u05e9\u05dd \u05de\u05e9\u05e4\u05d7\u05d4: {SYNTHETIC_VALUES['last_name']}",
        f"\u05ea\u05e2\u05d5\u05d3\u05ea \u05d6\u05d4\u05d5\u05ea: {SYNTHETIC_VALUES['israeli_id']}",
        "\u05de\u05e1\u05e4\u05e8\u05d9 \u05de\u05e2\u05d1\u05d3\u05d4: "
        + ", ".join(SYNTHETIC_VALUES["central_numbers"]),
        f"\u05d4\u05ea\u05e7\u05d1\u05dc: {SYNTHETIC_VALUES['sample_receipt_date']}",
        f"\u05d2\u05d5\u05e8\u05dd \u05e9\u05d5\u05dc\u05d7: {SYNTHETIC_VALUES['sender']}",
        "\u05de\u05e1\u05e4\u05e8\u05d9 \u05d4\u05ea\u05d7\u05d9\u05d9\u05d1\u05d5\u05ea: "
        + ", ".join(SYNTHETIC_VALUES["commitment_numbers"]),
        "Synthetic data only - no real patient record.",
    ]
    stream = _pdf_text_stream(lines)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    return _assemble_pdf(objects)


def build_synthetic_ground_truth(*, filename: str, sha256: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "fixture_id": "synthetic-hebrew-commitment-smoke-001",
        "fixture_set": "dev",
        "source_document": {
            "filename": filename,
            "sha256": sha256,
            "page_count": 1,
        },
        "logical_forms": [
            {
                "form_id": "smoke-form-001",
                "page_indices": [0],
                "fields": {
                    "first_name": _truth_present(SYNTHETIC_VALUES["first_name"]),
                    "last_name": _truth_present(SYNTHETIC_VALUES["last_name"]),
                    "israeli_id": _truth_present(SYNTHETIC_VALUES["israeli_id"]),
                    "central_numbers": _truth_present_many(
                        SYNTHETIC_VALUES["central_numbers"]
                    ),
                    "sample_receipt_date": _truth_present(
                        SYNTHETIC_VALUES["sample_receipt_date"]
                    ),
                    "sender": _truth_present(SYNTHETIC_VALUES["sender"]),
                    "commitment_numbers": _truth_present_many(
                        SYNTHETIC_VALUES["commitment_numbers"]
                    ),
                },
                "notes": "Synthetic Hebrew smoke-test fixture; no real patient data.",
            }
        ],
    }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_ground_truth(ground_truth: dict[str, Any]) -> None:
    schema = load_json(GROUND_TRUTH_SCHEMA_PATH)
    Draft202012Validator(schema).validate(ground_truth)


def run_smoke_test(options: SmokeRunOptions) -> dict[str, Any]:
    started = time.perf_counter()
    provider_name = _selected_provider(options)
    ground_truth = load_json(options.ground_truth_path)
    validate_ground_truth(ground_truth)

    document_bytes = options.document_path.read_bytes()
    document = DocumentInput(
        content=document_bytes,
        mime_type=detect_supported_mime_type(document_bytes),
        filename=options.document_path.name,
    )
    request = _build_request(
        options=options,
        ground_truth=ground_truth,
    )
    errors: list[dict[str, Any]] = []
    extraction_result: ExtractionResult | None = None
    schema_validation = {"valid": False, "error": None}
    scoring: dict[str, Any] | None = None

    try:
        adapter = _build_adapter(options, ground_truth)
        extraction_result = ExtractionOrchestrator(adapter=adapter).extract(
            request=request,
            document=document,
        )
        validate_extraction_result_schema(extraction_result)
        schema_validation = {"valid": True, "error": None}
        scoring = score_extraction(
            ground_truth,
            extraction_result.model_dump(mode="json", exclude_none=True),
        )
        status = "succeeded"
    except ExtractionAdapterError as exc:
        status = "failed"
        error = {
            "code": exc.code,
            "message": exc.safe_message,
            "retryable": exc.retryable,
        }
        diagnostics = exc.safe_diagnostics()
        if diagnostics:
            error["diagnostics"] = diagnostics
        errors.append(error)

    duration_seconds = round(time.perf_counter() - started, 6)
    return _build_report(
        document_path=options.document_path,
        status=status,
        duration_seconds=duration_seconds,
        ground_truth=ground_truth,
        extraction_result=extraction_result,
        schema_validation=schema_validation,
        scoring=scoring,
        errors=errors,
        redact=options.redact,
        provider_name=provider_name,
        used_live_gemini_alias=options.live_gemini,
        pipeline_version=request.pipeline_version,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.create_synthetic_fixture:
        pdf_path, truth_path = create_synthetic_fixture()
        print(json.dumps({"pdf": str(pdf_path), "ground_truth": str(truth_path)}, indent=2))
        return 0

    options = SmokeRunOptions(
        document_path=args.document,
        ground_truth_path=args.ground_truth,
        provider=args.provider,
        live_gemini=args.live_gemini,
        authorize_external_processing=args.authorize_external_processing,
        mock_scenario=args.mock_scenario,
        redact=args.redact,
    )
    report = run_smoke_test(options)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    return 0 if report["extraction_status"] == "succeeded" else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a controlled extraction smoke test.")
    parser.add_argument("--document", type=Path, default=SYNTHETIC_PDF_PATH)
    parser.add_argument("--ground-truth", type=Path, default=SYNTHETIC_GROUND_TRUTH_PATH)
    parser.add_argument("--mock-scenario", choices=["perfect", "needs_review"], default="perfect")
    parser.add_argument(
        "--provider", choices=["offline_mock", *registered_provider_names()], default="offline_mock"
    )
    parser.add_argument("--live-gemini", action="store_true")
    parser.add_argument("--authorize-external-processing", action="store_true")
    parser.add_argument("--redact", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--create-synthetic-fixture", action="store_true")
    return parser


def _build_adapter(
    options: SmokeRunOptions,
    ground_truth: dict[str, Any],
) -> ExtractionAdapter:
    if options.live_gemini and options.provider not in {"offline_mock", "gemini"}:
        raise ProviderNotAuthorized("Conflicting smoke-test provider options.")
    provider_name = _selected_provider(options)
    if provider_name == "offline_mock":
        return OfflineSmokeExtractionAdapter(ground_truth, scenario=options.mock_scenario)
    if not options.authorize_external_processing:
        raise ProviderNotAuthorized(
            "Live provider smoke test requires explicit request-level authorization."
        )
    config = replace(AppConfig.from_env(), default_extraction_adapter=provider_name)
    adapter = build_extraction_adapter(config)
    if adapter is None:
        raise ProviderUnavailable("No live extraction adapter is configured for this provider.")
    return adapter


def _build_request(
    *,
    options: SmokeRunOptions,
    ground_truth: dict[str, Any],
) -> ExtractionRequest:
    return ExtractionRequest(
        request_id=f"smoke-{uuid4()}",
        logical_form_id=ground_truth["logical_forms"][0]["form_id"],
        document_id=ground_truth["fixture_id"],
        pages=[],
        fields=list(REQUIRED_FIELDS),
        locale="he-IL",
        pipeline_version=AppConfig.from_env().extraction_pipeline_version,
        provider_policy=ProviderPolicySnapshot(
            data_boundary="external_phi" if options.authorize_external_processing else "local_only",
            allow_external_phi=options.authorize_external_processing,
        ),
    )


def _selected_provider(options: SmokeRunOptions) -> str:
    if options.live_gemini:
        return "gemini"
    return options.provider


def _truth_fields_to_extraction_fields(
    truth_fields: dict[str, Any],
    *,
    scenario: str,
) -> ExtractionFields:
    fields: dict[str, FieldResult] = {}
    for field_name in REQUIRED_FIELDS:
        truth_field = truth_fields[field_name]
        values = [FieldValue(**value) for value in truth_field.get("values", [])]
        fields[field_name] = FieldResult(
            value_status="present" if values else "missing",
            values=values,
            review_status="unreviewed" if values else "needs_review",
        )

    if scenario == "needs_review":
        fields["sample_receipt_date"] = FieldResult(
            value_status="missing",
            values=[],
            review_status="needs_review",
        )
        fields["sender"] = FieldResult(
            value_status="ambiguous",
            values=[],
            review_status="needs_review",
            notes="Offline mock scenario marks sender ambiguous.",
        )
    return ExtractionFields(**fields)


def _build_report(
    *,
    document_path: Path,
    status: str,
    duration_seconds: float,
    ground_truth: dict[str, Any],
    extraction_result: ExtractionResult | None,
    schema_validation: dict[str, Any],
    scoring: dict[str, Any] | None,
    errors: list[dict[str, Any]],
    redact: bool,
    provider_name: str,
    used_live_gemini_alias: bool,
    pipeline_version: str,
) -> dict[str, Any]:
    actual_payload = (
        extraction_result.model_dump(mode="json", exclude_none=True) if extraction_result else {}
    )
    provider = actual_payload.get(
        "provider",
        _fallback_provider_metadata(provider_name),
    )
    return {
        "document_name": document_path.name,
        "extraction_status": status,
        "provider": provider,
        "model": provider.get("version"),
        "pipeline_version": pipeline_version,
        "prompt_version": EXTRACTION_PROMPT_VERSION if provider_name != "offline_mock" else None,
        "duration_seconds": duration_seconds,
        "required_fields": list(REQUIRED_FIELDS),
        "schema_validation": schema_validation,
        "field_results": _field_report(ground_truth, actual_payload, redact=redact),
        "scoring": scoring,
        "errors": errors,
        "warnings": _report_warnings(
            provider_name=provider_name,
            redact=redact,
            used_live_gemini_alias=used_live_gemini_alias,
        ),
    }


def _fallback_provider_metadata(provider_name: str) -> dict[str, str]:
    if provider_name == "offline_mock":
        return {"name": provider_name, "mode": "mock"}
    metadata = {"name": provider_name, "mode": "vision_ai"}
    model = AppConfig.from_env().provider_model(provider_name)
    if model:
        metadata["version"] = model
    return metadata


def _field_report(
    ground_truth: dict[str, Any],
    extraction_result: dict[str, Any],
    *,
    redact: bool,
) -> dict[str, Any]:
    truth_form = ground_truth["logical_forms"][0]
    predicted_forms = {
        form["form_id"]: form for form in extraction_result.get("logical_forms", [])
    }
    predicted_form = predicted_forms.get(truth_form["form_id"], {"fields": {}})
    report = {}
    for field_name in REQUIRED_FIELDS:
        truth_field = truth_form["fields"][field_name]
        predicted_field = predicted_form.get("fields", {}).get(field_name, {})
        expected = _field_values(truth_field)
        actual = _field_values(predicted_field)
        expected_set = set(expected)
        actual_set = set(actual)
        report[field_name] = {
            "expected_values": _maybe_redact(expected, redact=redact),
            "actual_values": _maybe_redact(actual, redact=redact),
            "value_status": predicted_field.get("value_status", "missing"),
            "review_status": predicted_field.get("review_status"),
            "exact_match": expected_set == actual_set,
            "missing_values": _maybe_redact(sorted(expected_set - actual_set), redact=redact),
            "extra_values": _maybe_redact(sorted(actual_set - expected_set), redact=redact),
            "incorrect_values": _maybe_redact(sorted(actual_set ^ expected_set), redact=redact),
        }
    return report


def _field_values(field: dict[str, Any]) -> list[str]:
    if field.get("value_status") != "present":
        return []
    return [value.get("normalized", value.get("raw", "")) for value in field.get("values", [])]


def _maybe_redact(values: list[str], *, redact: bool) -> list[str]:
    if not redact:
        return values
    return ["[REDACTED]" for _ in values]


def _report_warnings(
    *,
    provider_name: str,
    redact: bool,
    used_live_gemini_alias: bool,
) -> list[str]:
    warnings = [
        "Smoke workflow is not a production accuracy claim.",
        "Authentication and access control are not implemented yet.",
    ]
    if provider_name != "offline_mock":
        warnings.append("Live provider mode may transmit the uploaded document to an external provider.")
    if used_live_gemini_alias:
        warnings.append("--live-gemini is deprecated; use --provider gemini for new smoke runs.")
    if redact:
        warnings.append("Report values are redacted.")
    return warnings


def _truth_present(value: str) -> dict[str, Any]:
    return {
        "annotation_status": "verified",
        "value_status": "present",
        "values": [{"raw": value}],
        "evidence": [{"page_index": 0, "source_kind": "manual_review"}],
    }


def _truth_present_many(values: list[str]) -> dict[str, Any]:
    return {
        "annotation_status": "verified",
        "value_status": "present",
        "values": [{"raw": value} for value in values],
        "evidence": [{"page_index": 0, "source_kind": "manual_review"}],
    }


def _pdf_text_stream(lines: list[str]) -> bytes:
    commands = ["BT", "/F1 14 Tf", "72 780 Td", "18 TL"]
    for index, line in enumerate(lines):
        if index:
            commands.append("T*")
        commands.append(f"{_pdf_hex_string(line)} Tj")
    commands.append("ET")
    return "\n".join(commands).encode("ascii")


def _pdf_hex_string(text: str) -> str:
    return "<FEFF" + text.encode("utf-16-be").hex().upper() + ">"


def _assemble_pdf(objects: list[bytes]) -> bytes:
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")

    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


if __name__ == "__main__":
    raise SystemExit(main())
