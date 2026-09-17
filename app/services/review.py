from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EvidenceClass = Literal[
    "fund_exposure",
    "service_role",
    "control_identity",
    "participation",
    "privacy_boundary",
    "correlation_candidate",
]


@dataclass(frozen=True)
class EvidenceIndicator:
    evidence_class: EvidenceClass
    label: str
    evidence_id: str
    reason: str
    review_status: Literal["pending", "accepted", "disputed"] = "pending"
    benign_explanation: str | None = None


ClaimClass = Literal[
    "fund_exposure",
    "service_role",
    "control_identity",
    "participation",
    "privacy_boundary",
    "correlation_candidate",
    "probability",
]

ReviewStatus = Literal["supported", "unsupported", "prohibited"]


@dataclass(frozen=True)
class EvidenceClaim:
    claim_class: ClaimClass
    evidence_id: str
    text: str


@dataclass(frozen=True)
class ClaimReview:
    claim: EvidenceClaim
    status: ReviewStatus
    reason: str


def exposure_only(address: str, *, amount_base: int, evidence_id: str) -> EvidenceIndicator:
    return EvidenceIndicator(
        evidence_class="fund_exposure",
        label="case-linked value exposure",
        evidence_id=evidence_id,
        reason=f"{address} received {amount_base} base units linked to the case trace.",
        benign_explanation=(
            "Receipt alone does not establish intent, control relationship or participation."
        ),
    )


def service_role_indicator(provider_key: str, *, evidence_id: str, role: str) -> EvidenceIndicator:
    return EvidenceIndicator(
        evidence_class="service_role",
        label="service role",
        evidence_id=evidence_id,
        reason=f"{provider_key} is identified as {role} for the referenced account or address.",
        benign_explanation="A service role identifies infrastructure or custody, not the offender.",
    )


def privacy_boundary(
    system: Literal["monero", "zcash_shielded", "firo_spark", "lightning", "unknown"],
    *,
    ingress_ref: str,
    note: str,
) -> EvidenceIndicator:
    labels = {
        "monero": "Monero private-transfer boundary",
        "zcash_shielded": "Zcash shielded-pool boundary",
        "firo_spark": "Firo Spark private-transfer boundary",
        "lightning": "Lightning off-chain payment boundary",
        "unknown": "unknown-operation boundary",
    }
    return EvidenceIndicator(
        evidence_class="privacy_boundary",
        label=labels[system],
        evidence_id=ingress_ref,
        reason=note,
        benign_explanation=(
            "The boundary preserves known evidence without inventing a continuous public path."
        ),
    )


def participation_unknown(indicators: list[EvidenceIndicator]) -> bool:
    return not any(item.evidence_class == "participation" for item in indicators)


def validate_claim_references(claims: list[dict], evidence_ids: set[str]) -> list[str]:
    errors: list[str] = []
    for index, claim in enumerate(claims):
        claim_id = str(claim.get("evidence_id") or "")
        if claim_id not in evidence_ids:
            errors.append(f"claim[{index}] references unknown evidence_id {claim_id!r}")
        if _uses_prohibited_language(str(claim.get("text") or "")):
            errors.append(f"claim[{index}] uses prohibited guilt language")
    return errors


def review_evidence_claims(
    claims: list[EvidenceClaim],
    indicators: list[EvidenceIndicator],
    *,
    independent_probability_calibration: bool = False,
) -> list[ClaimReview]:
    evidence_by_id = {indicator.evidence_id: indicator for indicator in indicators}
    reviews: list[ClaimReview] = []
    for claim in claims:
        indicator = evidence_by_id.get(claim.evidence_id)
        if _uses_prohibited_language(claim.text):
            reviews.append(
                ClaimReview(
                    claim=claim,
                    status="prohibited",
                    reason="Claim uses prohibited guilt language.",
                )
            )
            continue
        if claim.claim_class == "probability":
            status: ReviewStatus = (
                "supported" if independent_probability_calibration else "prohibited"
            )
            reviews.append(
                ClaimReview(
                    claim=claim,
                    status=status,
                    reason=(
                        "Independent calibration is required before probability claims are enabled."
                    ),
                )
            )
            continue
        if indicator is None:
            reviews.append(
                ClaimReview(
                    claim=claim,
                    status="unsupported",
                    reason=f"Claim references unknown evidence_id {claim.evidence_id!r}.",
                )
            )
            continue
        if _claim_is_supported_by_indicator(claim, indicator):
            reviews.append(
                ClaimReview(
                    claim=claim,
                    status="supported",
                    reason=(
                        f"{claim.claim_class} claim is supported by "
                        f"{indicator.evidence_class} evidence."
                    ),
                )
            )
        else:
            reviews.append(
                ClaimReview(
                    claim=claim,
                    status="unsupported",
                    reason=(
                        f"{claim.claim_class} claim is not supported by "
                        f"{indicator.evidence_class} evidence."
                    ),
                )
            )
    return reviews


def _claim_is_supported_by_indicator(
    claim: EvidenceClaim,
    indicator: EvidenceIndicator,
) -> bool:
    if claim.claim_class == "correlation_candidate":
        return indicator.evidence_class == "correlation_candidate"
    if claim.claim_class == "service_role":
        return indicator.evidence_class == "service_role"
    if claim.claim_class == "fund_exposure":
        return indicator.evidence_class == "fund_exposure"
    if claim.claim_class == "privacy_boundary":
        return indicator.evidence_class == "privacy_boundary"
    if claim.claim_class == "control_identity":
        return indicator.evidence_class == "control_identity"
    if claim.claim_class == "participation":
        return indicator.evidence_class == "participation"
    return False


def _uses_prohibited_language(text: str) -> bool:
    prohibited = ("guil" + "ty", "fraud" + "ster")
    return any(word in text.lower() for word in prohibited)
