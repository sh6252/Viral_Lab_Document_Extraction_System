"""Synthetic, offline tests for screening, provenance and non-authoritative advice."""

from dataclasses import FrozenInstanceError, replace
from itertools import permutations

import pytest

from backend.domain import ExtractionResult, FieldResult, FieldValue, LogicalFormResult
from backend.services.extraction.ocr_candidates import (
    FieldAssignmentEvidence,
    OCRCandidate,
    assess_ocr_candidates,
)
from tests.test_scoring import extraction, result_field

IMAGE_HASH = "a" * 64


def candidate(value="001", name="central_numbers", **changes):
    values = dict(
        field_name=name, raw_value=value, source_engine="synthetic-ocr",
        source_engine_version="test-v1", source_run_id="run-1",
        source_image_sha256=IMAGE_HASH, document_id="doc-1", form_id="form-1",
        page_number=1,
        field_assignment_evidence=FieldAssignmentEvidence(name, "label_relation", "label-1"),
    )
    values.update(changes)
    return OCRCandidate(**values)


def canonical(central=("001",), commitments=(), *, review="unreviewed"):
    def value_field(values):
        return result_field("present" if values else "missing", values, review_status=review)
    return ExtractionResult.model_validate(extraction({
        "central_numbers": value_field(central),
        "commitment_numbers": value_field(commitments),
    }))


def assess(result, candidates=(), **changes):
    options = dict(document_id="doc-1", form_page_images={"form-1": {1: IMAGE_HASH}})
    options.update(changes)
    return assess_ocr_candidates(result, candidates, **options)


@pytest.mark.parametrize("name", ["central_numbers", "commitment_numbers"])
def test_supported_fields_and_exact_matches(name):
    result = canonical(central=("001",), commitments=("001",))
    report = assess(result, [candidate(name=name)])
    field = next(f for f in report.fields if f.field_name == name)
    assert field.state == "corroborated"
    assert field.matched_values == ("001",)
    assert field.provider_only_values == field.ocr_only_values == ()
    assert not field.review_recommended
    assert field.ocr_candidates[0].validation_status == "syntax_screened_unverified"


@pytest.mark.parametrize("name", ["first_name", "last_name", "israeli_id", "sample_receipt_date", "sender"])
def test_unsupported_fields_rejected(name):
    with pytest.raises(ValueError, match="UNSUPPORTED_FIELD"):
        candidate(name=name)


def test_partial_multi_value_support_does_not_interpret_ocr_silence_as_conflict():
    field = assess(canonical(("001", "002")), [candidate()]).fields[0]
    assert field.state == "partial_support"
    assert field.matched_values == ("001",)
    assert field.provider_only_values == ("002",)
    assert not field.review_recommended


def test_no_ocr_keeps_provider_only_values_and_does_not_claim_absence():
    report = assess(canonical(("001", "002")))
    assert report.source_status == "succeeded"
    assert report.fields[0].state == "no_support"
    assert report.fields[0].provider_only_values == ("001", "002")
    assert not report.fields[0].review_recommended


@pytest.mark.parametrize("status", ["missing", "unreadable", "ambiguous", "not_processed", None])
def test_ocr_only_candidate_does_not_promote_missing_or_omitted_field(status):
    result = canonical(central=())
    result.logical_forms[0].fields.central_numbers = (
        FieldResult(value_status=status, values=[], review_status="needs_review") if status else None
    )
    before = result.model_dump(mode="json")
    field = assess(result, [candidate()]).fields[0]
    assert field.state == "ocr_only"
    assert field.provider_status == status
    assert field.ocr_only_values == ("001",)
    assert field.review_recommended
    assert "OCR_CANDIDATE_WITHOUT_PROVIDER_VALUE" in field.review_reasons
    assert result.model_dump(mode="json") == before


@pytest.mark.parametrize("ocr_values", [("999",), ("001", "999")])
def test_disjoint_and_superset_conflicts_do_not_union_provider_values(ocr_values):
    result = canonical()
    before = result.model_dump(mode="json")
    field = assess(result, [candidate(v) for v in ocr_values]).fields[0]
    assert field.state == "disagreement"
    assert field.ocr_only_values == ("999",)
    assert [v.raw_value for v in field.provider_values] == ["001"]
    assert field.review_reasons == ("OCR_PROVIDER_DISAGREEMENT",)
    assert result.model_dump(mode="json") == before


