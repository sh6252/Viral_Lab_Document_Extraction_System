"""Existing upload/review/export behavior backed by PostgreSQL, without a TTL."""

from backend.api.demo_schemas import DemoReviewCommand
from backend.api.records_schemas import RecordsQuery
from backend.core.config import AppConfig
from backend.persistence.business_repository import BusinessRepository
from backend.services.demo_workflow import DemoRun, DemoWorkflow
from backend.services.extraction.base import ExtractionAdapter


class DurableWorkflow(DemoWorkflow):
    def __init__(self, config: AppConfig, adapter: ExtractionAdapter | None, repository: BusinessRepository):
        super().__init__(config, adapter)
        self.repository = repository

    def _store_new(self, run: DemoRun) -> None:
        # Commit the private input first. A failed insert never invokes the provider.
        self.repository.create(run)

    def _store_completed(self, run: DemoRun) -> None:
        self.repository.complete(run)

    def _get(self, run_id: str) -> DemoRun:
        return self.repository.get(run_id)

    def list_records(self, query: RecordsQuery) -> dict:
        return self.repository.list_records(query)

    def review(self, run_id: str, form_id: str, command: DemoReviewCommand) -> dict:
        run = self.repository.review_atomic(run_id, lambda current: self._reviewed_run(current, form_id, command))
        return self._project(run, form_id)
