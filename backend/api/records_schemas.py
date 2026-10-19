"""Bounded local records queries; list responses never contain document bodies."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RecordsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str = Field(default="", max_length=200)
    status: Literal["pending", "completed"] | None = None
    sender: str = Field(default="", max_length=200)
    receipt_date: date | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100_000)
