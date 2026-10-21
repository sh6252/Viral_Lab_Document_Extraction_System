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
    allowed_document_hashes: frozenset[str]

    def __post_init__(self) -> None:
        url = urlsplit(self.origin)
        if (url.scheme != "https" or not url.hostname or url.path or url.query or url.fragment
                or url.username or url.password or url.port == 0
                or self.origin != f"https://{url.netloc}" or "*" in self.origin):
            raise ValueError("EXACT_PUBLIC_HTTPS_ORIGIN_REQUIRED")
        validate_password_hash(self.password_hash)
        if (not re.fullmatch(r"[a-z][a-z0-9_]{2,62}", self.database_name)
                or "synthetic" not in self.database_name):
            raise ValueError("EXPLICIT_SYNTHETIC_DATABASE_NAME_REQUIRED")
        if len(self.allowed_document_hashes) > 30 or any(not re.fullmatch(r"[0-9a-f]{64}", h) for h in self.allowed_document_hashes):
            raise ValueError("INVALID_SYNTHETIC_DOCUMENT_ALLOWLIST")

    @classmethod
    def from_env(cls) -> "CloudConfig":
        if os.environ.get("APP_DEPLOYMENT") != "cloud" or os.environ.get("DEMO_DATA_POLICY") != "synthetic-only":
            raise ValueError("EXPLICIT_CLOUD_SYNTHETIC_ONLY_CONFIGURATION_REQUIRED")
        return cls(os.environ.get("DEMO_PUBLIC_ORIGIN") or os.environ.get("RENDER_EXTERNAL_URL", ""), os.environ.get("DEMO_PASSWORD_HASH", ""),
                   os.environ.get("DEMO_DATABASE_NAME", ""),
                   frozenset(h.strip() for h in os.environ.get("DEMO_ALLOWED_DOCUMENT_SHA256", "").split(",") if h.strip()))
