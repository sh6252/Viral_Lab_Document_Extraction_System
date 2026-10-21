"""Fail-closed seeder and file-only synthetic replay: no database or network."""

from dataclasses import asdict
from hashlib import sha256
import json
import socket

import pymupdf
import pytest
import sqlalchemy

from backend.services.business_review import (
    BusinessReviewError, FIELDS, SourceBinding, apply_business_review, snapshot_canonical,
)
from backend.tools import seed_synthetic_demo as tool
from backend.tools.review_export import run_operator
from tests.test_business_review import action
from tests.test_excel_export import read_workbook

DSN = "postgresql+psycopg://synthetic_operator:DO_NOT_PRINT@127.0.0.1:5433/viral_lab"


@pytest.fixture(autouse=True)
def forbid_database_and_network(monkeypatch):
    from backend.persistence import db
    from backend.persistence.business_repository import BusinessRepository
    from backend.services.durable_workflow import DurableWorkflow
    from backend.services.extraction.gemini_adapter import GeminiExtractionAdapter

    def forbidden(*args, **kwargs):
        pytest.fail("offline seeder tests must never connect, persist or use Gemini")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(sqlalchemy, "create_engine", forbidden)
    monkeypatch.setattr(db, "create_review_engine", forbidden)
    monkeypatch.setattr(GeminiExtractionAdapter, "extract", forbidden)
    monkeypatch.setattr(DurableWorkflow, "create", forbidden)
    for method in ("__init__", "create", "complete", "review_atomic", "check_ready"):
        monkeypatch.setattr(BusinessRepository, method, forbidden)


@pytest.mark.parametrize("dsn", [
    "", "not-a-dsn", "postgresql+psycopg://user:DO_NOT_PRINT@127.0.0.1:5433/review_test_viral_lab",
    DSN.replace("127.0.0.1", "remote.example"), DSN.replace("127.0.0.1", "localhost"),
    DSN.replace("5433", "5432"), DSN.replace(":5433", ""),
    DSN.replace("/viral_lab", "/other"), DSN.replace("/viral_lab", "/"),
    DSN.replace("postgresql+psycopg", "postgresql"), DSN.replace("postgresql+psycopg", "sqlite"),
    DSN.replace("synthetic_operator:DO_NOT_PRINT@", ""),
    DSN + "?host=remote.example", DSN + "?port=5432", DSN + "?dbname=review_test_viral_lab",
    DSN + "?service=external", DSN + "?options=-csearch_path%3Dother", DSN + " ",
    DSN.replace("5433", "not-a-port"), DSN.replace("viral_lab", "viral_lab_extra"),
])
def test_rejects_every_non_exact_database_target_without_echo(dsn):
    with pytest.raises(tool.SeedSafetyError, match="DATABASE_TARGET_REJECTED") as caught:
        tool.validate_target(dsn)
    assert "DO_NOT_PRINT" not in str(caught.value)


def test_exact_target_returns_only_safe_identifiers():
    assert tool.validate_target(DSN) == {"host": "127.0.0.1", "port": 5433, "database": "viral_lab"}


@pytest.mark.parametrize("confirmed,code", [
    (False, "EXPLICIT_WRITE_CONFIRMATION_REQUIRED"), (True, tool.PROVENANCE_BLOCKED),
])
def test_seed_refuses_before_any_database_or_workflow_call(confirmed, code):
    for _ in range(2):
        with pytest.raises(tool.SeedSafetyError, match=code):
            tool.request_seed(dsn=DSN, confirmed=confirmed)


def test_confirmation_cannot_override_wrong_target():
    with pytest.raises(tool.SeedSafetyError, match="DATABASE_TARGET_REJECTED"):
        tool.request_seed(dsn=DSN.replace("/viral_lab", "/review_test_viral_lab"), confirmed=True)


@pytest.mark.parametrize("value", [None, "", "  "])
def test_missing_environment_fails_closed(value):
    env = {} if value is None else {"REVIEW_DATABASE_DSN": value}
    with pytest.raises(tool.SeedSafetyError, match="EXPLICIT_DATABASE_CONFIGURATION_REQUIRED"):
        tool.configured_dsn(env)


def test_explicit_env_file_reads_only_literal_target_and_preserves_environment(tmp_path):
    path = tmp_path / "private.env"
    path.write_text('GEMINI_API_KEY=DO_NOT_PRINT\nREVIEW_DATABASE_DSN="' + DSN + '"\n', encoding="utf-8-sig")
    env = {"REVIEW_DATABASE_DSN": "unrelated", "UNCHANGED": "yes"}
    before = path.read_bytes()
    assert tool.configured_dsn(env, path) == DSN
    assert tool.configured_dsn(env) == "unrelated"
    assert path.read_bytes() == before and env == {"REVIEW_DATABASE_DSN": "unrelated", "UNCHANGED": "yes"}


