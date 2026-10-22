"""Explicit cloud settings, separate from unchanged loopback-only launchers."""
from dataclasses import dataclass
import os
import re
from urllib.parse import urlsplit

from backend.core.demo_auth import validate_password_hash


@dataclass(frozen=True)
class CloudConfig:
    origin: str
    password_hash: str
    database_name: str
    gemini_timeout_seconds: int = 120
    demo_max_documents: int = 30
    demo_max_source_bytes: int = 32 * 1024 * 1024

    def __post_init__(self) -> None:
        if type(self.gemini_timeout_seconds) is not int or not 30 <= self.gemini_timeout_seconds <= 120:
            raise ValueError("GEMINI_TIMEOUT_SECONDS_MUST_BE_INTEGER_30_TO_120")
        if type(self.demo_max_documents) is not int or not 1 <= self.demo_max_documents <= 200:
            raise ValueError("DEMO_MAX_DOCUMENTS_MUST_BE_INTEGER_1_TO_200")
        if type(self.demo_max_source_bytes) is not int or not 1 <= self.demo_max_source_bytes <= 128 * 1024 * 1024:
            raise ValueError("DEMO_MAX_SOURCE_BYTES_MUST_BE_INTEGER_1_TO_134217728")
        url = urlsplit(self.origin)
        if (url.scheme != "https" or not url.hostname or url.path or url.query or url.fragment
                or url.username or url.password or url.port == 0
                or self.origin != f"https://{url.netloc}" or "*" in self.origin):
            raise ValueError("EXACT_PUBLIC_HTTPS_ORIGIN_REQUIRED")
        validate_password_hash(self.password_hash)
        if (not re.fullmatch(r"[a-z][a-z0-9_]{2,62}", self.database_name)
                or "synthetic" not in self.database_name):
            raise ValueError("EXPLICIT_SYNTHETIC_DATABASE_NAME_REQUIRED")

    @classmethod
    def from_env(cls) -> "CloudConfig":
        if os.environ.get("APP_DEPLOYMENT") != "cloud" or os.environ.get("DEMO_DATA_POLICY") != "synthetic-only":
            raise ValueError("EXPLICIT_CLOUD_SYNTHETIC_ONLY_CONFIGURATION_REQUIRED")
        try:
            timeout = int(os.environ.get("GEMINI_TIMEOUT_SECONDS", "120"))
        except ValueError:
            raise ValueError("GEMINI_TIMEOUT_SECONDS_MUST_BE_INTEGER_30_TO_120") from None
        try:
            max_documents = int(os.environ.get("DEMO_MAX_DOCUMENTS", "30"))
        except ValueError:
            raise ValueError("DEMO_MAX_DOCUMENTS_MUST_BE_INTEGER_1_TO_200") from None
        try:
            max_source_bytes = int(os.environ.get("DEMO_MAX_SOURCE_BYTES", str(32 * 1024 * 1024)))
        except ValueError:
            raise ValueError("DEMO_MAX_SOURCE_BYTES_MUST_BE_INTEGER_1_TO_134217728") from None
        return cls(os.environ.get("DEMO_PUBLIC_ORIGIN") or os.environ.get("RENDER_EXTERNAL_URL", ""), os.environ.get("DEMO_PASSWORD_HASH", ""),
                   os.environ.get("DEMO_DATABASE_NAME", ""), timeout, max_documents, max_source_bytes)
