from dataclasses import replace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.core.config import AppConfig
from backend.main import create_app
from backend.services.extraction.errors import ProviderUnavailable
from backend.services.extraction.gemini_adapter import GeminiExtractionAdapter
from backend.services.extraction.registry import build_extraction_adapter, registered_provider_names
from backend.services.extraction.provider_errors import safe_provider_diagnostics
from tests.test_extraction_api import FakeExtractionAdapter
from tests.test_gemini_adapter import _authorized_config, _FakeClient, _FakeResponse, _provider_payload


@pytest.mark.parametrize("provider", ["gemini", " Gemini "])
def test_factory_selects_configured_provider_without_creating_sdk_client(provider):
    with patch(
        "backend.services.extraction.gemini_adapter.GeminiExtractionAdapter._create_google_client"
    ) as gm:
        adapter = build_extraction_adapter(AppConfig(default_extraction_adapter=provider))
    assert adapter.provider_name == provider.strip().lower()
    gm.assert_not_called()


@pytest.mark.parametrize("provider", ["local_ocr", "none", ""])
def test_unimplemented_local_path_is_explicitly_unavailable(provider):
    assert build_extraction_adapter(AppConfig(default_extraction_adapter=provider)) is None


def test_unknown_provider_fails_closed():
    with pytest.raises(ProviderUnavailable) as caught:
        build_extraction_adapter(AppConfig(default_extraction_adapter="unknown"))
    assert caught.value.retryable is False
    with pytest.raises(ProviderUnavailable):
        create_app(config=AppConfig(default_extraction_adapter="unknown"))
    assert registered_provider_names() == ("gemini",)


def test_gemini_environment_configuration_and_fail_closed_defaults():
    with patch.dict("os.environ", {"GEMINI_API_KEY": "synthetic", "GEMINI_MODEL": "test-model"}, clear=True):
        config = AppConfig.from_env()
    assert config.provider_api_key("gemini") == "synthetic"
    assert config.provider_model("gemini") == "test-model"
    assert config.external_ai_allowed("gemini") is False
    assert config.provider_api_key("future") is None
    assert config.provider_model("future") is None


def test_injected_falsey_adapter_is_not_replaced():
    class FalseyAdapter(FakeExtractionAdapter):
        def __bool__(self):
            return False
    adapter = FalseyAdapter()
    app = create_app(config=AppConfig(default_extraction_adapter="unknown"), extraction_adapter=adapter)
    assert app.state.extraction_adapter is adapter


def test_startup_factory_and_http_contract_work_with_mock_gemini():
    adapter = GeminiExtractionAdapter(
        config=_authorized_config(), client_factory=lambda *_: _FakeClient(_FakeResponse(_provider_payload()))
    )
    config = replace(_authorized_config(), default_extraction_adapter="gemini")
    with patch.dict("backend.services.extraction.registry.ADAPTER_BUILDERS", {"gemini": lambda _: adapter}):
        client = TestClient(create_app(config=config))
    response = client.post(
        "/api/v1/extractions", data={"allow_external_processing": "true"},
        files={"file": ("synthetic.pdf", b"%PDF-1.7 synthetic", "application/pdf")},
    )
    assert response.status_code == 200
    assert response.json()["provider"]["name"] == "gemini"


def test_factory_passes_upload_limit_to_adapters():
    for provider in registered_provider_names():
        with patch("backend.services.extraction.registry.GeminiExtractionAdapter") as constructor:
            config = AppConfig(default_extraction_adapter=provider, max_upload_bytes=123)
            build_extraction_adapter(config)
        constructor.assert_called_once_with(config=config, max_document_bytes=123)


def test_registry_can_construct_a_future_provider_without_business_logic_changes():
    adapter = FakeExtractionAdapter()
    config = AppConfig(default_extraction_adapter="future_provider")
    with patch.dict("backend.services.extraction.registry.ADAPTER_BUILDERS", {"future_provider": lambda _: adapter}):
        assert build_extraction_adapter(config) is adapter
        assert create_app(config=config).state.extraction_adapter is adapter


def test_startup_with_only_gemini_environment_does_not_initialize_sdk():
    with patch.dict("os.environ", {
        "DEFAULT_EXTRACTION_ADAPTER": "gemini", "GEMINI_MODEL": "synthetic-model",
    }, clear=True), patch.object(GeminiExtractionAdapter, "_create_google_client") as client:
        app = create_app()
    assert app.state.extraction_adapter.provider_name == "gemini"
    assert app.state.config.gemini_api_key is None
    assert app.state.config.ai_outbound_enabled is False
    client.assert_not_called()


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