@pytest.mark.parametrize("content", [
    "REVIEW_TEST_DSN=" + DSN, "REVIEW_DATABASE_DSN=x\nREVIEW_DATABASE_DSN=y",
    'REVIEW_DATABASE_DSN="unclosed', "REVIEW_DATABASE_DSN='", "REVIEW_DATABASE_DSN=",
])
def test_invalid_env_configuration_is_not_inferred_or_evaluated(tmp_path, content):
    path = tmp_path / "private.env"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(tool.SeedSafetyError):
        tool.validate_target(tool.configured_dsn({}, path))


def test_cli_preflight_and_confirm_report_blocker_without_credentials(monkeypatch, capsys):
    monkeypatch.setenv("REVIEW_DATABASE_DSN", DSN)
    for args in (["--preflight"], ["--confirm-write"]):
        assert tool.main(args) == 2
        printed = capsys.readouterr().out
        result = json.loads(printed)
        assert result["code"] == tool.PROVENANCE_BLOCKED
        assert result["database_seeded"] is False
        assert "DO_NOT_PRINT" not in printed and "synthetic_operator" not in printed


def test_unexpected_configuration_error_never_leaks_payload(monkeypatch, capsys):
    def broken(*args, **kwargs):
        raise ValueError("DO_NOT_PRINT " + DSN)

    monkeypatch.setattr(tool, "configured_dsn", broken)
    assert tool.main(["--preflight"]) == 2
    printed = capsys.readouterr().out
    assert "DO_NOT_PRINT" not in printed
    assert json.loads(printed)["code"] == "SYNTHETIC_DEMO_PREPARATION_FAILED"


def test_cli_requires_explicit_mode_and_no_combined_write_and_offline(tmp_path):
    for args in ([], ["--confirm-write", "--offline-output", str(tmp_path / "new")]):
        with pytest.raises(SystemExit):
            tool.main(args)
    assert not (tmp_path / "new").exists()


def test_pdf_visible_watermark_and_all_requested_values():
    fixture = tool.build_offline_fixture()
    with pymupdf.open(stream=fixture.pdf, filetype="pdf") as document:
        assert len(document) == 1
        assert document.metadata["title"] == tool.WATERMARK
        text = document[0].get_text()
        # MuPDF text extraction may return the RTL glyphs in visual order.
        assert tool.WATERMARK in text or tool.WATERMARK[::-1] in text
        assert "NOT GEMINI" in text
        for name, values in tool.fixture_values().items():
            for value in values:
                assert value in text or value[::-1] in text, name
    assert fixture.binding.source_sha256 == sha256(fixture.pdf).hexdigest()


def test_exact_canonical_fixture_and_preserved_mock_provenance():
    fixture = tool.build_offline_fixture()
    snapshot, canonical = snapshot_canonical(fixture.snapshot)
    assert snapshot == fixture.snapshot
    assert canonical.provider.mode == "mock" and canonical.provider.name == "offline_smoke_mock"
    assert canonical.provider.version == "offline-perfect"
    assert len(canonical.logical_forms) == 1
    fields = canonical.logical_forms[0].fields
    expected = {
        "first_name": ("שרה",), "last_name": ("כהן",), "israeli_id": ("012345678",),
        "central_numbers": ("803215", "803216"), "sample_receipt_date": ("18.08.2026",),
        "sender": ("לאומית",), "commitment_numbers": ("456001", "456002", "456003"),
    }
    assert tool.fixture_values() == expected
    for name, values in expected.items():
        field = getattr(fields, name)
        assert tuple(value.raw for value in field.values) == values
        assert field.review_status == "unreviewed"
    assert fixture.binding.data_origin == "historical_replay"
    assert fixture.binding.snapshot_kind == "preserved"
    assert fixture.binding.prompt_version is None
    assert fixture.binding.canonical_snapshot_sha256 == sha256(snapshot.encode("utf-8")).hexdigest()
    result = apply_business_review(snapshot, fixture.binding, tool.FORM_ID)
    assert result.record.revision == 0 and result.audit == ()
    assert result.record.unresolved_fields == FIELDS
    assert all(field.review_status == "unreviewed" for field in result.record.fields)


def test_no_synthetic_origin_enum_is_invented_and_pair_cannot_be_mixed():
    binding = asdict(tool.build_offline_fixture().binding)
    for changes in ({"data_origin": "mock"}, {"data_origin": "synthetic"}, {"data_origin": "live"}):
        with pytest.raises(BusinessReviewError, match="EXPLICIT_DATA_ORIGIN_REQUIRED"):
            SourceBinding(**(binding | changes))


