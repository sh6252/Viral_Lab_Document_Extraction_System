"""Opt-in loopback-only demo routes; no production identity/ACL implementation."""

from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from backend.api.demo_schemas import DemoReviewCommand
from backend.api.records_schemas import RecordsQuery
from backend.services.business_review import BusinessReviewError
from backend.services.demo_workflow import DemoError, DemoWorkflow
from backend.services.excel_export import ExcelExportError
from backend.services.extraction.errors import ExtractionAdapterError, UnsupportedDocumentType
from backend.services.extraction.upload_validation import detect_supported_mime_type, read_upload_bytes

LOCAL_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173",
                 "http://127.0.0.1:8000", "http://localhost:8000"}


def _error(code: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": "Local demo request could not be completed.",
                                   "retryable": False}}, status_code=status, headers={"Cache-Control": "no-store"})


class LocalDemoRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def local(request: Request):
            # Browser custom header + exact Origin + loopback peer/Host block ambient
            # cross-site forms/fetch and DNS rebinding. No permissive CORS is installed.
            if (request.client is None or request.client.host not in {"127.0.0.1", "::1"}
                    or request.url.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or request.headers.get("x-local-demo") != "1"
                    or request.headers.get("sec-fetch-site") == "cross-site"
                    or (request.headers.get("origin") is not None and request.headers["origin"] not in LOCAL_ORIGINS)):
                return _error("LOCAL_DEMO_ONLY", 403)
            try:
                # Bound JSON commands before framework deserialization/input echo.
                if request.method == "POST" and request.headers.get("content-type", "").startswith("application/json"):
                    if len(await request.body()) > 128 * 1024:
                        return _error("DEMO_COMMAND_TOO_LARGE", 413)
                response = await handler(request)
                response.headers["Cache-Control"] = "no-store"
                response.headers["X-Content-Type-Options"] = "nosniff"
                return response
            except DemoError as error:
                return _error(error.code, error.status)
            except RequestValidationError:
                return _error("INVALID_DEMO_INPUT", 422)
            except BusinessReviewError:
                return _error("INVALID_DEMO_REVIEW", 422)
            except ExtractionAdapterError as error:
                status = 413 if error.code == "DOCUMENT_TOO_LARGE" else 415 if error.code == "UNSUPPORTED_DOCUMENT_TYPE" else 400
                return _error("INVALID_DEMO_UPLOAD", status)
            except ExcelExportError:
                return _error("DEMO_EXPORT_FAILED", 422)
            except Exception:
                return _error("DEMO_UNAVAILABLE", 503)
        return local


router = APIRouter(tags=["local-demo"], route_class=LocalDemoRoute)


def _service(request: Request) -> DemoWorkflow:
    return request.app.state.demo_workflow


@router.get("/business-records")
def list_business_records(request: Request, query: Annotated[RecordsQuery, Query()]) -> dict:
    from backend.services.durable_workflow import DurableWorkflow

    service = _service(request)
    if not isinstance(service, DurableWorkflow):
        raise DemoError("DURABLE_RECORDS_UNAVAILABLE", 404)
    return service.list_records(query)


@router.post("/demo-runs", status_code=201)
async def upload_demo(request: Request, file: Annotated[UploadFile, File()],
                      allow_external_processing: Annotated[bool, Form()] = False,
                      locale: Annotated[str, Form()] = "he-IL") -> dict:
    service = _service(request)
    if set((await request.form()).keys()) - {"file", "allow_external_processing", "locale"}:
        raise DemoError("INVALID_DEMO_INPUT")
    content = await read_upload_bytes(file, max_bytes=service.config.max_upload_bytes)
    mime = detect_supported_mime_type(content)
    if file.content_type and file.content_type != mime:
        raise UnsupportedDocumentType("MIME signature mismatch.")
    return await run_in_threadpool(service.create, content, mime, allow_external_processing, locale)


@router.get("/demo-runs/{run_id}")
def get_demo(request: Request, run_id: str, form_id: str | None = None) -> dict:
    return _service(request).get(run_id, form_id)


@router.post("/demo-runs/{run_id}/forms/{form_id:path}/review")
def review_demo(request: Request, run_id: str, form_id: str, command: DemoReviewCommand) -> dict:
    return _service(request).review(run_id, form_id, command)


@router.get("/demo-runs/{run_id}/audit")
def audit_demo(request: Request, run_id: str, form_id: str | None = None) -> dict:
    return _service(request).history(run_id, form_id)


@router.get("/demo-runs/{run_id}/source")
def source_demo(request: Request, run_id: str) -> Response:
    content, mime = _service(request).source(run_id)
    return Response(content, media_type=mime,
        headers={"Content-Disposition": 'attachment; filename="original-document"', "Cache-Control": "no-store"})


@router.get("/demo-runs/{run_id}/export.xlsx")
def export_demo(request: Request, run_id: str) -> Response:
    return Response(_service(request).export(run_id),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="reviewed-demo.xlsx"', "Cache-Control": "no-store"})
