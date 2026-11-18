"""Shared UI pieces for Cairn Demand lab: levers, cards, formatting, and the brief."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, TypedDict

import numpy as np

import streamlit as st
from utils import model

if TYPE_CHECKING:
    import pandas as pd

    from streamlit.delta_generator import DeltaGenerator


def money(value: float) -> str:
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e6:
        return f"{sign}${value / 1e6:.2f}M"
    if value >= 1e3:
        return f"{sign}${value / 1e3:.0f}k"
    return f"{sign}${value:.0f}"


def signed_money(value: float) -> str:
    return f"+{money(value)}" if value >= 0 else money(value)


def pct_change(new: float, old: float) -> float:
    return (new - old) / abs(old) * 100 if old else 0.0


def signed_pct(value: float) -> str:
    return f"{value:+.1f}%"


# Widget keys of the levers. They're bound to the URL, so every scenario is a link.
LEVER_KEYS = ("price", "promo", "window", "marketing", "supply")


class LeverSettings(TypedDict):
    price: int
    promo: int
    window: tuple[str, str]
    marketing: float
    supply: int


def plan_settings(product: str) -> LeverSettings:
    """Widget values of the plan for ``product``."""
    start, end = model.DEFAULT_WINDOW
    return {
        "price": 0,
        "promo": 0,
        "window": (model.WEEK_LABELS[start], model.WEEK_LABELS[end]),
        "marketing": float(model.PRODUCTS[product].marketing),
        "supply": 100,
    }


def reset_levers() -> None:
    # Bound widgets are reset by assigning their keys in a callback.
    for key, value in plan_settings(st.session_state.product).items():
        st.session_state[key] = value


def apply_levers(levers: model.Levers) -> None:
    """Set the lever widgets to ``levers``. Call it from a callback."""
    st.session_state.product = levers.product
    st.session_state.price = levers.price_change
    st.session_state.promo = levers.promo_discount
    st.session_state.window = tuple(
        model.WEEK_LABELS[week] for week in levers.promo_window
    )
    st.session_state.marketing = levers.marketing
    st.session_state.supply = levers.supply


def current_levers() -> model.Levers:
    """The scenario set by the lever widgets in the entrypoint."""
    state = st.session_state
    start, end = state.window
    return model.Levers(
        product=state.product,
        price_change=int(state.price),
        promo_discount=int(state.promo),
        promo_window=(model.WEEK_LABELS.index(start), model.WEEK_LABELS.index(end)),
        marketing=float(state.marketing),
        supply=int(state.supply),
        uncertainty=state.uncertainty,
        simulations=model.SIMULATIONS[state.simulations],
    )


def card_header(
    title: str, caption: str | None = None, *, help: str | None = None
) -> DeltaGenerator:
    """Render a card title row and return a right-aligned container for controls."""
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        with st.container(gap=None):
            st.markdown(f"**{title}**", help=help)
            if caption:
                st.caption(caption)
        controls = st.container(
            horizontal=True,
            horizontal_alignment="right",
            vertical_alignment="center",
            width="content",
        )
    return controls


def default_scenarios(product: str) -> list[tuple[str, model.Levers]]:
    """Example scenarios for a product line, so the comparison page isn't empty."""
    levers = model.Levers(product=product, marketing=model.PRODUCTS[product].marketing)
    spend = model.PRODUCTS[product].marketing
    return [
        ("Plan", levers),
        (
            "Holiday promo",
            replace(levers, promo_discount=20, marketing=spend * 1.4, supply=115),
        ),
        ("Premium price", replace(levers, price_change=8, marketing=spend * 1.2)),
    ]


def saved_scenarios(product: str) -> list[tuple[str, model.Levers]]:
    """The scenarios saved for ``product`` in this session, in the order saved."""
    scenarios = st.session_state.setdefault("scenarios", {})
    if product not in scenarios:
        scenarios[product] = default_scenarios(product)
    return scenarios[product]


@st.dialog("Save scenario", icon=":material/bookmark_add:")
def save_scenario_dialog(levers: model.Levers) -> None:
    summary = model.simulate(levers).summary
    st.caption(levers.describe())
    st.markdown(
        f"Median profit **{money(summary.profit)}** on **{money(summary.revenue)}** "
        f"revenue, with a **{summary.stockout_risk:.0%}** stockout risk."
    )
    # A form submits on Enter or on the button, with the name already applied.
    with st.form("save_scenario_form", border=False):
        name = st.text_input(
            "Name",
            placeholder="For example, Black Friday push",
            max_chars=40,
            required=True,
            key="scenario_name",
        )
        submitted = st.form_submit_button(
            "Save", type="primary", icon=":material/check:", key="save_confirm"
        )
    if submitted and name.strip():
        saved_scenarios(levers.product).append((name.strip(), levers))
        st.toast(f"Saved **{name.strip()}**", icon=":material/bookmark_added:")
        st.rerun()


