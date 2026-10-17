"""Thin authenticated review routes, with route-local safe validation handling."""

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from backend.api.dependencies import (
    get_review_api_service, get_review_authorization_policy, require_authenticated_principal,
)
from backend.api.review_schemas import ReviewDecisionResponseDTO, ReviewSessionDTO, SubmitReviewDecisionDTO
from backend.core.review_access import (
    AuthenticatedPrincipal, AuthorizationPolicy, ReviewBoundaryError,
)
from backend.services.review_api import ReviewAPIService


def safe_error(error: ReviewBoundaryError) -> JSONResponse:
    return JSONResponse(status_code=error.status, headers={"Cache-Control": "no-store"}, content={
        "error": {"code": error.code, "message": error.message, "retryable": error.retryable},
    })


class PrivateReviewRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def protected(request: Request):
            try:
                # Authenticate before JSON parsing as well as before resource access.
                await require_authenticated_principal(request)
                response = await handler(request)
                response.headers["Cache-Control"] = "no-store"
                return response
            except ReviewBoundaryError as error:
                return safe_error(error)
            except RequestValidationError:
                return safe_error(ReviewBoundaryError("INVALID_REVIEW_DECISION"))
            except Exception:
                return safe_error(ReviewBoundaryError("REVIEW_SERVICE_UNAVAILABLE"))

        return protected


router = APIRouter(tags=["reviews"], route_class=PrivateReviewRoute)


@router.get("/review-sessions/{session_id}", response_model=ReviewSessionDTO)
def get_review_session(
    session_id: str,
    principal: AuthenticatedPrincipal = Depends(require_authenticated_principal),
    policy: AuthorizationPolicy = Depends(get_review_authorization_policy),
    service: ReviewAPIService = Depends(get_review_api_service),
) -> ReviewSessionDTO:
    return service.get(session_id, principal, policy)


@router.post("/review-sessions/{session_id}/decisions", response_model=ReviewDecisionResponseDTO)
def submit_review_decision(
    session_id: str, decision: SubmitReviewDecisionDTO,
    principal: AuthenticatedPrincipal = Depends(require_authenticated_principal),
    policy: AuthorizationPolicy = Depends(get_review_authorization_policy),
    service: ReviewAPIService = Depends(get_review_api_service),
) -> ReviewDecisionResponseDTO:
    return service.submit(session_id, decision, principal, policy)
