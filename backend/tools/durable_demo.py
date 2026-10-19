"""Explicit loopback demo factory with PostgreSQL business persistence."""

from contextlib import asynccontextmanager
import os

from fastapi import FastAPI

from backend.persistence.business_repository import BusinessRepository
from backend.persistence.db import create_review_engine
from backend.services.durable_workflow import DurableWorkflow


def create_durable_demo_app() -> FastAPI:
    """No migration, provider request, .env loading or memory fallback at startup."""
    dsn = os.environ.get("REVIEW_DATABASE_DSN")
    if not dsn:
        raise RuntimeError("EXPLICIT_REVIEW_DATABASE_DSN_REQUIRED")
    engine = None
    try:
        engine = create_review_engine(dsn)
        repository = BusinessRepository(engine)
        repository.check_ready()
        # Reuse exactly the existing demo adapter configuration and policy gates.
        from backend.tools.local_demo import create_local_demo_app

        app = create_local_demo_app()
        app.state.demo_workflow = DurableWorkflow(app.state.config, app.state.extraction_adapter, repository)
    except Exception:
        if engine is not None:
            engine.dispose()
        raise RuntimeError("BUSINESS_DATABASE_STARTUP_FAILED") from None

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            yield
        finally:
            engine.dispose()

    app.router.lifespan_context = lifespan
    return app
