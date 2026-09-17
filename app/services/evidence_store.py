from __future__ import annotations

import re
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from sqlmodel import Session

from app.models import SourceCoverage
from app.services.hash import canonical_json_bytes, sha256_bytes
from app.services.time import now_ms


class EvidenceStoreError(ValueError):
    pass


@dataclass
class ProviderEvidenceCapture:
    root: Path
    case_id: int | None
    lookup_ref: str | None = None
    snapshot_id: int | None = None
    records: list[dict[str, Any]] = field(default_factory=list)


_ACTIVE_CAPTURE: ContextVar[ProviderEvidenceCapture | None] = ContextVar(
    "trinetra_provider_evidence_capture",
    default=None,
)


@contextmanager
def provider_evidence_scope(
    *,
    root: Path,
    case_id: int | None = None,
    lookup_ref: str | None = None,
    snapshot_id: int | None = None,
) -> Iterator[ProviderEvidenceCapture]:
    if case_id is None and not (lookup_ref or "").strip():
        raise EvidenceStoreError("case_id or lookup_ref is required.")
    if case_id is not None and case_id <= 0:
        raise EvidenceStoreError("case_id must be positive.")
    capture = ProviderEvidenceCapture(
        root=root,
        case_id=case_id,
        lookup_ref=(lookup_ref or "").strip() or None,
        snapshot_id=snapshot_id,
    )
    token = _ACTIVE_CAPTURE.set(capture)
    try:
        yield capture
    finally:
        _ACTIVE_CAPTURE.reset(token)


def capture_provider_payload(
    *,
    provider: str,
    endpoint: str,
    query: dict[str, Any],
    status_code: int | None,
    payload: Any,
    retrieval_ts_ms: int | None = None,
) -> dict[str, Any] | None:
    capture = _ACTIVE_CAPTURE.get()
    if capture is None:
        return None
    retrieved = now_ms() if retrieval_ts_ms is None else retrieval_ts_ms
    raw_record = _store_captured_payload(
        capture=capture,
        provider=provider,
        payload=payload,
        retrieval_ts_ms=retrieved,
    )
    return _capture_provider_receipt(
        capture=capture,
        raw_record=raw_record,
        endpoint=endpoint,
        query=query,
        status_code=status_code,
        retrieval_ts_ms=retrieved,
        provider_watermark=_provider_watermark(payload),
    )


def capture_provider_bytes(
    *,
    provider: str,
    endpoint: str,
    query: dict[str, Any],
    status_code: int | None,
    body: bytes,
    retrieval_ts_ms: int | None = None,
) -> dict[str, Any] | None:
    capture = _ACTIVE_CAPTURE.get()
    if capture is None:
        return None
    retrieved = now_ms() if retrieval_ts_ms is None else retrieval_ts_ms
    provider_key = _safe_segment(provider)
    if not provider_key:
        raise EvidenceStoreError("provider is required.")
    raw_body = bytes(body)
    digest = sha256_bytes(raw_body)
    directory = _capture_directory(capture, provider_key)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{retrieved}-{digest[:16]}.bin"
    path.write_bytes(raw_body)
    raw_record = {
        "schema": "trinetra.raw_evidence/1",
        "case_id": capture.case_id,
        "lookup_ref": capture.lookup_ref,
        "provider": provider_key,
        "retrieval_ts_ms": retrieved,
        "content_kind": "bytes",
        "sha256": digest,
        "path": str(path),
        "size_bytes": len(raw_body),
    }
    return _capture_provider_receipt(
        capture=capture,
        raw_record=raw_record,
        endpoint=endpoint,
        query=query,
        status_code=status_code,
        retrieval_ts_ms=retrieved,
        provider_watermark={},
    )


def _capture_provider_receipt(
    *,
    capture: ProviderEvidenceCapture,
    raw_record: dict[str, Any],
    endpoint: str,
    query: dict[str, Any],
    status_code: int | None,
    retrieval_ts_ms: int,
    provider_watermark: dict[str, Any],
) -> dict[str, Any]:
    request_ref = secrets.token_hex(12)
    receipt = {
        "schema": "trinetra.provider_request_receipt/1",
        "request_ref": request_ref,
        "case_id": capture.case_id,
        "lookup_ref": capture.lookup_ref,
        "snapshot_id": capture.snapshot_id,
        "provider": raw_record["provider"],
        "endpoint": endpoint,
        "query": _safe_metadata(query),
        "status_code": status_code,
        "retrieval_ts_ms": retrieval_ts_ms,
        "raw_sha256": raw_record["sha256"],
        "raw_path": raw_record["path"],
        "raw_content_kind": raw_record["content_kind"],
        "provider_watermark": provider_watermark,
        "schema_status": "captured_unvalidated",
        "conflicts": [],
    }
    receipt_bytes = canonical_json_bytes(receipt)
    receipt_digest = sha256_bytes(receipt_bytes)
    receipt_dir = Path(raw_record["path"]).parent / "receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / f"{retrieval_ts_ms}-{request_ref}.json"
    receipt_path.write_bytes(receipt_bytes)
    receipt["receipt_sha256"] = receipt_digest
    receipt["receipt_path"] = str(receipt_path)
    capture.records.append(receipt)
    return receipt


def mark_provider_schema(
    receipt: dict[str, Any] | None,
    *,
    status: str,
    conflicts: list[dict[str, Any]] | None = None,
) -> None:
    if receipt is None:
        return
    receipt["schema_status"] = status
    receipt["conflicts"] = list(conflicts or [])
    assessment = {
        "schema": "trinetra.provider_schema_assessment/1",
        "request_ref": receipt.get("request_ref"),
        "raw_sha256": receipt.get("raw_sha256"),
        "schema_status": status,
        "conflicts": receipt["conflicts"],
    }
    assessment_bytes = canonical_json_bytes(assessment)
    assessment_path = Path(str(receipt["receipt_path"])).with_name(
        f"{receipt['retrieval_ts_ms']}-{receipt['request_ref']}-schema.json"
    )
    assessment_path.write_bytes(assessment_bytes)
    receipt["schema_assessment_path"] = str(assessment_path)
    receipt["schema_assessment_sha256"] = sha256_bytes(assessment_bytes)


