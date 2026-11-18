"""Demand model, Monte Carlo simulation, and pricing analysis for Cairn Demand lab.

Everything here is plain NumPy and pandas, and it runs next to the data. To
use your own data, replace ``load_history`` with a warehouse query and fit the
``Product`` parameters from it. The pages only call the cached functions at
the bottom of this module.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import NamedTuple

import numpy as np
import pandas as pd

import streamlit as st

# Simulates warehouse latency on the first (uncached) load so the skeleton
# loading states are visible. Set to 0 when wiring up a real data source.
SIMULATED_LATENCY_S = 0.8

AS_OF = date(2026, 11, 9)  # A Monday: the first forecast week
HORIZON = 26  # Forecast weeks
HISTORY_WEEKS = 78
PRICE_RANGE = (-25, 25)  # Percent vs. list price
PROMO_RANGE = (0, 40)  # Percent off during the promo window
MARKETING_RANGE = (0.0, 60.0)  # $k per week
SUPPLY_RANGE = (70, 130)  # Percent of planned weekly supply
UNCERTAINTY = {"Low": (0.05, 0.07), "Normal": (0.09, 0.11), "High": (0.15, 0.17)}
SIMULATIONS = {"1k": 1_000, "5k": 5_000, "20k": 20_000}
DEFAULT_WINDOW = (2, 3)  # The weeks of Nov 23 and Nov 30: Black Friday and Cyber Monday
# Shoppers react more strongly to a visible promo than to a list-price change.
_PROMO_SENSITIVITY = 1.3
# Part of a promo's lift is demand pulled forward from the following weeks.
_PULL_FORWARD_WEEKS = 2
_PULL_FORWARD = 0.6
_MARKETING_LIFT = 0.5  # Lift per log-unit of marketing spend
_ELASTICITY_SPREAD = 0.12  # Uncertainty in the fitted elasticity (log scale)


@dataclass(frozen=True)
class Product:
    """A product line and its fitted demand parameters."""

    name: str
    description: str
    price: float  # List price
    unit_cost: float
    base_units: float  # Off-season weekly demand at list price
    elasticity: float  # Price elasticity of demand
    # (ISO week, height, width) of each seasonal peak.
    peaks: tuple[tuple[int, float, float], ...]
    supply: int  # Planned weekly supply in units
    marketing: float  # Planned marketing spend, $k per week
    growth: float  # Year-over-year demand growth
    seed: int


PRODUCTS = {
    product.name: product
    for product in [
        Product(
            name="Jackets",
            description="Insulated jackets",
            price=229,
            unit_cost=96,
            base_units=880,
            elasticity=-1.7,
            peaks=((48, 2.4, 1.6), (51, 1.5, 1.8), (2, 0.7, 2.5), (45, 0.8, 3)),
            supply=3_500,
            marketing=25,
            growth=0.09,
            seed=11,
        ),
        Product(
            name="Boots",
            description="Hiking boots",
            price=179,
            unit_cost=71,
            base_units=640,
            elasticity=-1.6,
            peaks=((48, 1.4, 1.8), (16, 1.0, 4), (36, 0.7, 4), (51, 0.8, 1.5)),
            supply=1_700,
            marketing=15,
            growth=0.05,
            seed=23,
        ),
        Product(
            name="Tents",
            description="Backpacking tents",
            price=349,
            unit_cost=138,
            base_units=240,
            elasticity=-1.9,
            peaks=((21, 2.6, 5), (48, 1.1, 1.5), (13, 0.9, 3)),
            supply=850,
            marketing=10,
            growth=0.12,
            seed=37,
        ),
        Product(
            name="Daypacks",
            description="Daypacks",
            price=89,
            unit_cost=38,
            base_units=1_350,
            elasticity=-1.9,
            peaks=((33, 1.8, 4), (24, 1.0, 6), (48, 1.2, 1.5), (51, 0.6, 1.5)),
            supply=3_600,
            marketing=10,
            growth=0.07,
            seed=41,
        ),
    ]
}


@dataclass(frozen=True)
class Levers:
    """One what-if scenario: a product line and the levers a planner can pull."""

    product: str
    price_change: int = 0
    promo_discount: int = 0
    promo_window: tuple[int, int] = DEFAULT_WINDOW
    marketing: float = 0.0
    supply: int = 100
    uncertainty: str = "Normal"
    simulations: int = 5_000

    def describe(self) -> str:
        parts = [
            f"Price {self.price_change:+d}%" if self.price_change else "List price"
        ]
        if self.promo_discount:
            start, end = (WEEK_LABELS[week] for week in self.promo_window)
            window = start if start == end else f"{start}–{end}"
            parts.append(f"{self.promo_discount}% promo {window}")
        parts.append(f"${self.marketing:g}k/wk marketing")
        if self.supply != 100:
            parts.append(f"supply {self.supply}%")
        return " · ".join(parts)


class Summary(NamedTuple):
    """Season totals of a forecast. Money and units are medians (P50)."""

    units: float
    revenue: float
    profit: float
    margin: float
    stockout_risk: float  # Chance that demand outstrips supply in some week
    lost_units: float
    stockout_weeks: tuple[int, ...]  # Weeks where a stockout is more likely than not


@dataclass(frozen=True)
class Forecast:
    weekly: pd.DataFrame  # One row per forecast week
    outcomes: pd.DataFrame  # One row per simulated season
    summary: Summary


def forecast_weeks() -> pd.DatetimeIndex:
    return pd.date_range(AS_OF, periods=HORIZON, freq="7D")


# strftime's "no leading zero" flag is "-" on Linux/macOS (glibc) and "#" on
# Windows (MSVCRT); "%-d" raises ValueError there. Use this in a nested format
# spec, e.g. f"{dt:%b {DAY}}", to get "Mar 5" on every platform.
DAY = "%#d" if os.name == "nt" else "%-d"

WEEK_LABELS = [week.strftime(f"%b {DAY}") for week in forecast_weeks()]


def plan_for(levers: Levers) -> Levers:
    """The plan with the same model settings as ``levers``, to compare against."""
    return replace(
        levers,
        price_change=0,
        promo_discount=0,
        promo_window=DEFAULT_WINDOW,
        marketing=PRODUCTS[levers.product].marketing,
        supply=100,
    )


def _seasonality(product: Product, weeks: pd.DatetimeIndex) -> np.ndarray:
    week_of_year = weeks.isocalendar().week.to_numpy(dtype=float)
    factor = np.full(len(weeks), 0.55)
    for peak, height, width in product.peaks:
        distance = np.abs(week_of_year - peak)
        distance = np.minimum(distance, 52 - distance)
        factor += height * np.exp(-0.5 * (distance / width) ** 2)
    return factor


def _trend(product: Product, weeks: pd.DatetimeIndex) -> np.ndarray:
    years = np.asarray((weeks - pd.Timestamp(AS_OF)) / pd.Timedelta(days=365.25))
    return (1 + product.growth) ** years


def _marketing_response(spend: float) -> float:
    return 1 + _MARKETING_LIFT * np.log1p(spend / 10)


def expected_demand(product: Product, weeks: pd.DatetimeIndex) -> np.ndarray:
    """Weekly demand at list price, with planned marketing and no promo."""
    return product.base_units * _seasonality(product, weeks) * _trend(product, weeks)


@st.cache_data(ttl="1h", show_spinner="Loading sales history...")
def load_history(product: str) -> pd.DataFrame:
    """Weekly units and revenue over the last 78 weeks, including last year's promos."""
    time.sleep(SIMULATED_LATENCY_S)
    item = PRODUCTS[product]
    weeks = pd.date_range(
        AS_OF - timedelta(weeks=HISTORY_WEEKS), periods=HISTORY_WEEKS, freq="7D"
    )
    rng = np.random.default_rng(item.seed + 1_000)
    demand = expected_demand(item, weeks) * np.exp(rng.normal(-0.005, 0.1, len(weeks)))
    # Last year's Black Friday and spring sales, at 25% and 15% off.
    promos = {
        date(2025, 11, 24): 0.25,
        date(2025, 12, 1): 0.25,
        date(2026, 4, 20): 0.15,
    }
    discount = np.zeros(len(weeks))
    for week, off in promos.items():
        discount[weeks == pd.Timestamp(week)] = off
    demand *= (1 - discount) ** (item.elasticity * _PROMO_SENSITIVITY)
    units = np.minimum(demand, item.supply * 0.95).round()
    revenue = units * item.price * (1 - discount)
    return pd.DataFrame({"week": weeks, "units": units, "revenue": revenue})


