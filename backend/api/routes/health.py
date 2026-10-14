"""Health-check routes."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from backend.core.config import AppConfig

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["healthy"]
    service: str
    version: str
    external_ai_enabled: bool


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Return process health without touching external dependencies."""

    config: AppConfig = request.app.state.config
    return HealthResponse(
        status="healthy",
        service=config.app_name,
        version=config.app_version,
        external_ai_enabled=config.ai_outbound_enabled,
    )
