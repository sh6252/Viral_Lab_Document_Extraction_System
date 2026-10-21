"""Bounded local-only export selection; no extraction or review data accepted."""
from pydantic import Field, StrictInt, StrictStr

from backend.api.demo_schemas import DemoInput


class ExportDocument(DemoInput):
    run_id: StrictStr = Field(min_length=1, max_length=256)
    review_token: StrictStr = Field(min_length=1, max_length=256)


class ExportForm(ExportDocument):
    form_id: StrictStr = Field(min_length=1, max_length=256)
    expected_revision: StrictInt = Field(ge=0)
    commitments: list[StrictStr | None] = Field(min_length=1, max_length=2000)


class BulkExportCommand(DemoInput):
    forms: list[ExportForm] = Field(default_factory=list, max_length=256)
    failures: list[ExportDocument] = Field(default_factory=list, max_length=30)
