from dataclasses import replace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.core.config import AppConfig
from backend.main import create_app
from backend.services.extraction.errors import ProviderUnavailable
from backend.services.extraction.registry import build_extraction_adapter, registered_provider_names
from backend.services.extraction.provider_errors import safe_provider_diagnostics
from tests.test_extraction_api import FakeExtractionAdapter
from tests.test_openai_adapter import authorized_config, json_response, make_adapter


@pytest.mark.parametrize("provider", ["gemini", "openai", " OpenAI "])
def test_factory_selects_configured_provider_without_creating_sdk_client(provider):
    with patch("backend.services.extraction.openai_adapter.OpenAIExtractionAdapter._create_openai_client") as oa, patch(
        "backend.services.extraction.gemini_adapter.GeminiExtractionAdapter._create_google_client"
    ) as gm:
        adapter = build_extraction_adapter(AppConfig(default_extraction_adapter=provider))
    assert adapter.provider_name == provider.strip().lower()
    oa.assert_not_called()
    gm.assert_not_called()


@pytest.mark.parametrize("provider", ["local_ocr", "none", ""])
def test_unimplemented_local_path_is_explicitly_unavailable(provider):
    assert build_extraction_adapter(AppConfig(default_extraction_adapter=provider)) is None


def test_unknown_provider_fails_closed():
    with pytest.raises(ProviderUnavailable) as caught:
        build_extraction_adapter(AppConfig(default_extraction_adapter="unknown"))
    assert caught.value.retryable is False
    assert registered_provider_names() == ("gemini", "openai")


def test_openai_environment_configuration_and_fail_closed_defaults():
    with patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic", "OPENAI_MODEL": "test-model"}, clear=True):
        config = AppConfig.from_env()
    assert config.provider_api_key("openai") == "synthetic"
    assert config.provider_model("openai") == "test-model"
    assert config.external_ai_allowed("openai") is False
    assert config.provider_api_key("future") is None
    assert config.provider_model("future") is None


def test_injected_falsey_adapter_is_not_replaced():
    class FalseyAdapter(FakeExtractionAdapter):
        def __bool__(self):
            return False
    adapter = FalseyAdapter()
    app = create_app(config=AppConfig(default_extraction_adapter="unknown"), extraction_adapter=adapter)
    assert app.state.extraction_adapter is adapter


def test_startup_factory_and_http_contract_work_with_mock_openai():
    adapter, _ = make_adapter(json_response())
    config = replace(authorized_config(), default_extraction_adapter="openai")
    with patch.dict("backend.services.extraction.registry.ADAPTER_BUILDERS", {"openai": lambda _: adapter}):
        client = TestClient(create_app(config=config))
    response = client.post(
        "/api/v1/extractions", data={"allow_external_processing": "true"},
        files={"file": ("synthetic.pdf", b"%PDF-1.7 synthetic", "application/pdf")},
    )
    assert response.status_code == 200
    assert response.json()["provider"]["name"] == "openai"


def test_factory_passes_upload_limit_to_adapters():
    for provider in registered_provider_names():
        with patch(f"backend.services.extraction.registry.{('Gemini' if provider == 'gemini' else 'OpenAI')}ExtractionAdapter") as constructor:
            config = AppConfig(default_extraction_adapter=provider, max_upload_bytes=123)
            build_extraction_adapter(config)
        constructor.assert_called_once_with(config=config, max_document_bytes=123)


def test_provider_diagnostics_never_copy_arbitrary_status_or_error_text():
    error = RuntimeError("SECRET payload")
    error.status = "SECRET patient name"
    error.code = "SECRET token"
    assert "SECRET" not in str(safe_provider_diagnostics(error))


def test_nested_quota_error_code_is_recognized_without_copying_body():
    error = RuntimeError("SECRET payload")
    error.body = {"error": {"code": "insufficient_quota", "message": "SECRET"}}
    assert safe_provider_diagnostics(error)["provider_status"] == "insufficient_quota"
    assert "SECRET" not in str(safe_provider_diagnostics(error))
