"""Number formatting shared by the answers, the charts, and the canvas."""

from __future__ import annotations

import os

# strftime's "no leading zero" flag is "-" on Linux/macOS (glibc) and "#" on
# Windows (MSVCRT); "%-d" raises ValueError there. Use this in a nested format
# spec, e.g. f"{dt:%b {DAY}}", to get "Mar 5" on every platform.
DAY = "%#d" if os.name == "nt" else "%-d"


def money(value: float) -> str:
    """Dollars, compact: $2.24M, $162k, $9,840."""
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1_000_000:
        return f"{sign}${value / 1_000_000:.2f}M"
    if value >= 10_000:
        return f"{sign}${value / 1_000:.0f}k"
    return f"{sign}${value:,.0f}"


def signed_money(value: float) -> str:
    return f"+{money(value)}" if value >= 0 else f"−{money(-value)}"


def pct(value: float, *, signed: bool = True) -> str:
    """A fraction as a percent with one decimal, such as +2.4% or −5.8%."""
    if not signed:
        return f"{abs(value):.1%}"
    return f"+{value:.1%}" if value >= 0 else f"−{-value:.1%}"