def test_duplicate_values_deduplicate_comparison_but_preserve_every_evidence_occurrence():
    first = candidate()
    second = replace(first, source_run_id="run-2")
    field = assess(canonical(), [first, second, first]).fields[0]
    assert field.state == "corroborated"
    assert field.matched_values == ("001",)
    assert len(field.ocr_candidates) == 3
    assert {c.source_run_id for c in field.ocr_candidates} == {"run-1", "run-2"}


@pytest.mark.parametrize("value", ["0", "000000", "1" * 100])
def test_numeric_screen_preserves_zeros_without_length_or_checksum_rules(value):
    c = candidate(value)
    assert c.raw_value == value
    assert c.normalized_value is None
    assert assess(canonical((value,)), [c]).fields[0].matched_values == (value,)


def test_leading_zero_difference_is_a_conflict_not_numeric_equivalence():
    field = assess(canonical(("001",)), [candidate("1")]).fields[0]
    assert field.state == "disagreement"
    assert field.provider_only_values == ("001",)
    assert field.ocr_only_values == ("1",)


@pytest.mark.parametrize("value", [
    "2603204)", " 001", "001 ", "001\n", "001 002", "12/08/2026",
    "12-34", "O01", "X132767", "HT-00077", "", "١٢٣", "１２３", 123, None,
])
def test_malformed_punctuated_and_unsupported_numeric_tokens_are_not_repaired(value):
    with pytest.raises(ValueError, match="INTACT_ASCII_NUMERIC_TOKEN_REQUIRED"):
        candidate(value)


def test_alphanumeric_business_value_remains_legal_and_unchanged_without_ocr_support():
    result = canonical(commitments=("HT-00077",))
    field = assess(result).fields[1]
    assert field.provider_only_values == ("HT-00077",)
    assert field.state == "no_support"


@pytest.mark.parametrize("evidence", [None, "unlabeled barcode", {}])
def test_missing_or_unstructured_assignment_evidence_rejected(evidence):
    with pytest.raises(ValueError, match="EXPLICIT_FIELD_ASSIGNMENT_REQUIRED"):
        candidate(field_assignment_evidence=evidence)


@pytest.mark.parametrize("kind", ["unlabeled_barcode", "nearby_sticker", "truth_match", "value_match"])
def test_unlabeled_or_value_based_assignment_is_not_explicit_field_evidence(kind):
    with pytest.raises(ValueError, match="EXPLICIT_FIELD_ASSIGNMENT_REQUIRED"):
        FieldAssignmentEvidence("central_numbers", kind, "reference-1")


def test_wrong_field_assignment_and_empty_reference_rejected():
    with pytest.raises(ValueError, match="FIELD_ASSIGNMENT_MISMATCH"):
        candidate(field_assignment_evidence=FieldAssignmentEvidence(
            "commitment_numbers", "label_relation", "reference-1",
        ))
    with pytest.raises(ValueError, match="NONEMPTY_TEXT_REQUIRED"):
        FieldAssignmentEvidence("central_numbers", "reviewed_region", " ")


def test_canonical_not_mutated_or_referenced_and_existing_review_never_cleared():
    result = canonical(review="needs_review")
    before = result.model_dump(mode="json")
    field = assess(result, [candidate()]).fields[0]
    assert field.state == "corroborated"
    assert field.provider_review_status == "needs_review"
    assert field.review_recommended
    assert field.review_reasons == ("EXISTING_REVIEW_REQUIRED",)
    assert result.model_dump(mode="json") == before
    result.logical_forms[0].fields.central_numbers.values[0].raw = "changed later"
    assert field.provider_values[0].raw_value == "001"
    with pytest.raises(FrozenInstanceError):
        field.state = "no_support"
    with pytest.raises(FrozenInstanceError):
        field.ocr_candidates[0].raw_value = "changed"


def test_conflict_with_human_approval_only_recommends_review_without_overwriting_it():
    result = canonical(review="approved_corrected")
    field = assess(result, [candidate("999")]).fields[0]
    assert field.review_recommended
    assert result.logical_forms[0].fields.central_numbers.review_status == "approved_corrected"


