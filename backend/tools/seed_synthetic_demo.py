"""Fail-closed synthetic seeder preflight and isolated, file-only replay fixture.

The current durable workflow cannot preserve non-live source bindings. Therefore
this tool intentionally has NO database connection/write path, even with consent.
Do not remove that guard or relabel mock data as live to make seeding work.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from hashlib import sha256
from html import escape
import json
import os
from pathlib import Path

from sqlalchemy.engine import make_url

from backend.domain import ExtractionRequest, ProviderPolicySnapshot
from backend.services.business_review import (
    FIELDS, SourceBinding, apply_business_review, snapshot_canonical,
)
from backend.services.extraction.base import DocumentInput
from backend.services.extraction.orchestrator import ExtractionOrchestrator
from backend.tools.extraction_smoke import OfflineSmokeExtractionAdapter

FIXTURE_VERSION = "synthetic-business-demo-v1"
WATERMARK = "נתונים סינתטיים להדגמה בלבד"
FORM_ID = "synthetic-business-demo-form-v1"
PROVENANCE_BLOCKED = "SYNTHETIC_DURABLE_PROVENANCE_UNSUPPORTED"


class SeedSafetyError(ValueError):
    """Only constant safe codes, never DSNs or input exception messages."""


@dataclass(frozen=True)
class OfflineFixture:
    pdf: bytes
    snapshot: str
    binding: SourceBinding


def fixture_values() -> dict[str, tuple[str, ...]]:
    """Fresh values: no mutation of the existing smoke/benchmark fixtures."""
    return {
        "first_name": ("שרה",),
        "last_name": ("כהן",),
        "israeli_id": ("012345678",),
        "central_numbers": ("803215", "803216"),
        "sample_receipt_date": ("18.08.2026",),
        "sender": ("לאומית",),
        "commitment_numbers": ("456001", "456002", "456003"),
    }


def configured_dsn(environ: Mapping[str, str], env_file: Path | None = None) -> str:
    """Read only the explicitly selected source; no env mutation or interpolation."""
    if env_file is None:
        value = environ.get("REVIEW_DATABASE_DSN", "")
    else:
        matches = []
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            key, separator, candidate = line.strip().partition("=")
            if separator and key.strip() == "REVIEW_DATABASE_DSN":
                candidate = candidate.strip()
                if candidate[:1] in {"'", '"'}:
                    if len(candidate) < 2 or candidate[-1] != candidate[0]:
                        raise SeedSafetyError("INVALID_DATABASE_CONFIGURATION")
                    candidate = candidate[1:-1]
                matches.append(candidate)
        if len(matches) != 1:
            raise SeedSafetyError("EXPLICIT_DATABASE_CONFIGURATION_REQUIRED")
        value = matches[0]
    if not value or not value.strip():
        raise SeedSafetyError("EXPLICIT_DATABASE_CONFIGURATION_REQUIRED")
    return value


def validate_target(dsn: str) -> dict[str, str | int]:
    """Reject URL/query overrides, sockets, test DBs and non-explicit targets."""
    try:
        url = make_url(dsn)
        allowed = (
            url.drivername == "postgresql+psycopg"
            and url.host == "127.0.0.1"
            and url.port == 5433
            and url.database == "viral_lab"
            and bool(url.username)
            and not url.query
            and dsn == dsn.strip()
        )
    except Exception:
        raise SeedSafetyError("DATABASE_TARGET_REJECTED") from None
    if not allowed:
        raise SeedSafetyError("DATABASE_TARGET_REJECTED")
    return {"host": "127.0.0.1", "port": 5433, "database": "viral_lab"}


def request_seed(*, dsn: str, confirmed: bool) -> None:
    """Never reach DurableWorkflow.create: it would persist a live binding.

    DurableWorkflow.create hardcodes live/live; BusinessRepository._load and
    list_records require that pair. SourceBinding supports preserved replay,
    but the durable repository does not. Injecting a mock cannot fix that.
    """
    if not confirmed:
        raise SeedSafetyError("EXPLICIT_WRITE_CONFIRMATION_REQUIRED")
    validate_target(dsn)
    raise SeedSafetyError(PROVENANCE_BLOCKED)


def _pdf_bytes() -> bytes:
    import pymupdf

    labels = ("שם פרטי", "שם משפחה", "תעודת זהות", "מספרים מרכזיים",
              "תאריך קבלת דגימה", "גורם שולח", "מספרי התחייבות")
    rows = []
    for (name, values), label in zip(fixture_values().items(), labels, strict=True):
        direction = "rtl" if name in {"first_name", "last_name", "sender"} else "ltr"
        text = "<br>".join(escape(value) for value in values)
        rows.append(f'<tr><td dir="{direction}">{text}</td><td dir="rtl">{label}</td></tr>')
    html = (f'<h1 dir="rtl">{WATERMARK}</h1>'
            '<p>SYNTHETIC / OFFLINE MOCK — NOT GEMINI — NO REAL PATIENT</p>'
            '<table>' + "".join(rows) + '</table>'
            f'<h2 dir="rtl">{WATERMARK}</h2>')
    css = ("body {font-family: serif; font-size: 17pt;} table {width:100%;} "
           "td {padding:8pt;} h1,h2 {color:#a00000; text-align:center; font-size:23pt;} "
           "p {font-size:10pt; text-align:center;}")
    with pymupdf.open() as document:
        page = document.new_page(width=595, height=842)
        spare, scale = page.insert_htmlbox(pymupdf.Rect(30, 30, 565, 812), html,
                                          css=css, scale_low=1)
        if spare < 0 or scale != 1:
            raise SeedSafetyError("SYNTHETIC_PDF_LAYOUT_FAILED")
        document.set_metadata({"title": WATERMARK, "subject": "SYNTHETIC / OFFLINE MOCK"})
        document.subset_fonts()
        return document.tobytes(garbage=4, deflate=True, no_new_id=True)


def build_offline_fixture() -> OfflineFixture:
    """Preserved local replay, not a live provider request or a database seed."""
    pdf = _pdf_bytes()
    source_hash = sha256(pdf).hexdigest()
    truth = {"logical_forms": [{"form_id": FORM_ID, "fields": {
        name: {"value_status": "present", "values": [{"raw": value} for value in values]}
        for name, values in fixture_values().items()
    }}]}
    request = ExtractionRequest(
        request_id=f"{FIXTURE_VERSION}-extraction", logical_form_id=FORM_ID,
        document_id=FIXTURE_VERSION, pages=[], fields=list(FIELDS), locale="he-IL",
        pipeline_version=FIXTURE_VERSION,
        provider_policy=ProviderPolicySnapshot(data_boundary="local_only", allow_external_phi=False),
    )
    adapter = OfflineSmokeExtractionAdapter(truth)
    result = ExtractionOrchestrator(adapter).extract(
        request=request, document=DocumentInput(pdf, "application/pdf"))
    snapshot, canonical = snapshot_canonical(result)
    if canonical.provider.mode != "mock" or canonical.provider.name != "offline_smoke_mock":
        raise SeedSafetyError("MOCK_PROVENANCE_REQUIRED")
    binding = SourceBinding(
        document_id=FIXTURE_VERSION, source_sha256=source_hash,
        extraction_id=canonical.extraction_id,
        canonical_snapshot_sha256=sha256(snapshot.encode("utf-8")).hexdigest(),
        data_origin="historical_replay", snapshot_kind="preserved",
        prompt_version=None, pipeline_version=FIXTURE_VERSION,
    )
    review = apply_business_review(snapshot, binding, FORM_ID)
    if review.audit or review.record.revision or review.record.review_status != "unresolved":
        raise SeedSafetyError("UNREVIEWED_FIXTURE_REQUIRED")
    return OfflineFixture(pdf, snapshot, binding)


def write_offline_fixture(directory: Path) -> dict[str, str | int | bool]:
    """New directory only; a partial write is retained and prevents reuse."""
    fixture = build_offline_fixture()
    source = json.dumps({"source_path": "synthetic.pdf", "binding": asdict(fixture.binding)},
                        ensure_ascii=False, indent=2).encode("utf-8")
    files = {"synthetic.pdf": fixture.pdf, "snapshot.json": fixture.snapshot.encode("utf-8"),
             "source.json": source, "actions.json": b"[]\n"}
    manifest = {
        "fixture_version": FIXTURE_VERSION, "synthetic": True, "database_seeded": False,
        "provider": "offline_smoke_mock", "provider_mode": "mock", "human_review_actions": 0,
        "data_origin": "historical_replay", "snapshot_kind": "preserved",
        "sha256": {name: sha256(content).hexdigest() for name, content in files.items()},
    }
    files["manifest.json"] = json.dumps(manifest, indent=2).encode("utf-8")
    # mkdir is the exclusive one-time reservation; no exist_ok, overwrite or cleanup.
    directory.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        with (directory / name).open("xb") as output:
            output.write(content)
    return {"status": "offline_fixture_prepared", "database_seeded": False,
            "forms": 1, "review_actions": 0, "source_sha256": fixture.binding.source_sha256,
            "canonical_snapshot_sha256": fixture.binding.canonical_snapshot_sha256}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true", help="No DB connection; reports the blocker.")
    mode.add_argument("--confirm-write", action="store_true", help="Still blocked by provenance policy.")
    mode.add_argument("--offline-output", type=Path, help="New file-only replay directory; never a DB seed.")
    parser.add_argument("--env-file", type=Path, help="Explicit DSN source; never loads other settings.")
    args = parser.parse_args(argv)
    try:
        if args.offline_output is not None:
            if args.env_file is not None:
                raise SeedSafetyError("OFFLINE_MODE_DOES_NOT_USE_DATABASE_CONFIGURATION")
            print(json.dumps(write_offline_fixture(args.offline_output), sort_keys=True))
            return 0
        dsn = configured_dsn(os.environ, args.env_file)
        target = validate_target(dsn)
        if args.confirm_write:
            request_seed(dsn=dsn, confirmed=True)
        print(json.dumps({"status": "blocked", "code": PROVENANCE_BLOCKED,
                          "target": target, "database_connected": False, "database_seeded": False}))
        return 2
    except SeedSafetyError as error:
        print(json.dumps({"status": "blocked", "code": str(error), "database_seeded": False}))
        return 2
    except Exception:
        # Never expose configuration, Pydantic input echoes, paths or SQL messages.
        print(json.dumps({"status": "blocked", "code": "SYNTHETIC_DEMO_PREPARATION_FAILED",
                          "database_seeded": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
