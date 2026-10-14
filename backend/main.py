"""FastAPI application entry point."""

from fastapi import FastAPI

from backend.api.routes.extractions import router as extractions_router
from backend.api.routes.health import router as health_router
from backend.core.config import AppConfig
from backend.services.extraction import ExtractionAdapter, GeminiExtractionAdapter


def _build_extraction_adapter(config: AppConfig) -> ExtractionAdapter | None:
    if config.default_extraction_adapter == "gemini":
        return GeminiExtractionAdapter(config=config)
    return None


def create_app(
    config: AppConfig | None = None,
    extraction_adapter: ExtractionAdapter | None = None,
) -> FastAPI:
    """Create the FastAPI application without external side effects."""

    app_config = config or AppConfig.from_env()
    app = FastAPI(
        title=app_config.app_name,
        version=app_config.app_version,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.state.config = app_config
    app.state.extraction_adapter = extraction_adapter or _build_extraction_adapter(app_config)
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(extractions_router, prefix="/api/v1")
    return app


app = create_app()
