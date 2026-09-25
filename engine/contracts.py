from __future__ import annotations

from typing import NotRequired, TypedDict


class NormalizedTransferRecord(TypedDict):
    """Dictionary contract emitted by provider transfer normalizers.

    Provider ordinals may arrive as integers or numeric strings. Provenance
    fields are optional because directly supplied fixture/provider rows do not
    always pass through the evidence-capture layer first.
    """

    txid: str
    ts_ms: int
    source: str
    destination: str
    amount_base: int
    token: str | None
    decimals: int
    contract: str | None
    block: int | str | None
    event_index: int | str | None
    retrieval_ts_ms: NotRequired[int]
    raw_sha256: NotRequired[str]
    provider_request_ref: NotRequired[str]
