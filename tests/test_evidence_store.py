from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
import shutil
import uuid

import pytest

from app.services.evidence_store import EvidenceStoreError, store_raw_evidence
from app.services.hash import sha256_bytes


@pytest.fixture
def local_tmp_dir() -> Iterator[Path]:
    root = Path.cwd() / "var" / f"test-evidence-store-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        if root.is_dir() and root.parent == Path.cwd() / "var":
            shutil.rmtree(root)


def test_raw_evidence_store_writes_canonical_payload_with_hash(local_tmp_dir: Path) -> None:
    record = store_raw_evidence(
        root=local_tmp_dir,
        case_id=42,
        provider="TronGrid V1",
        payload={"b": 2, "a": 1},
        retrieval_ts_ms=1234567890,
    )

    path = (
        local_tmp_dir
        / "evidence"
        / "case-42"
        / "trongrid-v1"
        / f"1234567890-{record['sha256'][:16]}.json"
    )
    assert record["path"] == str(path)
    assert path.exists()
    assert json.loads(path.read_text()) == {"a": 1, "b": 2}
    assert sha256_bytes(path.read_bytes()) == record["sha256"]


def test_raw_evidence_store_preserves_explicit_zero_retrieval_timestamp(
    local_tmp_dir: Path,
) -> None:
    record = store_raw_evidence(
        root=local_tmp_dir,
        case_id=42,
        provider="TronGrid V1",
        payload={"a": 1},
        retrieval_ts_ms=0,
    )

    assert record["retrieval_ts_ms"] == 0
    assert Path(record["path"]).name.startswith("0-")


def test_raw_evidence_store_rejects_invalid_scope(local_tmp_dir: Path) -> None:
    with pytest.raises(EvidenceStoreError):
        store_raw_evidence(root=local_tmp_dir, case_id=0, provider="TronGrid", payload={})
    with pytest.raises(EvidenceStoreError):
        store_raw_evidence(root=local_tmp_dir, case_id=1, provider="   ", payload={})
    with pytest.raises(EvidenceStoreError):
        store_raw_evidence(
            root=local_tmp_dir,
            case_id=1,
            provider="TronGrid",
            payload={},
            content_kind="text",
        )
