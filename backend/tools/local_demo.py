"""Explicit local-demo ASGI entry point. Never expose beyond loopback."""

from backend.core.config import AppConfig
from backend.main import create_app
from backend.services.demo_workflow import DemoWorkflow
from backend.services.extraction.gemini_adapter import GeminiExtractionAdapter


def _one_attempt_client(api_key: str, timeout_seconds: int):
    from google import genai
    from google.genai import types

    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_seconds * 1000,
        retry_options=types.HttpRetryOptions(attempts=1)))


def create_local_demo_app():
    config = AppConfig.from_env()
    # No policy override, model/key change, nested retry or hidden replay fallback.
    adapter = (GeminiExtractionAdapter(config=config, max_document_bytes=config.max_upload_bytes,
               max_retries=0, client_factory=_one_attempt_client)
               if config.default_extraction_adapter.strip().lower() == "gemini" else None)
    return create_app(config=config, extraction_adapter=adapter, demo_workflow=DemoWorkflow(config, adapter))


app = create_local_demo_app()
