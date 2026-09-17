from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlmodel import Session

from app.models import SourceCoverage
from app.services.time import now_ms

ValueBucket = Literal["custody", "stationary", "deferred", "fees_loss", "unresolved"]


class AccountingError(ValueError):
    pass


@dataclass(frozen=True)
class ValueOutcome:
    asset_identifier: str
    amount_base: int
    bucket: ValueBucket
    evidence_ref: str


def conservation_report(
    seed_amounts: dict[str, int],
    outcomes: list[ValueOutcome],
) -> dict[str, dict[str, int]]:
    if any(amount < 0 for amount in seed_amounts.values()):
        raise AccountingError("Seed amounts must be non-negative.")

    report = {
        asset: {
            "seed": amount,
            "custody": 0,
            "stationary": 0,
            "deferred": 0,
            "fees_loss": 0,
            "unresolved": 0,
            "assigned": 0,
        }
        for asset, amount in seed_amounts.items()
    }
    seen_refs: set[str] = set()
    for outcome in outcomes:
        if outcome.amount_base < 0:
            raise AccountingError("Outcome amount cannot be negative.")
        if outcome.asset_identifier not in report:
            raise AccountingError(f"Outcome asset has no seed basis: {outcome.asset_identifier}")
        if not outcome.evidence_ref:
            raise AccountingError("Outcome evidence reference is required.")
        if outcome.evidence_ref in seen_refs:
            raise AccountingError(f"Outcome evidence assigned twice: {outcome.evidence_ref}")
        seen_refs.add(outcome.evidence_ref)
        report[outcome.asset_identifier][outcome.bucket] += outcome.amount_base
        report[outcome.asset_identifier]["assigned"] += outcome.amount_base

    for asset, row in report.items():
        if row["assigned"] != row["seed"]:
            raise AccountingError(
                "Attributed value for "
                f"{asset} is not conserved: seed={row['seed']} assigned={row['assigned']}"
            )
    return report


def persist_conservation_report(
    session: Session,
    *,
    case_id: int,
    snapshot_id: int | None,
    chain_family: str,
    chain_network: str,
    report: dict[str, dict[str, int]],
    provider: str = "trace-accounting",
) -> list[SourceCoverage]:
    rows: list[SourceCoverage] = []
    ts = now_ms()
    for asset_identifier, totals in sorted(report.items()):
        assigned = int(totals.get("assigned", -1))
        seed = int(totals.get("seed", -2))
        completeness = "balanced" if assigned == seed else "unbalanced"
        row = SourceCoverage(
            case_id=case_id,
            snapshot_id=snapshot_id,
            provider=provider,
            chain_family=chain_family,
            chain_network=chain_network,
            query_range={
                "kind": "per_asset_conservation",
                "asset_identifier": asset_identifier,
            },
            observed_watermark={"totals": dict(totals)},
            retrieval_ts_ms=ts,
            completeness=completeness,
            gaps=[] if completeness == "balanced" else [{"reason": "not_conserved"}],
            conflicts=[],
        )
        session.add(row)
        rows.append(row)
    session.commit()
    for row in rows:
        session.refresh(row)
    return rows
