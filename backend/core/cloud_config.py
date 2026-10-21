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

    @classmethod
    def from_env(cls) -> "CloudConfig":
        if os.environ.get("APP_DEPLOYMENT") != "cloud" or os.environ.get("DEMO_DATA_POLICY") != "synthetic-only":
            raise ValueError("EXPLICIT_CLOUD_SYNTHETIC_ONLY_CONFIGURATION_REQUIRED")
        return cls(os.environ.get("DEMO_PUBLIC_ORIGIN") or os.environ.get("RENDER_EXTERNAL_URL", ""), os.environ.get("DEMO_PASSWORD_HASH", ""),
                   os.environ.get("DEMO_DATABASE_NAME", ""))