def persist_provider_coverage(
    session: Session,
    records: list[dict[str, Any]],
    *,
    snapshot_id: int | None = None,
) -> list[SourceCoverage]:
    rows: list[SourceCoverage] = []
    for record in records:
        if record.get("case_id") is None:
            raise EvidenceStoreError("Case-less lookup evidence cannot be persisted as case coverage.")
        query = dict(record.get("query") or {})
        watermark = dict(record.get("provider_watermark") or {})
        cursor = watermark.get("fingerprint")
        status_code = record.get("status_code")
        schema_status = str(record.get("schema_status") or "captured_unvalidated")
        conflicts = list(record.get("conflicts") or [])
        gaps: list[dict[str, Any]] = []
        if status_code is None:
            gaps.append({"reason": "provider_no_response"})
        elif int(status_code) >= 400:
            gaps.append({"reason": "provider_http_error", "status_code": int(status_code)})
        if schema_status == "schema_drift":
            gaps.append({"reason": "schema_drift"})
        row = SourceCoverage(
            case_id=int(record["case_id"]),
            snapshot_id=snapshot_id
            if snapshot_id is not None
            else record.get("snapshot_id"),
            provider=str(record.get("provider") or "provider"),
            chain_family="TRON",
            chain_network="mainnet",
            query_range={
                "kind": "provider_request",
                "endpoint": record.get("endpoint"),
                "query": query,
            },
            pagination_cursor=str(cursor) if cursor is not None else None,
            observed_watermark={
                "request_ref": record.get("request_ref"),
                "status_code": status_code,
                "raw_sha256": record.get("raw_sha256"),
                "raw_path": record.get("raw_path"),
                "receipt_sha256": record.get("receipt_sha256"),
                "schema_status": schema_status,
                "schema_assessment_sha256": record.get("schema_assessment_sha256"),
                "provider": watermark,
            },
            retrieval_ts_ms=int(record["retrieval_ts_ms"]),
            completeness="response_recorded" if not gaps else "provider_gap",
            gaps=gaps,
            conflicts=conflicts,
        )
        session.add(row)
        rows.append(row)
    return rows


def store_raw_evidence(
    *,
    root: Path,
    case_id: int,
    provider: str,
    payload: Any,
    retrieval_ts_ms: int | None = None,
    content_kind: str = "json",
) -> dict[str, Any]:
    if case_id <= 0:
        raise EvidenceStoreError("case_id must be positive.")
    provider_key = _safe_segment(provider)
    if not provider_key:
        raise EvidenceStoreError("provider is required.")
    if content_kind != "json":
        raise EvidenceStoreError("Only JSON evidence payloads are supported by this helper.")

    body = canonical_json_bytes(payload)
    digest = sha256_bytes(body)
    ts = retrieval_ts_ms if retrieval_ts_ms is not None else now_ms()
    directory = root / "evidence" / f"case-{case_id}" / provider_key
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{ts}-{digest[:16]}.json"
    path.write_bytes(body)
    return {
        "schema": "trinetra.raw_evidence/1",
        "case_id": case_id,
        "provider": provider_key,
        "retrieval_ts_ms": ts,
        "content_kind": content_kind,
        "sha256": digest,
        "path": str(path),
        "size_bytes": len(body),
    }


def _store_captured_payload(
    *,
    capture: ProviderEvidenceCapture,
    provider: str,
    payload: Any,
    retrieval_ts_ms: int,
) -> dict[str, Any]:
    if capture.case_id is not None:
        return store_raw_evidence(
            root=capture.root,
            case_id=capture.case_id,
            provider=provider,
            payload=payload,
            retrieval_ts_ms=retrieval_ts_ms,
        )
    provider_key = _safe_segment(provider)
    lookup_key = _safe_segment(capture.lookup_ref or "")
    if not provider_key:
        raise EvidenceStoreError("provider is required.")
    if not lookup_key:
        raise EvidenceStoreError("lookup_ref is required for case-less evidence.")
    raw_bytes = canonical_json_bytes(payload)
    digest = sha256_bytes(raw_bytes)
    directory = _capture_directory(capture, provider_key)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{retrieval_ts_ms}-{digest[:16]}.json"
    path.write_bytes(raw_bytes)
    return {
        "schema": "trinetra.raw_evidence/1",
        "case_id": None,
        "lookup_ref": capture.lookup_ref,
        "provider": provider_key,
        "retrieval_ts_ms": retrieval_ts_ms,
        "content_kind": "json",
        "sha256": digest,
        "path": str(path),
        "size_bytes": len(raw_bytes),
    }


def _capture_directory(capture: ProviderEvidenceCapture, provider_key: str) -> Path:
    if capture.case_id is not None:
        subject = f"case-{capture.case_id}"
    else:
        lookup_key = _safe_segment(capture.lookup_ref or "")
        if not lookup_key:
            raise EvidenceStoreError("lookup_ref is required for case-less evidence.")
        subject = f"lookup-{lookup_key}"
    return capture.root / "evidence" / subject / provider_key


def _safe_segment(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip(".-").lower()


def _safe_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[redacted]"
                if any(marker in str(key).lower() for marker in ("key", "token", "secret", "authorization"))
                else _safe_metadata(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_metadata(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _provider_watermark(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return {}
    return dict(_safe_metadata(meta))
