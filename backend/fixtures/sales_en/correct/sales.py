"""Sales summary per product."""

from __future__ import annotations

import re

_DIGITS = re.compile(r"[0-9]+")


def summarize_sales(text: str) -> dict[str, int]:
    """Return the total sold per product, in cents.

    Each line is `product,quantity,unit_price_cents`. Blank lines and comments (`#`) are skipped.
    Invalid lines raise ValueError mentioning the 1-based line number.
    """
    totals: dict[str, int] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 3:
            raise ValueError(f"line {number}: expected 3 fields, got {len(fields)}")
        product, quantity, price = fields
        if not product:
            raise ValueError(f"line {number}: empty product name")
        if not _DIGITS.fullmatch(quantity) or not _DIGITS.fullmatch(price):
            raise ValueError(f"line {number}: quantity and price must be non-negative integers")
        key = product.lower()
        totals[key] = totals.get(key, 0) + int(quantity) * int(price)
    return totals
