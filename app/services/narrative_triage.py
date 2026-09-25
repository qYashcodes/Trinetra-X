from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import json
from pathlib import Path
import re
import unicodedata
from typing import Any

from pypdf import PdfReader
from sqlmodel import Session, select

from app.models import ComplaintNarrative, NarrativeAssessment, NarrativeAssessmentReview
from app.services.hash import sha256_bytes, sha256_json
from app.services.time import now_ms
from app.settings import ROOT_DIR, settings


NARRATIVE_RESULT_SCHEMA = "trinetra.narrative_triage/1"
ANALYZER_REVISION = "deterministic-indicators-v1"
EXTRACTION_REVISION = "pypdf-text-v1"
MAX_NARRATIVE_BYTES = 10 * 1024 * 1024
MAX_NARRATIVE_CHARS = 100_000
ALLOWED_LANGUAGE = "en"
NEGATION_WORDS = {"no", "not", "never", "without", "denied", "denies", "denying"}
TAXONOMY_PATH = ROOT_DIR / "app" / "data" / "narrative_typology_v1.json"


class NarrativeTriageError(ValueError):
    pass


@lru_cache(maxsize=1)
def narrative_taxonomy() -> dict[str, Any]:
    payload = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    if payload.get("schema") != "trinetra.narrative_typology/1":
        raise NarrativeTriageError("Narrative taxonomy schema is unsupported.")
    return payload


def typology_options() -> list[dict[str, str]]:
    taxonomy = narrative_taxonomy()
    by_key = {str(item["key"]): item for item in taxonomy["categories"]}
    return [
        {"key": key, "label": str(by_key[key]["label"])}
        for key in taxonomy["taxonomy_order"]
    ]


