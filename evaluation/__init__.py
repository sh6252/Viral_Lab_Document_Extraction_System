"""Evaluation foundation for the Virology Lab IDP project."""

from .scoring import REQUIRED_FIELDS, MULTI_VALUE_FIELDS, score_extraction

__all__ = ["REQUIRED_FIELDS", "MULTI_VALUE_FIELDS", "score_extraction"]