def brief(levers: model.Levers) -> list[tuple[str, str]]:
    """A plain-language read of the scenario, as (icon, Markdown) pairs.

    Every sentence comes from the same simulations as the charts: the plan,
    the scenario without its promo, and the lever sensitivity.
    """
    forecast = model.simulate(levers)
    summary = forecast.summary
    plan = model.simulate(model.plan_for(levers)).summary
    target = model.revenue_target(levers)
    lines: list[tuple[str, str]] = []

    profit_change = pct_change(summary.profit, plan.profit)
    if abs(profit_change) < 0.5:
        headline = f"Profit is in line with plan at **{money(summary.profit)}**"
    else:
        direction = "up" if profit_change > 0 else "down"
        color = "green" if profit_change > 0 else "red"
        headline = (
            f"Profit is :{color}[**{direction} {abs(profit_change):.1f}%**] vs. plan "
            f"at **{money(summary.profit)}**"
        )
    p10, p90 = np.percentile(forecast.outcomes["units"], [10, 90])
    lines.append(
        (
            ":material/insights:",
            f"{headline}. The median season sells **{summary.units:,.0f} units** "
            f"(80% range {p10:,.0f}–{p90:,.0f}).",
        )
    )

    if levers.promo_discount:
        without = model.simulate(replace(levers, promo_discount=0)).summary
        promo_effect = summary.profit - without.profit
        start, end = (model.WEEK_LABELS[week] for week in levers.promo_window)
        window = start if start == end else f"{start} to {end}"
        verdict = (
            "pays for its discount"
            if promo_effect > 0
            else "doesn't pay for its discount: part of its lift is demand pulled "
            "forward from the following weeks"
        )
        lines.append(
            (
                ":material/sell:",
                f"The **{levers.promo_discount}% promo** ({window}) changes profit by "
                f"**{signed_money(promo_effect)}**. It {verdict}.",
            )
        )

    if summary.stockout_weeks:
        weeks = ", ".join(model.WEEK_LABELS[week] for week in summary.stockout_weeks)
        lines.append(
            (
                ":material/inventory_2:",
                f"Demand likely outruns supply in the weeks of **{weeks}**. The "
                f"median season leaves **{summary.lost_units:,.0f} units** of demand "
                "unmet.",
            )
        )
    elif summary.stockout_risk >= 0.25:
        lines.append(
            (
                ":material/inventory_2:",
                f"There's a **{summary.stockout_risk:.0%} chance** of selling out in "
                "at least one week.",
            )
        )
    else:
        lines.append(
            (
                ":material/inventory_2:",
                "Supply covers demand in almost every simulated season.",
            )
        )

    beat = float((forecast.outcomes["revenue"] >= target).mean())
    lines.append(
        (
            ":material/flag:",
            f"**{beat:.0%} chance** that revenue reaches the **{money(target)}** target.",
        )
    )

    moves = model.sensitivity(levers)
    best = moves.melt(
        id_vars=["lever", "low_label", "high_label"],
        value_vars=["low", "high"],
        var_name="direction",
        value_name="profit",
    ).sort_values("profit", ascending=False)
    top = best.iloc[0]
    if top["profit"] > 0.002 * abs(summary.profit):
        setting = top["low_label"] if top["direction"] == "low" else top["high_label"]
        lines.append(
            (
                ":material/lightbulb:",
                f"Biggest single upside: set **{top['lever'].lower()} to {setting}** "
                f"for **{signed_money(top['profit'])}** of median profit.",
            )
        )
    else:
        lines.append(
            (
                ":material/lightbulb:",
                "No single lever move adds meaningful profit: this scenario is close "
                "to the best these levers allow.",
            )
        )
    return lines


def week_range_label(weekly: pd.DataFrame) -> str:
    first, last = weekly["week"].iloc[0], weekly["week"].iloc[-1]
    return f"{first:%b {model.DAY}, %Y} – {last:%b {model.DAY}, %Y}"