class _Run(NamedTuple):
    demand: np.ndarray  # (simulations, weeks)
    sales: np.ndarray
    revenue: np.ndarray
    profit: np.ndarray
    supply: float


def _random_draws(product: Product, simulations: int) -> tuple[np.ndarray, ...]:
    # Common random numbers: every scenario of a product line sees the same
    # simulated futures, so differences between scenarios come from the levers
    # alone and not from sampling noise.
    rng = np.random.default_rng(product.seed)
    return (
        rng.standard_normal(simulations),
        rng.standard_normal(simulations),
        rng.standard_normal((simulations, HORIZON)),
    )


def _run(levers: Levers, simulations: int | None = None) -> _Run:
    product = PRODUCTS[levers.product]
    simulations = simulations or levers.simulations
    z_market, z_elasticity, z_week = _random_draws(product, simulations)
    weeks = forecast_weeks()
    horizon = np.arange(HORIZON)
    market_sigma, week_sigma = UNCERTAINTY[levers.uncertainty]

    elasticity = product.elasticity * np.exp(_ELASTICITY_SPREAD * z_elasticity)
    price_factor = (1 + levers.price_change / 100) ** elasticity

    discount = levers.promo_discount / 100
    start, end = levers.promo_window
    in_promo = (horizon >= start) & (horizon <= end) if discount else horizon < 0
    after_promo = (horizon > end) & (horizon <= end + _PULL_FORWARD_WEEKS)
    promo_factor = np.where(
        in_promo, (1 - discount) ** (elasticity[:, None] * _PROMO_SENSITIVITY), 1.0
    )
    promo_factor = np.where(after_promo, 1 - _PULL_FORWARD * discount, promo_factor)
    marketing_factor = _marketing_response(levers.marketing) / _marketing_response(
        product.marketing
    )

    # Mean-one lognormal noise: a market shock per simulated season, plus weekly
    # noise that widens with the forecast horizon.
    week_scale = week_sigma * np.sqrt(1 + horizon / 10)
    noise = np.exp(
        market_sigma * z_market[:, None]
        - market_sigma**2 / 2
        + week_scale * z_week
        - week_scale**2 / 2
    )
    demand = (
        expected_demand(product, weeks)
        * price_factor[:, None]
        * promo_factor
        * marketing_factor
        * noise
    )
    supply = product.supply * levers.supply / 100
    sales = np.minimum(demand, supply)
    price = product.price * (1 + levers.price_change / 100)
    unit_price = np.where(in_promo, price * (1 - discount), price)
    revenue = sales * unit_price
    profit = revenue - sales * product.unit_cost - levers.marketing * 1_000
    return _Run(demand, sales, revenue, profit, supply)


