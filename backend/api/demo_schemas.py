"""Demo-specific inputs; canonical extraction/provenance are never client inputs."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from backend.domain.models import RequiredFieldName
from backend.services.business_review import ActionKind


class DemoInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DemoFieldAction(DemoInput):
    field_name: RequiredFieldName
    decision_id: StrictStr
    action: ActionKind
    reviewed_values: list[StrictStr]
    reason: StrictStr


class DemoReviewCommand(DemoInput):
    review_token: StrictStr
    expected_revision: StrictInt = Field(ge=0)
    operator_declaration: StrictStr
    declared_at: datetime
    actions: list[DemoFieldAction] = Field(min_length=1, max_length=7)

    @field_validator("declared_at", mode="before")
    @classmethod
    def aware_time(cls, value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError("ISO_TIME_REQUIRED")
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError("AWARE_TIME_REQUIRED")
        return result
