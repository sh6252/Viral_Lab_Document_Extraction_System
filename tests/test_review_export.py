"""Local operator file flow with synthetic source/snapshots; no external calls."""

from dataclasses import asdict
import hashlib
import json

import pytest

from backend.services.business_review import FIELDS
from backend.tools.review_export import main, run_operator
from tests.test_business_review import action, binding, canonical
from tests.test_excel_export import failure, read_workbook


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, default=lambda v: v.isoformat()), encoding="utf-8")


def inputs(tmp_path, *, two_forms=False, live=False):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n% synthetic only, not OCR input\n")
    value = canonical(two_forms=two_forms)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    bound = binding(value, source_sha256=digest, data_origin="live" if live else "historical_replay",
                    snapshot_kind="live" if live else "preserved")
    snapshot, metadata, actions, output = (tmp_path/n for n in ("snapshot.json", "source.json", "actions.json", "output.xlsx"))
    save(snapshot, value.model_dump(mode="json"))
    save(metadata, {"source_path": source.name, "binding": asdict(bound)})
    commands = [action(value, bound, name=name, kind="approve_as_extracted", revision=i, decision_id=f"d-{i}")
                for i, name in enumerate(FIELDS)]
    save(actions, [asdict(a) for a in commands])
    return dict(snapshot_path=snapshot, source_path=metadata, actions_path=actions, output_path=output)


@pytest.mark.parametrize("live", [False, True])
def test_operator_creates_reopenable_workbook_bundle_and_hash_without_network(tmp_path, monkeypatch, live):
    import socket
    from backend.services.extraction.orchestrator import ExtractionOrchestrator
    from backend.services.extraction.gemini_adapter import GeminiExtractionAdapter

    def unexpected(*args, **kwargs):
        pytest.fail("local review/export must never use provider, OCR or network")

    monkeypatch.setattr(socket, "create_connection", unexpected)
    monkeypatch.setattr(ExtractionOrchestrator, "extract", unexpected)
    monkeypatch.setattr(GeminiExtractionAdapter, "extract", unexpected)
    args = inputs(tmp_path, two_forms=True, live=live)
    before = {key: path.read_bytes() for key, path in args.items() if key != "output_path"}
    summary = run_operator(**args)
    data = args["output_path"].read_bytes()
    assert summary["workbook_sha256"] == hashlib.sha256(data).hexdigest()
    assert summary["forms"] == 2 and summary["complete_records"] == summary["unresolved_records"] == 1
    book = read_workbook(data)
    assert len(book["Forms"][1]) == 2 and len(book["ReviewAudit"][1]) == 7
    bundle = json.loads(args["output_path"].with_suffix(".review.json").read_text(encoding="utf-8"))
    assert bundle["source_binding"]["data_origin"] == ("live" if live else "historical_replay")
    assert bundle["workbook_sha256"] == summary["workbook_sha256"]
    assert len(bundle["results"]) == 2
    assert all(path.read_bytes() == before[key] for key, path in args.items() if key != "output_path")


@pytest.mark.parametrize("changes", [{"data_origin": "live"}, {"snapshot_kind": "live"},
    {"data_origin": None}, {"canonical_snapshot_sha256": "c"*64}, {"source_sha256": "b"*64}])
def test_origin_and_source_snapshot_binding_fail_before_any_output(tmp_path, changes):
    args = inputs(tmp_path)
    metadata = json.loads(args["source_path"].read_text(encoding="utf-8"))
    metadata["binding"].update(changes)
    save(args["source_path"], metadata)
    with pytest.raises(ValueError):
        run_operator(**args)
    assert not args["output_path"].exists() and not args["output_path"].with_suffix(".review.json").exists()


def test_missing_explicit_origin_not_inferred_from_path(tmp_path):
    args = inputs(tmp_path)
    metadata = json.loads(args["source_path"].read_text(encoding="utf-8"))
    del metadata["binding"]["data_origin"]
    save(args["source_path"], metadata)
    with pytest.raises(ValueError):
        run_operator(**args)


def test_unknown_form_cross_field_original_and_failed_batch_no_partial_files(tmp_path):
    args = inputs(tmp_path)
    commands = json.loads(args["actions_path"].read_text(encoding="utf-8"))
    commands[-1]["form_id"] = "foreign-form"
    save(args["actions_path"], commands)
    with pytest.raises(ValueError):
        run_operator(**args)
    assert not args["output_path"].exists()


def test_optional_failures_remain_separate(tmp_path):
    args = inputs(tmp_path)
    failures = tmp_path / "failures.json"
    save(failures, [asdict(failure())])
    summary = run_operator(**args, failures_path=failures)
    book = read_workbook(args["output_path"].read_bytes())
    assert summary["forms"] == len(book["Forms"][1]) == 1
    assert summary["processing_failures"] == len(book["ProcessingFailures"][1]) == 1


def test_no_sensitive_stdout_on_success_or_validation_error(tmp_path, capsys):
    args = inputs(tmp_path)
    cli = ["--snapshot", str(args["snapshot_path"]), "--source", str(args["source_path"]),
           "--actions", str(args["actions_path"]), "--output", str(args["output_path"])]
    assert main(cli) == 0
    printed = capsys.readouterr().out
    assert json.loads(printed)["status"] == "exported"
    for value in ("טקסט בדיקה", "000123", "Synthetic operator", "document-synthetic", str(tmp_path)):
        assert value not in printed
    args["actions_path"].write_text('{"PRIVATE_INPUT":', encoding="utf-8")
    # Existing output is also a safe failure, never an overwrite.
    assert main(cli) == 1
    assert json.loads(capsys.readouterr().out) == {"error": {"code": "REVIEW_EXPORT_FAILED"}}


def test_detailed_validation_errors_never_echo_private_payload(tmp_path, capsys):
    args = inputs(tmp_path)
    save(args["actions_path"], [{"operator_declaration": "PRIVATE_OPERATOR", "secret": "PRIVATE_VALUE"}])
    assert main(["--snapshot", str(args["snapshot_path"]), "--source", str(args["source_path"]),
                 "--actions", str(args["actions_path"]), "--output", str(args["output_path"])]) == 1
    assert "PRIVATE_" not in capsys.readouterr().out


def test_outputs_are_new_exclusive_and_existing_bundle_not_overwritten(tmp_path):
    args = inputs(tmp_path)
    bundle = args["output_path"].with_suffix(".review.json")
    bundle.write_bytes(b"original")
    with pytest.raises(ValueError, match="OUTPUT_MUST_BE_NEW"):
        run_operator(**args)
    assert bundle.read_bytes() == b"original" and not args["output_path"].exists()


def test_second_output_io_failure_rolls_back_only_own_first_file(tmp_path):
    args = inputs(tmp_path)
    with pytest.raises(ValueError, match="PRIVATE_OUTPUT_WRITE_FAILED"):
        run_operator(**args, bundle_path=tmp_path/"missing-parent"/"bundle.json")
    assert not args["output_path"].exists() and args["snapshot_path"].exists()


@pytest.mark.parametrize("payload", ['[{"decision_id":"a","decision_id":"b"}]', '[NaN]', '[] trailing'])
def test_noncanonical_json_inputs_fail_safely(tmp_path, payload):
    args = inputs(tmp_path)
    args["actions_path"].write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        run_operator(**args)
    assert not args["output_path"].exists()