def _summarize(run: _Run) -> Summary:
    revenue = run.revenue.sum(axis=1)
    profit = run.profit.sum(axis=1)
    short = run.demand > run.supply
    return Summary(
        units=float(np.median(run.sales.sum(axis=1))),
        revenue=float(np.median(revenue)),
        profit=float(np.median(profit)),
        margin=float(np.median(profit / revenue)),
        stockout_risk=float(short.any(axis=1).mean()),
        lost_units=float(np.median((run.demand - run.sales).sum(axis=1))),
        stockout_weeks=tuple(
            int(week) for week in np.flatnonzero(short.mean(axis=0) > 0.5)
        ),
    )


@st.cache_data(max_entries=256, show_spinner=False)
def simulate(levers: Levers) -> Forecast:
    """Simulate the season under ``levers`` and return weekly bands and outcomes."""
    run = _run(levers)
    demand = np.percentile(run.demand, [10, 25, 50, 75, 90], axis=0)
    revenue = np.percentile(run.revenue, [10, 25, 50, 75, 90], axis=0)
    weekly = pd.DataFrame(
        {
            "week": forecast_weeks(),
            "label": WEEK_LABELS,
            **{f"units_p{q}": demand[i] for i, q in enumerate([10, 25, 50, 75, 90])},
            **{f"revenue_p{q}": revenue[i] for i, q in enumerate([10, 25, 50, 75, 90])},
            "sales_p50": np.median(run.sales, axis=0),
            "profit_p50": np.median(run.profit, axis=0),
            "stockout_risk": (run.demand > run.supply).mean(axis=0),
            "supply": run.supply,
        }
    )
    outcomes = pd.DataFrame(
        {
            "units": run.sales.sum(axis=1),
            "revenue": run.revenue.sum(axis=1),
            "profit": run.profit.sum(axis=1),
        }
    )
    return Forecast(weekly=weekly, outcomes=outcomes, summary=_summarize(run))