def analyse_narrative(
    text: str | None,
    *,
    language: str,
    source_sha256: str | None = None,
    pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    taxonomy = narrative_taxonomy()
    normalized_language = (language or "").strip().lower()
    if normalized_language != ALLOWED_LANGUAGE:
        return _empty_result(
            "unsupported_language",
            normalized_language or "und",
            source_sha256,
            taxonomy,
            "Version 1 analyses only text explicitly identified as English.",
        )
    if not isinstance(text, str):
        return _empty_result(
            "invalid_input",
            normalized_language,
            source_sha256,
            taxonomy,
            "Narrative text must be a string.",
        )
    if len(text) > MAX_NARRATIVE_CHARS:
        return _empty_result(
            "text_too_large",
            normalized_language,
            source_sha256 or sha256_bytes(text.encode("utf-8")),
            taxonomy,
            "Narrative exceeds the 100,000 character analysis limit and was not truncated.",
        )
    if not text.strip():
        return _empty_result(
            "unknown",
            normalized_language,
            source_sha256 or sha256_bytes(text.encode("utf-8")),
            taxonomy,
            "No narrative indicators were available for review.",
        )

    digest = source_sha256 or sha256_bytes(text.encode("utf-8"))
    segments = pages or [{"page": None, "text": text, "base_offset": 0}]
    category_rows: list[dict[str, Any]] = []
    negated: list[dict[str, Any]] = []
    order = {key: index for index, key in enumerate(taxonomy["taxonomy_order"])}

    for category in taxonomy["categories"]:
        matches = _category_matches(category, segments)
        affirmed = [item for item in matches if item["polarity"] == "affirmed"]
        negated.extend(item for item in matches if item["polarity"] == "negated")
        direct = [item for item in affirmed if item["kind"] == "direct"]
        groups = sorted(
            {str(item["group"]) for item in affirmed if item["kind"] == "corroborating"}
        )
        required = int(category.get("required_groups") or 2)
        if not direct and len(groups) < required:
            continue
        category_rows.append(
            {
                "key": str(category["key"]),
                "label": str(category["label"]),
                "basis": "direct_term" if direct else "corroborated_indicators",
                "direct_match": bool(direct),
                "evidence_groups": groups,
                "indicators": affirmed,
                "reasoning": _candidate_reasoning(bool(direct), groups),
                "limitation": (
                    "Narrative indicators support review of the reported typology only; "
                    "they do not establish identity, participation, custody or an offence."
                ),
            }
        )

    category_rows.sort(
        key=lambda row: (
            0 if row["direct_match"] else 1,
            -len(row["evidence_groups"]),
            order[row["key"]],
        )
    )
    return {
        "schema": NARRATIVE_RESULT_SCHEMA,
        "status": "candidate" if category_rows else "unknown",
        "method": "deterministic_indicator_rules",
        "language": normalized_language,
        "source_sha256": digest,
        "analyzer_revision": ANALYZER_REVISION,
        "taxonomy_revision": str(taxonomy["revision"]),
        "extraction_revision": EXTRACTION_REVISION,
        "candidates": category_rows,
        "negated_indicators": sorted(negated, key=_match_sort_key),
        "score": None,
        "posterior": None,
        "probability_enabled": False,
        "calibration_status": "not_applicable_deterministic_rules",
        "reason": (
            "Review one or more reported typology candidates."
            if category_rows
            else "No category met the deterministic evidence-sufficiency rules."
        ),
        "limitations": [
            "This is complaint narrative triage, not sentiment analysis.",
            "Results are advisory and require officer review.",
            "A narrative match is not evidence of identity, participation, custody or guilt.",
        ],
    }


def extract_searchable_pdf(data: bytes) -> dict[str, Any]:
    if not data:
        return _extraction_result("text_unavailable", reason="The PDF is empty.")
    if len(data) > MAX_NARRATIVE_BYTES:
        return _extraction_result("text_too_large", reason="The PDF exceeds 10 MB.")
    if not data.startswith(b"%PDF-"):
        return _extraction_result("invalid_input", reason="The upload is not a PDF document.")
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            return _extraction_result(
                "text_unavailable",
                reason="Encrypted PDFs are not extracted in version 1.",
            )
        page_rows: list[dict[str, Any]] = []
        parts: list[str] = []
        offset = 0
        for page_number, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text() or ""
            page_rows.append({"page": page_number, "text": page_text, "base_offset": offset})
            parts.append(page_text)
            offset += len(page_text) + 2
        text = "\n\n".join(parts)
    except Exception:
        return _extraction_result(
            "invalid_input",
            reason="The PDF could not be parsed as a searchable document.",
        )
    if len(text) > MAX_NARRATIVE_CHARS:
        return _extraction_result(
            "text_too_large",
            reason="Extracted text exceeds 100,000 characters and was not truncated.",
        )
    if not text.strip():
        return _extraction_result(
            "text_unavailable",
            reason="No searchable text was found; OCR is not enabled.",
        )
    return {
        "status": "ready",
        "reason": "Searchable PDF text extracted locally.",
        "text": text,
        "pages": page_rows,
        "extraction_revision": EXTRACTION_REVISION,
    }


def ingest_narrative(
    session: Session,
    *,
    case_id: int,
    source_kind: str,
    source_ref: str,
    language: str,
    original_name: str,
    mime_type: str,
    data: bytes,
    provenance: str,
    created_by_pis: str,
    retrieved_ts_ms: int | None = None,
) -> tuple[ComplaintNarrative, NarrativeAssessment, bool]:
    if case_id <= 0:
        raise NarrativeTriageError("A persisted case is required.")
    source_kind = _safe_token(source_kind, "source kind")
    source_ref = source_ref.strip()
    if not source_ref or len(source_ref) > 240:
        raise NarrativeTriageError("A source reference of 240 characters or fewer is required.")
    language = (language or "").strip().lower() or "und"
    if not re.fullmatch(r"(?:[a-z]{2,3}(?:-[a-z0-9]{2,8})*|und)", language):
        raise NarrativeTriageError("A valid source language code is required.")
    clean_name = _safe_filename(original_name)
    if not data:
        raise NarrativeTriageError("The narrative source is empty.")
    if len(data) > MAX_NARRATIVE_BYTES:
        raise NarrativeTriageError("Narrative sources must be 10 MB or smaller.")
    if mime_type not in {"text/plain", "application/pdf"}:
        raise NarrativeTriageError("Narrative sources must be UTF-8 text or PDF.")
    suffix = Path(clean_name).suffix.lower()
    if mime_type == "application/pdf" and suffix != ".pdf":
        raise NarrativeTriageError("PDF filename extension does not match its content type.")
    if mime_type == "text/plain" and suffix not in {".txt", ".text"}:
        raise NarrativeTriageError("Text filename extension does not match its content type.")
    if mime_type == "application/pdf" and not data.startswith(b"%PDF-"):
        raise NarrativeTriageError("PDF content does not match its MIME type.")
    if mime_type == "text/plain":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NarrativeTriageError("Narrative text must be valid UTF-8.") from exc
        extraction = (
            _extraction_result(
                "text_too_large",
                reason="Narrative exceeds 100,000 characters and was not truncated.",
            )
            if len(text) > MAX_NARRATIVE_CHARS
            else {
                "status": "ready" if text.strip() else "text_unavailable",
                "reason": "Structured UTF-8 narrative text received."
                if text.strip()
                else "The narrative text is empty.",
                "text": text,
                "pages": [{"page": None, "text": text, "base_offset": 0}],
                "extraction_revision": EXTRACTION_REVISION,
            }
        )
    else:
        extraction = extract_searchable_pdf(data)

    digest = sha256_bytes(data)
    existing = session.exec(
        select(ComplaintNarrative).where(
            ComplaintNarrative.case_id == case_id,
            ComplaintNarrative.source_kind == source_kind,
            ComplaintNarrative.source_ref == source_ref,
            ComplaintNarrative.source_sha256 == digest,
        )
    ).first()
    created = existing is None
    if existing is None:
        source_relative = _store_artifact(data, digest, Path(clean_name).suffix.lower() or ".txt")
        extracted_text = str(extraction.get("text") or "")
        extracted_ref = None
        extracted_sha = None
        if extracted_text:
            extracted_bytes = extracted_text.encode("utf-8")
            extracted_sha = sha256_bytes(extracted_bytes)
            extracted_ref = _store_artifact(extracted_bytes, extracted_sha, ".txt")
        timestamp = now_ms()
        existing = ComplaintNarrative(
            case_id=case_id,
            source_kind=source_kind,
            source_ref=source_ref,
            language=language,
            original_name=clean_name,
            mime_type=mime_type,
            size_bytes=len(data),
            source_sha256=digest,
            storage_ref=source_relative,
            extracted_text_sha256=extracted_sha,
            extracted_text_ref=extracted_ref,
            extraction_status=str(extraction["status"]),
            extraction_revision=EXTRACTION_REVISION,
            extraction_detail=str(extraction["reason"]),
            provenance=provenance,
            retrieved_ts_ms=now_ms() if retrieved_ts_ms is None else int(retrieved_ts_ms),
            created_by_pis=created_by_pis,
            created_ts_ms=timestamp,
        )
        session.add(existing)
        session.flush()
        previous = session.exec(
            select(ComplaintNarrative)
            .where(
                ComplaintNarrative.case_id == case_id,
                ComplaintNarrative.source_kind == source_kind,
                ComplaintNarrative.source_ref == source_ref,
                ComplaintNarrative.id != existing.id,
                ComplaintNarrative.superseded_by_id == None,  # noqa: E711
            )
            .order_by(ComplaintNarrative.created_ts_ms.desc(), ComplaintNarrative.id.desc())
        ).first()
        if previous is not None:
            previous.superseded_by_id = existing.id
            session.add(previous)

    assessment = session.exec(
        select(NarrativeAssessment).where(
            NarrativeAssessment.narrative_id == existing.id,
            NarrativeAssessment.analyzer_revision == ANALYZER_REVISION,
            NarrativeAssessment.taxonomy_revision == str(narrative_taxonomy()["revision"]),
        )
    ).first()
    if assessment is None:
        if extraction["status"] == "ready":
            result = analyse_narrative(
                str(extraction.get("text") or ""),
                language=language,
                source_sha256=digest,
                pages=list(extraction.get("pages") or []),
            )
        else:
            result = _empty_result(
                str(extraction["status"]),
                language,
                digest,
                narrative_taxonomy(),
                str(extraction["reason"]),
            )
        assessment = NarrativeAssessment(
            case_id=case_id,
            narrative_id=int(existing.id or 0),
            status=str(result["status"]),
            analyzer_revision=ANALYZER_REVISION,
            taxonomy_revision=str(narrative_taxonomy()["revision"]),
            result_json=result,
            result_sha256=sha256_json(result),
            created_ts_ms=now_ms(),
        )
        session.add(assessment)
        session.flush()
    return existing, assessment, created


def record_narrative_review(
    session: Session,
    *,
    assessment: NarrativeAssessment,
    accepted_typologies: list[str],
    primary_typology: str | None,
    reviewer_pis: str,
    note: str | None,
) -> NarrativeAssessmentReview:
    valid = {item["key"] for item in typology_options()}
    accepted = list(dict.fromkeys(str(item) for item in accepted_typologies))
    if any(item not in valid for item in accepted):
        raise NarrativeTriageError("Review contains an unknown typology.")
    primary = (primary_typology or "").strip() or None
    if primary == "none":
        primary = None
    if primary is not None and primary not in accepted:
        raise NarrativeTriageError("The primary typology must be one of the accepted typologies.")
    clean_note = (note or "").strip() or None
    if clean_note is not None and len(clean_note) > 1000:
        raise NarrativeTriageError("Review notes must be 1,000 characters or fewer.")
    row = NarrativeAssessmentReview(
        case_id=assessment.case_id,
        assessment_id=int(assessment.id or 0),
        accepted_typologies=accepted,
        primary_typology=primary,
        reviewer_pis=reviewer_pis,
        note=clean_note,
        created_ts_ms=now_ms(),
    )
    session.add(row)
    session.flush()
    return row


def narrative_case_rows(session: Session, case_id: int) -> list[dict[str, Any]]:
    narratives = session.exec(
        select(ComplaintNarrative)
        .where(
            ComplaintNarrative.case_id == case_id,
            ComplaintNarrative.superseded_by_id == None,  # noqa: E711
        )
        .order_by(ComplaintNarrative.created_ts_ms.desc(), ComplaintNarrative.id.desc())
    ).all()
    rows: list[dict[str, Any]] = []
    for narrative in narratives:
        assessment = session.exec(
            select(NarrativeAssessment)
            .where(NarrativeAssessment.narrative_id == narrative.id)
            .order_by(NarrativeAssessment.created_ts_ms.desc(), NarrativeAssessment.id.desc())
        ).first()
        review = (
            session.exec(
                select(NarrativeAssessmentReview)
                .where(NarrativeAssessmentReview.assessment_id == assessment.id)
                .order_by(
                    NarrativeAssessmentReview.created_ts_ms.desc(),
                    NarrativeAssessmentReview.id.desc(),
                )
            ).first()
            if assessment is not None
            else None
        )
        rows.append({"narrative": narrative, "assessment": assessment, "review": review})
    return rows


def narrative_export_rows(session: Session, case_id: int) -> list[dict[str, Any]]:
    exported: list[dict[str, Any]] = []
    for row in narrative_case_rows(session, case_id):
        narrative = row["narrative"]
        assessment = row["assessment"]
        review = row["review"]
        exported.append(
            {
                "source": {
                    "id": narrative.id,
                    "source_kind": narrative.source_kind,
                    "source_ref": narrative.source_ref,
                    "language": narrative.language,
                    "mime_type": narrative.mime_type,
                    "size_bytes": narrative.size_bytes,
                    "source_sha256": narrative.source_sha256,
                    "extracted_text_sha256": narrative.extracted_text_sha256,
                    "extraction_status": narrative.extraction_status,
                    "extraction_revision": narrative.extraction_revision,
                    "provenance": narrative.provenance,
                    "retrieved_ts_ms": narrative.retrieved_ts_ms,
                    "protected_source_route": f"/cases/{case_id}/narratives/{narrative.id}/source",
                },
                "assessment": None
                if assessment is None
                else {
                    "id": assessment.id,
                    "status": assessment.status,
                    "analyzer_revision": assessment.analyzer_revision,
                    "taxonomy_revision": assessment.taxonomy_revision,
                    "result_sha256": assessment.result_sha256,
                    "result": assessment.result_json,
                },
                "latest_review": None
                if review is None
                else {
                    "id": review.id,
                    "accepted_typologies": review.accepted_typologies,
                    "primary_typology": review.primary_typology,
                    "reviewer_pis": review.reviewer_pis,
                    "note": review.note,
                    "created_ts_ms": review.created_ts_ms,
                },
            }
        )
    return exported


def narrative_artifact_path(storage_ref: str) -> Path:
    relative = Path(storage_ref)
    if relative.is_absolute() or ".." in relative.parts:
        raise NarrativeTriageError("Narrative storage reference is invalid.")
    return settings.var_dir / relative


def _category_matches(category: dict[str, Any], segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    terms: list[tuple[str, str, str]] = []
    for term in category.get("direct_terms") or []:
        terms.append(("direct", "direct", str(term)))
    for group, values in (category.get("groups") or {}).items():
        for term in values:
            terms.append(("corroborating", str(group), str(term)))
    for term in category.get("context_terms") or []:
        terms.append(("context", "context", str(term)))
    raw: list[dict[str, Any]] = []
    for segment in segments:
        source_text = str(segment.get("text") or "")
        normalized, index_map = _normalise_with_map(source_text)
        for kind, group, term in terms:
            pattern = _term_pattern(term)
            for match in pattern.finditer(normalized):
                start = index_map[match.start()] if index_map else match.start()
                end = (index_map[match.end() - 1] + 1) if index_map else match.end()
                polarity = "negated" if _is_negated(normalized, match.start()) else "affirmed"
                raw.append(
                    {
                        "indicator_id": _indicator_id(str(category["key"]), kind, group, term),
                        "category_key": str(category["key"]),
                        "kind": kind,
                        "group": group,
                        "term": term,
                        "polarity": polarity,
                        "page": segment.get("page"),
                        "start": int(segment.get("base_offset") or 0) + start,
                        "end": int(segment.get("base_offset") or 0) + end,
                    }
                )
    return _longest_non_overlapping(raw)


def _longest_non_overlapping(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    for item in sorted(
        matches,
        key=lambda row: (
            row["page"] if row["page"] is not None else 0,
            row["start"],
            -(row["end"] - row["start"]),
            0 if row["kind"] == "direct" else 1 if row["kind"] == "corroborating" else 2,
        ),
    ):
        if any(
            prior["page"] == item["page"]
            and item["start"] < prior["end"]
            and prior["start"] < item["end"]
            for prior in chosen
        ):
            continue
        chosen.append(item)
    return sorted(chosen, key=_match_sort_key)


def _match_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    return (
        int(item["page"] or 0),
        int(item["start"]),
        str(item["indicator_id"]),
    )


def _candidate_reasoning(direct: bool, groups: list[str]) -> str:
    group_text = ", ".join(group.replace("_", " ") for group in groups) or "none"
    if direct:
        return (
            "Suggested because at least one non-negated direct typology indicator matched. "
            f"Corroborating evidence groups also present: {group_text}."
        )
    return (
        "Suggested because distinct category-exclusive evidence groups met the deterministic "
        f"sufficiency rule: {group_text}."
    )


def _normalise_with_map(text: str) -> tuple[str, list[int]]:
    characters: list[str] = []
    index_map: list[int] = []
    for index, character in enumerate(text):
        normalized = unicodedata.normalize("NFKC", character).casefold()
        characters.extend(normalized)
        index_map.extend([index] * len(normalized))
    return "".join(characters), index_map


def _term_pattern(term: str) -> re.Pattern[str]:
    pieces = [re.escape(piece) for piece in term.casefold().split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(pieces) + r"(?!\w)", re.UNICODE)


def _is_negated(text: str, start: int) -> bool:
    prefix = text[:start]
    clause = re.split(r"[.;:!?\n]", prefix)[-1]
    words = re.findall(r"\b[\w'-]+\b", clause, flags=re.UNICODE)
    return bool(NEGATION_WORDS.intersection(words[-3:]))


def _indicator_id(category: str, kind: str, group: str, term: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", term.casefold()).strip("_")
    return f"{category}:{kind}:{group}:{slug}"


def _empty_result(
    status: str,
    language: str,
    source_sha256: str | None,
    taxonomy: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    return {
        "schema": NARRATIVE_RESULT_SCHEMA,
        "status": status,
        "method": "deterministic_indicator_rules",
        "language": language,
        "source_sha256": source_sha256,
        "analyzer_revision": ANALYZER_REVISION,
        "taxonomy_revision": str(taxonomy["revision"]),
        "extraction_revision": EXTRACTION_REVISION,
        "candidates": [],
        "negated_indicators": [],
        "score": None,
        "posterior": None,
        "probability_enabled": False,
        "calibration_status": "not_applicable_deterministic_rules",
        "reason": reason,
        "limitations": [
            "This is complaint narrative triage, not sentiment analysis.",
            "Results are advisory and require officer review.",
            "A narrative match is not evidence of identity, participation, custody or guilt.",
        ],
    }


def _extraction_result(status: str, *, reason: str) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "text": "",
        "pages": [],
        "extraction_revision": EXTRACTION_REVISION,
    }


def _safe_filename(name: str) -> str:
    clean = Path(name or "narrative.txt").name
    if clean != name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ ()-]{0,199}", clean):
        raise NarrativeTriageError("Use a safe narrative filename.")
    return clean


def _safe_token(value: str, label: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", normalized):
        raise NarrativeTriageError(f"A valid {label} is required.")
    return normalized


def _store_artifact(data: bytes, digest: str, suffix: str) -> str:
    safe_suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ".bin"
    relative = Path("complaint_narratives") / digest[:2] / f"{digest}{safe_suffix}"
    target = settings.var_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha256_bytes(target.read_bytes()) != digest:
            raise NarrativeTriageError("Content-addressed narrative collision detected.")
    else:
        target.write_bytes(data)
    return relative.as_posix()
