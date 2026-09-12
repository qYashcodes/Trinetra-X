from __future__ import annotations

from decimal import Decimal


def format_amount(amount_base: int, decimals: int = 6, symbol: str = "USDT") -> str:
    quantum = Decimal(10) ** -decimals
    amount = (Decimal(amount_base) / (Decimal(10) ** decimals)).quantize(quantum)
    if decimals >= 2:
        rendered = f"{amount:,.2f}"
    else:
        rendered = f"{amount:,}"
    return f"{rendered} {symbol}"


def format_millions(amount_base: int, decimals: int = 6) -> str:
    amount = Decimal(amount_base) / (Decimal(10) ** decimals)
    return f"{amount / Decimal(1_000_000):.2f}"


def basis_points(amount_base: int, total_base: int) -> int:
    if total_base <= 0:
        return 0
    tenths_percent = (amount_base * 1000 + total_base // 2) // total_base
    return tenths_percent * 10