def revenue_target(levers: Levers) -> float:
    """The season revenue target: the plan's median revenue plus 4%, rounded."""
    return round(simulate(plan_for(levers)).summary.revenue * 1.04, -4)


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return min(max(value, bounds[0]), bounds[1])


def lever_swings(levers: Levers) -> list[tuple[str, Levers, Levers, str, str]]:
    """Each lever moved down and up from its current setting."""
    price_low, price_high = (
        int(_clamp(levers.price_change + step, PRICE_RANGE)) for step in (-5, 5)
    )
    promo_low, promo_high = (
        int(_clamp(levers.promo_discount + step, PROMO_RANGE)) for step in (-10, 10)
    )
    spend_low, spend_high = (
        _clamp(levers.marketing * factor, MARKETING_RANGE) for factor in (0.5, 1.5)
    )
    supply_low, supply_high = (
        int(_clamp(levers.supply + step, SUPPLY_RANGE)) for step in (-15, 15)
    )
    return [
        (
            "Price",
            replace(levers, price_change=price_low),
            replace(levers, price_change=price_high),
            f"{price_low:+d}%",
            f"{price_high:+d}%",
        ),
        (
            "Promo discount",
            replace(levers, promo_discount=promo_low),
            replace(levers, promo_discount=promo_high),
            f"{promo_low}% off",
            f"{promo_high}% off",
        ),
        (
            "Marketing",
            replace(levers, marketing=spend_low),
            replace(levers, marketing=spend_high),
            f"${spend_low:g}k/wk",
            f"${spend_high:g}k/wk",
        ),
        (
            "Supply",
            replace(levers, supply=supply_low),
            replace(levers, supply=supply_high),
            f"{supply_low}%",
            f"{supply_high}%",
        ),
    ]


@st.cache_data(max_entries=128, show_spinner=False)
def sensitivity(levers: Levers) -> pd.DataFrame:
    """How much median season profit changes when each lever moves."""
    current = simulate(levers).summary.profit
    rows = []
    for name, low, high, low_label, high_label in lever_swings(levers):
        rows.append(
            {
                "lever": name,
                "low_label": low_label,
                "high_label": high_label,
                "low": simulate(low).summary.profit - current,
                "high": simulate(high).summary.profit - current,
            }
        )
    return pd.DataFrame(rows)


@st.cache_data(max_entries=128, show_spinner=False)
def price_response(levers: Levers) -> pd.DataFrame:
    """Median season units, revenue, and profit at every price from -25% to +25%."""
    rows = []
    for change in range(PRICE_RANGE[0], PRICE_RANGE[1] + 1):
        summary = _summarize(_run(replace(levers, price_change=change), 1_000))
        rows.append(
            {
                "price_change": change,
                "price": PRODUCTS[levers.product].price * (1 + change / 100),
                "units": summary.units,
                "revenue": summary.revenue,
                "profit": summary.profit,
                "stockout_risk": summary.stockout_risk,
            }
        )
    return pd.DataFrame(rows)


SURFACE_PRICES = list(range(PRICE_RANGE[0], PRICE_RANGE[1] + 1, 5))
SURFACE_SPEND = [float(spend) for spend in range(0, 61, 5)]


@st.cache_data(max_entries=128, show_spinner=False)
def profit_surface(levers: Levers) -> pd.DataFrame:
    """Median season profit for every price and marketing combination."""
    rows = []
    for spend in SURFACE_SPEND:
        for change in SURFACE_PRICES:
            run = _run(replace(levers, price_change=change, marketing=spend), 500)
            rows.append(
                {
                    "price": f"{change:+d}%",
                    "marketing": f"${spend:g}k",
                    "profit": float(np.median(run.profit.sum(axis=1))),
                    "price_change": change,
                    "spend": spend,
                }
            )
    return pd.DataFrame(rows)
