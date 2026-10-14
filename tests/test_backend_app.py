import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.core.config import AppConfig
from backend.main import create_app


class BackendAppTests(unittest.TestCase):
    def test_create_app_imports_and_starts_without_external_services(self):
        app = create_app()

        self.assertEqual(app.title, "Virology Lab IDP")

    def test_health_endpoint_returns_healthy_response(self):
        client = TestClient(create_app())

        response = client.get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "healthy",
                "service": "Virology Lab IDP",
                "version": "0.1.0",
                "external_ai_enabled": False,
            },
        )

    def test_external_ai_is_disabled_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            config = AppConfig.from_env()

        self.assertFalse(config.ai_outbound_enabled)
        self.assertFalse(config.allow_external_phi)
        self.assertEqual(config.approved_ai_providers, ())
        self.assertFalse(config.external_ai_allowed("future-provider"))

    def test_external_ai_requires_explicit_provider_approval(self):
        with patch.dict(
            "os.environ",
            {
                "AI_OUTBOUND_ENABLED": "true",
                "APPROVED_AI_PROVIDERS": "approved-provider",
            },
            clear=True,
        ):
            config = AppConfig.from_env()

        self.assertTrue(config.external_ai_allowed("approved-provider"))
        self.assertFalse(config.external_ai_allowed("unknown-provider"))


if __name__ == "__main__":
    unittest.main()
