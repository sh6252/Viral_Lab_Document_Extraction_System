"""Explicit single-origin invited-judge ASGI factory. Never imports local launchers."""
from contextlib import asynccontextmanager
import hmac
import json
import os
from pathlib import Path
from threading import BoundedSemaphore
from typing import Callable

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.staticfiles import StaticFiles
from urllib.parse import urlsplit

from backend.api.routes.demo import router as local_business_router
from backend.core.cloud_config import CloudConfig
from backend.core.config import AppConfig
from backend.core import cloud_diagnostics as diagnostic
from backend.core.demo_auth import COOKIE, DemoAuth
from backend.persistence.business_repository import BusinessRepository
from backend.persistence.cloud_database import check_business_schema, create_cloud_engine
from backend.services.business_review import BusinessReviewError
from backend.services.demo_workflow import DemoError, DemoRun
from backend.services.durable_workflow import DurableWorkflow
from backend.services.excel_export import ExcelExportError
from backend.services.extraction.errors import ExtractionAdapterError
from backend.services.extraction.base import ExtractionAdapter
from backend.services.extraction.gemini_adapter import GeminiExtractionAdapter


def error(code: str, status: int) -> JSONResponse:
    retry_after = "1" if code == "DEMO_BUSY" else "60" if code == "DEMO_RATE_LIMIT" else "300"
    trace = diagnostic.CURRENT.get()
    return JSONResponse({"error": {"code": code, "message": "Demo request could not be completed.",
                                    "retryable": status in {429, 503, 504}},
                         **({"diagnostic": trace.summary()} if trace else {})}, status_code=status,
                        headers={"Cache-Control": "no-store", **({"Retry-After": retry_after} if status == 429 else {})})


class CloudBusinessRoute(APIRoute):
    """Reuse domain handlers, not LocalDemoRoute or its loopback trust model."""
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request: Request):
            try:
                response = await handler(request)
                if request.method == "POST" and request.url.path == "/api/v1/demo-runs" and response.status_code == 201:
                    value = json.loads(response.body)
                    if value.get("status") == "failed":
                        code = value["failure"]["code"]
                        status = diagnostic.failure_status(code)
                        value["error"] = {"code": code, "message": "Extraction failed; no fallback was used.",
                                          "retryable": value["failure"]["retryable"]}
                        if trace := diagnostic.CURRENT.get():
                            if trace.code is None:
                                diagnostic.failure(code, status)
                            trace.http_status = status
                            value["diagnostic"] = trace.summary()
                        return JSONResponse(value, status_code=status)
                return response
            except DemoError as exc:
                diagnostic.failure(exc.code, exc.status)
                return error(exc.code, exc.status)
            except RequestValidationError:
                return error("INVALID_DEMO_INPUT", 422)
            except BusinessReviewError:
                return error("INVALID_DEMO_REVIEW", 422)
            except ExtractionAdapterError as exc:
                return error(exc.code, 413 if exc.code == "DOCUMENT_TOO_LARGE" else 415 if exc.code == "UNSUPPORTED_DOCUMENT_TYPE" else 400)
            except ExcelExportError:
                return error("DEMO_EXPORT_FAILED", 422)
            except Exception:
                diagnostic.failure("CLOUD_BACKEND_UNEXPECTED", 503)
                return error("CLOUD_BACKEND_UNEXPECTED", 503)
        return guarded


class CloudWorkflow(DurableWorkflow):
    def __init__(self, config: AppConfig, adapter: ExtractionAdapter | None, repository: BusinessRepository):
        super().__init__(config, adapter, repository)
        self.max_snapshot_bytes = 64 * 1024

    def _store_new(self, run: DemoRun) -> None:
        if trace := diagnostic.CURRENT.get():
            trace.run_id = run.run_id
        diagnostic.stage("database_create")
        try:
            super()._store_new(run)
        except Exception as exc:
            code = exc.code if isinstance(exc, DemoError) and exc.code != "BUSINESS_STORAGE_UNAVAILABLE" else "CLOUD_DATABASE_CREATE_FAILED"
            status = exc.status if isinstance(exc, DemoError) and code == exc.code else 503
            diagnostic.failure(code, status)
            raise DemoError(code, status) from None
        diagnostic.stage("provider_policy")

    def _store_completed(self, run: DemoRun) -> None:
        trace = diagnostic.CURRENT.get()
        if run.failure and trace and trace.code is None:
            diagnostic.failure(run.failure.failure_code, diagnostic.failure_status(run.failure.failure_code))
        diagnostic.stage("database_complete")
        try:
            super()._store_completed(run)
        except Exception:
            diagnostic.failure("CLOUD_DATABASE_COMPLETE_FAILED", 503)
            raise DemoError("CLOUD_DATABASE_COMPLETE_FAILED", 503) from None
        diagnostic.stage("projection")


class BodyTooLarge(Exception):
    pass


