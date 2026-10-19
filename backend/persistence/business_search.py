"""Parameterized PostgreSQL search over existing versioned review projections."""

from collections.abc import Callable
from typing import Any

from sqlalchemy import case, cast, exists, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
from sqlalchemy.sql.elements import ColumnElement

from backend.api.records_schemas import RecordsQuery
from backend.persistence.business_models import documents, records
from backend.services.business_review import FIELDS


def field_values(name: str, *, include_normalized: bool = False) -> ColumnElement[Any]:
    field = cast(records.c.projection_json, JSONB)["payload"]["record"]["fields"][FIELDS.index(name)]
    raw = func.jsonb_path_query_array(field["original"]["values"], cast(literal("$[*].raw"), JSONPATH))
    if include_normalized:
        normalized = func.jsonb_path_query_array(field["original"]["values"], cast(literal("$[*].normalized"), JSONPATH))
        raw = raw.op("||")(normalized)
    # Corrected/approved values take precedence. Acknowledged blanks and explicitly
    # unresolved/rejected values are never repopulated from the original candidate.
    return case(
        (field["review_status"].astext.in_(("approved_provider", "approved_corrected")), field["reviewed_values"]),
        (field["review_status"].astext == "unreviewed", raw),
        else_=cast(literal("[]"), JSONB),
    )


def literal_pattern(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def matching_value(values: ColumnElement[Any],
                   predicate: Callable[[ColumnElement[str]], ColumnElement[bool]]) -> ColumnElement[bool]:
    entries = func.jsonb_array_elements_text(values).table_valued("value")
    return exists(select(literal(1)).select_from(entries).where(predicate(entries.c.value)))


def conditions(query: RecordsQuery) -> list[ColumnElement[bool]]:
    result = [documents.c.status == "succeeded"]
    if query.status:
        result.append(records.c.review_status == ("complete" if query.status == "completed" else "unresolved"))
    if query.q.strip():
        pattern = "%" + literal_pattern(query.q.strip()) + "%"
        searched = ("first_name", "last_name", "israeli_id", "central_numbers", "commitment_numbers", "sender")
        checks = [matching_value(field_values(name), lambda value: value.ilike(pattern, escape="!")) for name in searched]
        full_name = func.concat_ws(" ", cast(field_values("first_name"), JSONB)[0].astext,
                                   cast(field_values("last_name"), JSONB)[0].astext)
        result.append(or_(*checks, full_name.ilike(pattern, escape="!")))
    if query.sender.strip():
        result.append(matching_value(field_values("sender"), lambda value: func.btrim(value) == query.sender.strip()))
    if query.receipt_date:
        day = query.receipt_date
        # Exact date only. No unsafe SQL date casts of uncertain provider text.
        formats = (day.isoformat(), day.strftime("%d/%m/%Y"), f"{day.day}/{day.month}/{day.year}",
                   day.strftime("%d.%m.%Y"), f"{day.day}.{day.month}.{day.year}")
        result.append(matching_value(field_values("sample_receipt_date", include_normalized=True),
                                     lambda value: value.in_(formats)))
    return result