@pytest.mark.parametrize("provider", [
    {"name": "gemini", "mode": "vision_ai", "version": "not-a-real-call"},
    {"name": "gemini", "mode": "mock", "version": "misleading-name"},
])
def test_unexpected_provider_provenance_never_creates_output(tmp_path, monkeypatch, provider):
    original = tool.OfflineSmokeExtractionAdapter.extract

    def wrong_provider(self, request, document):
        result = original(self, request, document)
        result.provider = type(result.provider).model_validate(provider)
        return result

    monkeypatch.setattr(tool.OfflineSmokeExtractionAdapter, "extract", wrong_provider)
    output = tmp_path / "new"
    with pytest.raises(tool.SeedSafetyError, match="MOCK_PROVENANCE_REQUIRED"):
        tool.write_offline_fixture(output)
    assert not output.exists()


def test_new_offline_package_hashes_empty_actions_and_refuses_repetition(tmp_path):
    directory = tmp_path / "new"
    summary = tool.write_offline_fixture(directory)
    assert summary["database_seeded"] is False and summary["forms"] == 1
    assert json.loads((directory / "actions.json").read_text()) == []
    manifest = json.loads((directory / "manifest.json").read_text())
    for name, digest in manifest["sha256"].items():
        assert sha256((directory / name).read_bytes()).hexdigest() == digest
    original = {path.name: path.read_bytes() for path in directory.iterdir()}
    with pytest.raises(FileExistsError):
        tool.write_offline_fixture(directory)
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == original


def test_offline_mode_never_reads_database_configuration(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("offline replay must not read DSNs")

    monkeypatch.setattr(tool, "configured_dsn", forbidden)
    directory = tmp_path / "new"
    assert tool.main(["--offline-output", str(directory)]) == 0
    assert json.loads(capsys.readouterr().out)["database_seeded"] is False
    assert tool.main(["--offline-output", str(tmp_path / "other"), "--env-file", ".env"]) == 2
    assert not (tmp_path / "other").exists()


def test_isolated_replay_export_never_fabricates_approvals_and_remains_reopenable(tmp_path):
    directory = tmp_path / "new"
    tool.write_offline_fixture(directory)
    summary = run_operator(snapshot_path=directory / "snapshot.json", source_path=directory / "source.json",
                           actions_path=directory / "actions.json", output_path=directory / "unreviewed.xlsx")
    assert summary["complete_records"] == summary["review_actions"] == 0
    bundle = json.loads((directory / "unreviewed.review.json").read_text(encoding="utf-8"))
    assert bundle["source_binding"]["data_origin"] == "historical_replay"
    assert bundle["results"][0]["record"]["revision"] == 0
    assert bundle["results"][0]["audit"] == []
    snapshot, _ = snapshot_canonical(bundle["canonical_snapshot_json"])
    reopened = apply_business_review(snapshot, SourceBinding(**bundle["source_binding"]), tool.FORM_ID)
    assert reopened.record.revision == 0 and reopened.audit == ()


def test_explicit_simulated_operator_actions_export_three_rows_seven_audits(tmp_path):
    """Only the test supplies human actions; the tool always writes an empty list."""
    directory = tmp_path / "new"
    tool.write_offline_fixture(directory)
    _, canonical = snapshot_canonical((directory / "snapshot.json").read_text(encoding="utf-8"))
    binding = SourceBinding(**json.loads((directory / "source.json").read_text(encoding="utf-8"))["binding"])
    commands = [action(canonical, binding, name=name, form=tool.FORM_ID, kind="approve_as_extracted",
                       revision=i, decision_id=f"explicit-test-action-{i}") for i, name in enumerate(FIELDS)]
    (directory / "operator-actions.json").write_text(json.dumps(
        [asdict(command) for command in commands], default=lambda value: value.isoformat()), encoding="utf-8")
    before = (directory / "snapshot.json").read_bytes()
    summary = run_operator(snapshot_path=directory / "snapshot.json", source_path=directory / "source.json",
                           actions_path=directory / "operator-actions.json", output_path=directory / "reviewed.xlsx")
    assert summary["complete_records"] == 1 and summary["review_actions"] == 7
    workbook = read_workbook((directory / "reviewed.xlsx").read_bytes())
    rows = workbook["Forms"][1]
    assert [row["commitment_numbers"] for row in rows] == ["456001", "456002", "456003"]
    assert all(row["israeli_id"] == "012345678" for row in rows)
    assert all(json.loads(row["central_numbers"]) == ["803215", "803216"] for row in rows)
    assert all(row["sample_receipt_date"] == "18.08.2026" for row in rows)
    assert len(workbook["ReviewAudit"][1]) == 7
    assert (directory / "snapshot.json").read_bytes() == before