class PublicBoundary:
    """Authenticate before consuming multipart input; bound streamed bodies too."""
    def __init__(self, app, *, auth: DemoAuth, config: CloudConfig, readiness: Callable[[], None], upload_limit: int):
        self.app, self.auth, self.config, self.readiness = app, auth, config, readiness
        self.upload_limit = upload_limit
        self.upload_slot = BoundedSemaphore(1)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/api/v1/demo-runs" or scope.get("method") != "POST":
            await self._handle(scope, receive, send)
            return
        trace = diagnostic.UploadTrace()
        token = diagnostic.CURRENT.set(trace)
        trace.log("cloud_upload_started")
        try:
            await self._handle(scope, receive, send)
        finally:
            trace.log("cloud_upload_finished")
            diagnostic.CURRENT.reset(token)

    async def _handle(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = StarletteRequest(scope)
        path, method = request.url.path, request.method
        is_api = path.startswith("/api/")

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                if trace := diagnostic.CURRENT.get():
                    trace.http_status = message["status"]
                    headers.append((b"x-request-id", trace.request_id.encode("ascii")))
                if is_api:
                    headers.append((b"cache-control", b"no-store"))
                headers.extend([(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"),
                                (b"x-frame-options", b"DENY"), (b"strict-transport-security", b"max-age=31536000"),
                                (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; frame-src 'self' blob:; object-src 'none'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")])
                message = {**message, "headers": headers}
            await send(message)

        async def deny(code: str, status: int):
            diagnostic.failure(code, status)
            await error(code, status)(scope, receive, secure_send)

        diagnostic.stage("authentication")
        origin = request.headers.get("origin")
        if is_api and origin is not None and origin != self.config.origin:
            await deny("ORIGIN_DENIED", 403)
            return
        # Let only strict CORS middleware answer preflights, never a business handler.
        if method == "OPTIONS":
            await self.app(scope, receive, secure_send)
            return
        public = path in {"/api/v1/health", "/api/v1/ready", "/api/v1/auth/login"}
        if is_api and not public:
            if request.headers.get("x-demo-request") != "1":
                await deny("AUTHENTICATION_REQUIRED", 401)
                return
            session = self.auth.get(request.cookies.get(COOKIE, ""))
            if session is None:
                await deny("AUTHENTICATION_REQUIRED", 401)
                return
            if not self.auth.permit_request(session):
                await deny("DEMO_RATE_LIMIT", 429)
                return
            if method not in {"GET", "HEAD"} and (origin != self.config.origin
                    or not hmac.compare_digest(request.headers.get("x-demo-csrf", "").encode(), session.csrf.encode())):
                await deny("CSRF_DENIED", 403)
                return
        if path == "/api/v1/auth/login":
            if origin != self.config.origin or request.headers.get("x-demo-request") != "1":
                await deny("ORIGIN_DENIED", 403)
                return
            if not self.auth.permit_login_attempt():
                await deny("LOGIN_RATE_LIMIT", 429)
                return
        if is_api and method not in {"GET", "HEAD"} and not path.startswith("/api/v1/auth/"):
            diagnostic.stage("database_readiness")
            try:
                await run_in_threadpool(self.readiness)
            except Exception:
                await deny("CLOUD_DATABASE_NOT_READY", 503)
                return
        upload = path == "/api/v1/demo-runs" and method == "POST"
        diagnostic.stage("upload_validation")
        if upload and not self.upload_slot.acquire(blocking=False):
            await deny("DEMO_BUSY", 429)
            return
        limit = self.upload_limit + 65536 if upload else 4096 if path == "/api/v1/auth/login" else 128 * 1024
        consumed = 0

        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    raise BodyTooLarge
            return message
        try:
            length = request.headers.get("content-length")
            if length is not None and (not length.isascii() or not length.isdigit() or len(length) > 9 or int(length) > limit):
                await deny("DEMO_REQUEST_TOO_LARGE", 413)
                return
            # Buffer only bounded API mutations before framework parsing, so an
            # oversized chunked multipart cannot be swallowed by parser exception handling.
            if is_api and method not in {"GET", "HEAD"}:
                chunks = []
                while True:
                    message = await bounded_receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunks.append(message.get("body", b""))
                    if not message.get("more_body", False):
                        break
                buffered = b"".join(chunks)
                async def replay():
                    return {"type": "http.request", "body": buffered, "more_body": False}
                await self.app(scope, replay, secure_send)
            else:
                await self.app(scope, receive, secure_send)
        except BodyTooLarge:
            await deny("DEMO_REQUEST_TOO_LARGE", 413)
        finally:
            if upload:
                self.upload_slot.release()


class LoginCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)


def create_cloud_application(config: CloudConfig, workflow: DurableWorkflow, readiness: Callable[[], None],
                             *, static_directory: Path | None = None, auth: DemoAuth | None = None) -> FastAPI:
    """Trusted dependency injection for tests; the environment factory below owns production wiring."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    authentication = auth or DemoAuth(config.password_hash)
    app.state.demo_workflow, app.state.demo_auth = workflow, authentication

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        return error("INVALID_DEMO_INPUT", 422)

    @app.get("/api/v1/health")
    def health():
        return {"status": "healthy"}

    @app.get("/api/v1/ready")
    def ready():
        try:
            readiness()
            return {"status": "ready"}
        except Exception:
            return error("CLOUD_DATABASE_NOT_READY", 503)

    @app.post("/api/v1/auth/login")
    def login(command: LoginCommand):
        token = authentication.login(command.password)
        if token is None:
            return error("INVALID_DEMO_LOGIN", 401)
        response = JSONResponse({"authenticated": True, "csrf_token": authentication.get(token).csrf})
        response.set_cookie(COOKIE, token, max_age=authentication.ttl_seconds, httponly=True, secure=True,
                            samesite="strict", path="/")
        return response

    @app.get("/api/v1/auth/session")
    def session(request: Request):
        value = authentication.get(request.cookies.get(COOKIE, ""))
        return {"authenticated": True, "csrf_token": value.csrf} if value else error("AUTHENTICATION_REQUIRED", 401)

    @app.post("/api/v1/auth/logout")
    def logout(request: Request):
        authentication.logout(request.cookies.get(COOKIE, ""))
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        return response

    router = APIRouter(route_class=CloudBusinessRoute)
    for route in local_business_router.routes:
        router.add_api_route(route.path, route.endpoint, methods=route.methods,
                             status_code=route.status_code, response_model=route.response_model)
    app.include_router(router, prefix="/api/v1")
    if static_directory is not None:
        app.mount("/", StaticFiles(directory=static_directory, html=True), name="frontend")
    app.add_middleware(CORSMiddleware, allow_origins=[config.origin], allow_credentials=True,
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Demo-Request", "X-Demo-CSRF"])
    app.add_middleware(PublicBoundary, auth=authentication, config=config, readiness=readiness,
                       upload_limit=workflow.config.max_upload_bytes)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(config.origin).hostname])
    return app


def _one_attempt_client(api_key: str, timeout_seconds: int):
    from google import genai
    from google.genai import types
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_seconds * 1000,
                        retry_options=types.HttpRetryOptions(attempts=1)))


def create_cloud_app() -> FastAPI:
    """Render entry point; startup never runs migrations or extraction."""
    cloud = CloudConfig.from_env()
    config = AppConfig.from_env()
    if not 0 < config.max_upload_bytes <= 5 * 1024 * 1024:
        raise ValueError("CLOUD_UPLOAD_LIMIT_MAX_5_MIB_REQUIRED")
    engine = create_cloud_engine(os.environ.get("REVIEW_DATABASE_DSN", ""), cloud.database_name)
    try:
        provider = config.default_extraction_adapter.strip().lower()
        if provider not in {"local_ocr", "none", "", "gemini"}:
            raise ValueError("UNSUPPORTED_CLOUD_PROVIDER")
        adapter = GeminiExtractionAdapter(config=config, max_document_bytes=config.max_upload_bytes,
            timeout_seconds=cloud.gemini_timeout_seconds, max_retries=0, client_factory=_one_attempt_client,
            observer=diagnostic.observe_provider) if provider == "gemini" else None
        diagnostic.LOGGER.info(json.dumps({"event": "cloud_extraction_config",
            "adapter_gemini": provider == "gemini", "model_is_gemini_38_flash": config.gemini_model == "gemini-3.8-flash",
            "api_key_present": bool(config.gemini_api_key), "ai_outbound_enabled": config.ai_outbound_enabled,
            "external_policy_enabled": config.allow_external_phi, "gemini_approved": "gemini" in config.approved_ai_providers,
            "structured_output_enabled": config.gemini_structured_outputs_experimental,
            "sdk_timeout_ms": cloud.gemini_timeout_seconds * 1000, "adapter_retries": 0, "sdk_attempts": 1}, separators=(",", ":")))
        workflow = CloudWorkflow(config, adapter, BusinessRepository(engine, capacity_limits=(30, 32 * 1024 * 1024)))
        directory = Path(__file__).resolve().parents[1] / "frontend/dist"
        if not (directory / "index.html").is_file():
            raise ValueError("FRONTEND_BUILD_REQUIRED")
        app = create_cloud_application(cloud, workflow, lambda: check_business_schema(engine, cloud.database_name),
                                       static_directory=directory)
    except Exception:
        engine.dispose()
        raise RuntimeError("CLOUD_CONFIGURATION_FAILED") from None

    @asynccontextmanager
    async def lifespan(application):
        try:
            yield
        finally:
            engine.dispose()
    app.router.lifespan_context = lifespan
    return app
