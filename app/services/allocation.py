from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal


class AllocationError(ValueError):
    pass


@dataclass(frozen=True)
class AllocationStep:
    outgoing_attributed_base: int
    remaining_balance_base: int
    remaining_attributed_base: int
    residual_numerator: int


@dataclass(frozen=True)
class OrderedAllocationStep:
    outgoing_base: int
    balance_before: int
    attributed_before: int
    residual_before: int
    numerator_base: int | None
    result: AllocationStep


@dataclass(frozen=True)
class OrderedAllocationResult:
    outgoing_total_base: int
    initial_balance_base: int
    steps: tuple[OrderedAllocationStep, ...]
    remaining_attributed_base: int


@dataclass(frozen=True)
class FrontierCandidate:
    stable_id: str
    attributed_base: int
    event_order: int


def allocate_proportional(
    *,
    balance_base: int,
    attributed_base: int,
    outgoing_base: int,
    residual_numerator: int = 0,
) -> AllocationStep:
    if balance_base <= 0:
        raise AllocationError("Spendable balance must be positive before allocation.")
    if attributed_base < 0 or outgoing_base < 0 or residual_numerator < 0:
        raise AllocationError("Allocation inputs must be non-negative integer base units.")
    if attributed_base > balance_base:
        raise AllocationError("Attributed share cannot exceed spendable balance.")
    if outgoing_base > balance_base:
        raise AllocationError("Outgoing amount cannot exceed spendable balance.")

    if outgoing_base == balance_base:
        return AllocationStep(
            outgoing_attributed_base=attributed_base,
            remaining_balance_base=0,
            remaining_attributed_base=0,
            residual_numerator=0,
        )

    numerator = outgoing_base * attributed_base + residual_numerator
    outgoing_attributed_base = numerator // balance_base
    next_residual = numerator % balance_base
    if outgoing_attributed_base > attributed_base:
        raise AllocationError("Allocation would create attributed value.")

    return AllocationStep(
        outgoing_attributed_base=outgoing_attributed_base,
        remaining_balance_base=balance_base - outgoing_base,
        remaining_attributed_base=attributed_base - outgoing_attributed_base,
        residual_numerator=next_residual,
    )


def allocate_ordered_outgoing(
    outgoing_amounts: Iterable[int],
    *,
    attributed_base: int,
) -> OrderedAllocationResult:
    amounts = tuple(outgoing_amounts)
    outgoing_total_base = sum(amounts)
    initial_balance_base = max(attributed_base, outgoing_total_base)
    balance_base = initial_balance_base
    remaining_attributed_base = attributed_base
    residual_numerator = 0
    steps: list[OrderedAllocationStep] = []

    for outgoing_base in amounts:
        balance_before = balance_base
        attributed_before = remaining_attributed_base
        residual_before = residual_numerator
        numerator_base: int | None = None
        if balance_before <= 0 or attributed_before <= 0:
            result = AllocationStep(
                outgoing_attributed_base=0,
                remaining_balance_base=balance_before,
                remaining_attributed_base=attributed_before,
                residual_numerator=residual_before,
            )
        else:
            numerator_base = outgoing_base * attributed_before + residual_before
            result = allocate_proportional(
                balance_base=balance_before,
                attributed_base=attributed_before,
                outgoing_base=outgoing_base,
                residual_numerator=residual_before,
            )
            balance_base = result.remaining_balance_base
            remaining_attributed_base = result.remaining_attributed_base
            residual_numerator = result.residual_numerator
        steps.append(
            OrderedAllocationStep(
                outgoing_base=outgoing_base,
                balance_before=balance_before,
                attributed_before=attributed_before,
                residual_before=residual_before,
                numerator_base=numerator_base,
                result=result,
            )
        )

    return OrderedAllocationResult(
        outgoing_total_base=outgoing_total_base,
        initial_balance_base=initial_balance_base,
        steps=tuple(steps),
        remaining_attributed_base=remaining_attributed_base,
    )


def schedule_candidates(
    candidates: Iterable[FrontierCandidate],
    *,
    strategy: Literal["dominant_fund_flow", "value_weighted"],
    breadth_cap: int,
) -> tuple[list[FrontierCandidate], list[FrontierCandidate]]:
    if breadth_cap < 0:
        raise AllocationError("Breadth cap must be zero or greater.")

    ranked = sorted(candidates, key=lambda item: (-item.attributed_base, item.event_order, item.stable_id))
    if strategy == "dominant_fund_flow":
        scheduled_count = min(1, breadth_cap, len(ranked))
    elif strategy == "value_weighted":
        scheduled_count = min(breadth_cap, len(ranked))
    else:
        raise AllocationError(f"Unknown scheduling strategy: {strategy}")
    return ranked[:scheduled_count], ranked[scheduled_count:]
