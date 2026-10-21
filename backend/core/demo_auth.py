"""Small invited-judge boundary: opaque, revocable, process-local sessions.

One worker only. Restarts revoke sessions and reset the bounded rate window.
No passwords, session IDs or document content are logged or stored in the bundle.
"""
from collections import deque
from dataclasses import dataclass
import hashlib
import hmac
import secrets
from threading import Lock, RLock
import time
from typing import Callable

COOKIE = "__Host-viral-demo"


def hash_password(password: str) -> str:
    if not 16 <= len(password) <= 256:
        raise ValueError("DEMO_PASSWORD_LENGTH_16_TO_256_REQUIRED")
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=5).hex()
    return f"scrypt${salt}${digest}"


def validate_password_hash(value: str) -> None:
    try:
        kind, salt, digest = value.split("$")
        assert kind == "scrypt" and len(salt) == 32 and len(digest) == 128
        bytes.fromhex(salt)
        bytes.fromhex(digest)
    except (ValueError, AssertionError):
        raise ValueError("VALID_DEMO_PASSWORD_HASH_REQUIRED") from None


@dataclass
class Session:
    csrf: str
    expires_at: float
    requests: deque[float]


class DemoAuth:
    def __init__(self, password_hash: str, *, clock: Callable[[], float] = time.monotonic,
                 ttl_seconds: int = 3600, max_sessions: int = 32):
        validate_password_hash(password_hash)
        self.password_hash = password_hash
        self.clock = clock
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.sessions: dict[str, Session] = {}
        self.attempts: deque[float] = deque()
        self.lock = RLock()
        self.verify_lock = Lock()

    def permit_login_attempt(self) -> bool:
        # Global window: independent of spoofed X-Forwarded-For / rotating IPs.
        with self.lock:
            now = self.clock()
            while self.attempts and self.attempts[0] <= now - 300:
                self.attempts.popleft()
            if len(self.attempts) >= 20:
                return False
            self.attempts.append(now)
            return True

    def login(self, password: str) -> str | None:
        _, salt, expected = self.password_hash.split("$")
        # Serialize password work: a login burst cannot allocate 20 scrypt buffers.
        with self.verify_lock:
            actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=5).hex()
        if not hmac.compare_digest(actual, expected):
            return None
        with self.lock:
            self.sessions = {k: v for k, v in self.sessions.items() if v.expires_at > self.clock()}
            if len(self.sessions) >= self.max_sessions:
                return None
            token = secrets.token_urlsafe(32)
            self.sessions[self._key(token)] = Session(secrets.token_urlsafe(32), self.clock() + self.ttl_seconds, deque())
            return token

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def get(self, token: str) -> Session | None:
        with self.lock:
            value = self.sessions.get(self._key(token))
            if value and value.expires_at > self.clock():
                return value
            self.sessions.pop(self._key(token), None)
            return None

    def permit_request(self, session: Session) -> bool:
        with self.lock:
            now = self.clock()
            while session.requests and session.requests[0] <= now - 60:
                session.requests.popleft()
            if len(session.requests) >= 120:
                return False
            session.requests.append(now)
            return True

    def logout(self, token: str) -> None:
        with self.lock:
            self.sessions.pop(self._key(token), None)