@pytest.mark.parametrize("status", ["disabled", "unavailable", "failed"])
def test_ocr_source_outcomes_are_distinct_from_empty_success(status):
    report = assess(canonical(), source_status=status)
    assert report.source_status == status
    assert all(f.state == "no_support" for f in report.fields)
    with pytest.raises(ValueError, match="CANDIDATES_REQUIRE_SUCCESSFUL_OCR_SOURCE"):
        assess(canonical(), [candidate()], source_status=status)


def test_multiple_fields_and_full_provenance_preserved():
    c = candidate(bbox=(1.0, 2.0, 3.0, 4.0), bbox_coordinate_system="ocr-pdf-points")
    other = candidate("009", "commitment_numbers",
                      field_assignment_evidence=FieldAssignmentEvidence(
                          "commitment_numbers", "reviewed_region", "region-2",
                      ))
    report = assess(canonical(commitments=("009",)), [other, c])
    assert report.extraction_id == "result-1"
    assert report.document_id == "doc-1"
    assert report.fields[0].ocr_candidates == (c,)
    assert report.fields[1].ocr_candidates == (other,)
    assert all(f.state == "corroborated" for f in report.fields)


def test_order_is_deterministic_for_candidates_provider_values_and_forms():
    result = canonical(("002", "001"))
    result.logical_forms.append(LogicalFormResult(form_id="form-2", fields=result.logical_forms[0].fields.model_copy(deep=True)))
    items = [candidate(), candidate("002"), candidate(form_id="form-2")]
    scope = {"form-2": {1: IMAGE_HASH}, "form-1": {1: IMAGE_HASH}}
    expected = assess(result, items, form_page_images=scope)
    for ordering in permutations(items):
        result.logical_forms.reverse()
        for form in result.logical_forms:
            form.fields.central_numbers.values.reverse()
        assert assess(result, iter(ordering), form_page_images=scope) == expected
    assert [f.form_id for f in expected.fields] == ["form-1", "form-1", "form-2", "form-2"]


@pytest.mark.parametrize("changes", [
    {"document_id": "another-doc"}, {"form_id": "other-form"},
    {"page_number": 2}, {"source_image_sha256": "b" * 64},
])
def test_candidate_with_wrong_document_form_page_or_image_is_rejected(changes):
    with pytest.raises(ValueError, match="CANDIDATE_PROVENANCE_MISMATCH"):
        assess(canonical(), [candidate(**changes)])


@pytest.mark.parametrize("source", ["provider", "ocr"])
def test_normalized_values_cannot_establish_equivalence(source):
    result = canonical(("001",))
    c = candidate("1")
    if source == "provider":
        result.logical_forms[0].fields.central_numbers.values[0].normalized = "1"
    else:
        c = replace(c, normalized_value="001")
    field = assess(result, [c]).fields[0]
    assert field.state == "unresolved_normalization"
    assert field.matched_values == ()
    assert field.review_reasons == ("UNSUPPORTED_NORMALIZATION",)
    assert field.provider_only_values == ("001",)
    assert field.ocr_only_values == ("1",)


def test_no_ocr_does_not_add_normalization_review_advice():
    result = canonical()
    result.logical_forms[0].fields.central_numbers.values[0].normalized = "1"
    assert assess(result).fields[0].state == "no_support"
    assert not assess(result).fields[0].review_recommended


def test_provider_failure_cannot_be_turned_into_an_extraction_result():
    with pytest.raises(ValueError, match="SUCCESSFUL_CANONICAL_RESULT_REQUIRED"):
        assess(None, [candidate()])


@pytest.mark.parametrize("changes", [
    {"page_number": 0}, {"page_number": True}, {"source_image_sha256": "bad-hash"},
    {"source_run_id": ""}, {"bbox": [0, 0, 1, 1], "bbox_coordinate_system": "pixels"},
    {"bbox": (0, 0, float('nan'), 1), "bbox_coordinate_system": "pixels"},
    {"bbox": (0, 0, 1, 1)}, {"bbox_coordinate_system": "pixels"},
])
def test_invalid_or_mutable_provenance_rejected(changes):
    with pytest.raises(ValueError):
        candidate(**changes)


def test_bypassing_canonical_sanitization_fails_explicitly():
    result = canonical(central=())
    result.logical_forms[0].fields.central_numbers.values.append(FieldValue(raw="001"))
    with pytest.raises(ValueError, match="SANITIZED_CANONICAL_FIELDS_REQUIRED"):
        assess(result)
