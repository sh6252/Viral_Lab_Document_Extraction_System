"""Read-only, capability-bound export using authoritative persisted aggregates."""
import secrets

from backend.api.bulk_export_schemas import BulkExportCommand
from backend.services.demo_workflow import DemoError, DemoWorkflow
from backend.services.excel_export import export_workbook


def bulk_export(service: DemoWorkflow, command: BulkExportCommand) -> bytes:
    if not command.forms and not command.failures:
        raise DemoError("EMPTY_EXPORT_SELECTION")
    documents = {item.run_id for item in [*command.forms, *command.failures]}
    if len(documents) > 30 or sum(len(item.commitments) for item in command.forms) > 2000:
        raise DemoError("EXPORT_SELECTION_LIMIT", 413)
    selected = set()
    forms = {}
    for item in command.forms:
        key = (item.run_id, item.form_id)
        if key in forms and (forms[key].expected_revision != item.expected_revision
                             or forms[key].review_token != item.review_token):
            raise DemoError("CONFLICTING_EXPORT_SELECTION", 409)
        forms[key] = item
        selected.update((item.run_id, item.form_id, value) for value in item.commitments)
    results, failures = [], []
    snapshot_bytes = 0
    with service._lock:
        for run_id in sorted(documents):
            run = service._get(run_id)
            bindings = [item for item in [*command.forms, *command.failures] if item.run_id == run_id]
            if any(not secrets.compare_digest(run.review_token, item.review_token) for item in bindings):
                raise DemoError("DEMO_RUN_BINDING_MISMATCH", 404)
            wanted = {key[1]: item for key, item in forms.items() if key[0] == run_id}
            if wanted:
                if run.status != "succeeded" or not wanted.keys() <= {r.record.form_id for r in run.results}:
                    raise DemoError("INVALID_EXPORT_SELECTION")
                for result in run.results:
                    if result.record.form_id in wanted and result.record.revision != wanted[result.record.form_id].expected_revision:
                        raise DemoError("STALE_EXPORT_REVISION", 409)
                snapshot_bytes += len((run.canonical_snapshot_json or "").encode("utf-8"))
                if snapshot_bytes > 32 * 1024 * 1024:
                    raise DemoError("EXPORT_SELECTION_LIMIT", 413)
                # Full extraction groups are validated before selected rows are rendered.
                results.extend(run.results)
            if any(item.run_id == run_id for item in command.failures):
                if run.status != "failed" or run.failure is None:
                    raise DemoError("INVALID_EXPORT_FAILURE_SELECTION")
                failures.append(run.failure)
    return export_workbook(results, failures, selected_rows=selected)
