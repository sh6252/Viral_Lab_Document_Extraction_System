"""FastAPI application entry point."""

from fastapi import FastAPI

from backend.api.routes.extractions import router as extractions_router
from backend.api.routes.health import router as health_router
from backend.api.routes.reviews import router as reviews_router
from backend.core.config import AppConfig
from backend.core.review_access import Authenticator, AuthorizationPolicy
from backend.services.review_api import ReviewAPIService
from backend.services.extraction.base import ExtractionAdapter
from backend.services.extraction.registry import build_extraction_adapter


def create_app(
    config: AppConfig | None = None,
    extraction_adapter: ExtractionAdapter | None = None,
    *, review_api_service: ReviewAPIService | None = None,
    review_authenticator: Authenticator | None = None,
    review_authorization_policy: AuthorizationPolicy | None = None,
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
    app.state.review_api_service = review_api_service
    app.state.review_authenticator = review_authenticator
    app.state.review_authorization_policy = review_authorization_policy
    app.state.extraction_adapter = (
        extraction_adapter if extraction_adapter is not None else build_extraction_adapter(app_config)
    )
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(extractions_router, prefix="/api/v1")
    app.include_router(reviews_router, prefix="/api/v1")
    return app


app = create_app()
