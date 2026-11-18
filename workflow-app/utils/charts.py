"""ECharts options for Cairn Reorder desk.

The theme in ``.streamlit/config.toml`` colors the charts: series take the
``chartCategoricalColors`` palette in order. Decision outcomes are statuses,
so they use the theme's semantic colors instead, the same ones as their
badges in the tables (``ui.ACTION_BADGES``).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

import streamlit as st
from utils.store import DAY

LEGEND = {"top": 0, "right": 6, "icon": "roundRect", "itemGap": 16}
# The theme color option of each decision outcome.
ACTION_COLORS = {
    "Approved": "greenColor",
    "Approved with changes": "blueColor",
    "Rejected": "orangeColor",
    "Held": "grayColor",
}


def theme(option: str) -> Any:
    """A color option of the active light or dark theme, such as ``"grayColor"``."""
    mode = "dark" if st.context.theme.type == "dark" else "light"
    return st.get_option(f"theme.{mode}.{option}")


def stock_projection(proposal: pd.Series, order_qty: int) -> dict[str, Any]:
    """Projected stock for eight weeks, with and without the proposed order."""
    days = np.arange(57)
    daily = proposal["weekly_forecast"] / 7
    open_order_day = 5  # Open orders are already in transit
    arrival_day = int(proposal["lead_time_days"])
    without = (
        proposal["on_hand"]
        + np.where(days >= open_order_day, proposal["on_order"], 0)
        - daily * days
    )
    with_order = without + np.where(days >= arrival_day, order_qty, 0)
    dates = pd.date_range(date.today(), periods=len(days), freq="D")
    data = pd.DataFrame(
        {
            "day": dates,
            "With the order": np.maximum(with_order, 0).round(),
            "Without the order": np.maximum(without, 0).round(),
            "Safety stock": round(proposal["weekly_forecast"]),
        }
    )
    marks: list[dict[str, Any]] = [
        {
            "name": "Arrives",
            "xAxis": (date.today() + timedelta(days=arrival_day)).isoformat(),
        }
    ]
    stockout = np.flatnonzero(without <= 0)
    if stockout.size:
        marks.append(
            {
                "name": "Stockout",
                "xAxis": (date.today() + timedelta(days=int(stockout[0]))).isoformat(),
            }
        )
    return {
        "dataset": {"source": data},
        "legend": LEGEND,
        "grid": {"left": 8, "right": 16, "top": 36, "bottom": 8, "containLabel": True},
        "tooltip": {"trigger": "axis", "valueFormatter": "%,d units"},
        "xAxis": {"type": "time", "splitLine": {"show": False}},
        "yAxis": {"type": "value"},
        "series": [
            {
                "type": "line",
                "name": "With the order",
                "encode": {"x": "day", "y": "With the order"},
                "showSymbol": False,
                "lineStyle": {"width": 2.5},
                "areaStyle": {"opacity": 0.12},
                "markLine": {
                    "silent": True,
                    "symbol": "none",
                    "lineStyle": {"type": "dashed", "color": theme("grayColor")},
                    "label": {"formatter": "{b}", "position": "insideEndTop"},
                    "data": marks,
                },
            },
            {
                "type": "line",
                "name": "Without the order",
                "encode": {"x": "day", "y": "Without the order"},
                "showSymbol": False,
                "lineStyle": {"width": 2, "type": "dashed"},
            },
            {
                "type": "line",
                "name": "Safety stock",
                "encode": {"x": "day", "y": "Safety stock"},
                "showSymbol": False,
                "lineStyle": {"width": 1.5, "type": "dotted"},
            },
        ],
    }


def demand(proposal: pd.Series) -> dict[str, Any]:
    """Twelve weeks of units sold and the agent's six-week forecast."""
    history, forecast = proposal["history"], proposal["forecast"]
    start = pd.Timestamp(date.today()) - pd.Timedelta(weeks=len(history))
    weeks = pd.date_range(start, periods=len(history) + len(forecast), freq="7D")
    data = pd.DataFrame(
        {
            "week": [f"{week:%b {DAY}}" for week in weeks],
            "Sold": [*history] + [None] * len(forecast),
            "Forecast": [None] * (len(history) - 1) + [history[-1], *forecast],
        }
    )
    return {
        "dataset": {"source": data},
        "legend": LEGEND,
        "grid": {"left": 8, "right": 16, "top": 36, "bottom": 8, "containLabel": True},
        "tooltip": {"trigger": "axis", "valueFormatter": "%,d units"},
        "xAxis": {"type": "category", "axisTick": {"show": False}},
        "yAxis": {"type": "value"},
        "series": [
            {
                "type": "bar",
                "name": "Sold",
                "encode": {"x": "week", "y": "Sold"},
                # Past sales stay neutral so the forecast stands out.
                "itemStyle": {"color": theme("grayColor"), "opacity": 0.55},
                "barCategoryGap": "30%",
            },
            {
                "type": "line",
                "name": "Forecast",
                "encode": {"x": "week", "y": "Forecast"},
                "symbolSize": 6,
                "lineStyle": {"width": 2.5, "type": "dashed"},
            },
        ],
    }


def decisions_per_day(decisions: pd.DataFrame) -> dict[str, Any]:
    """Decisions per day, stacked by outcome."""
    counts = (
        decisions.assign(day=decisions["decided_at"].dt.normalize())
        .pivot_table(
            index="day", columns="action", values="proposal_id", aggfunc="count"
        )
        .reindex(columns=list(ACTION_COLORS))
        .fillna(0)
        .reset_index()
    )
    return {
        "dataset": {"source": counts},
        "legend": LEGEND,
        "grid": {"left": 8, "right": 16, "top": 36, "bottom": 8, "containLabel": True},
        "tooltip": {"trigger": "axis", "valueFormatter": "%d proposals"},
        "xAxis": {"type": "time", "splitLine": {"show": False}},
        "yAxis": {"type": "value"},
        "series": [
            {
                "type": "bar",
                "name": action,
                "stack": "decisions",
                "encode": {"x": "day", "y": action},
                "itemStyle": {"color": theme(color)},
                "barMaxWidth": 18,
            }
            for action, color in ACTION_COLORS.items()
        ],
    }


def acceptance_by_category(decisions: pd.DataFrame) -> dict[str, Any]:
    """Share of each category's proposals approved unchanged."""
    shares = (
        decisions.assign(accepted=decisions["action"] == "Approved")
        .groupby("category")["accepted"]
        .mean()
        .sort_values()
        .mul(100)
        .round()
        .reset_index()
    )
    return {
        "dataset": {"source": shares},
        "grid": {"left": 8, "right": 40, "top": 8, "bottom": 8, "containLabel": True},
        "tooltip": {"trigger": "axis", "valueFormatter": "%.0f%% approved unchanged"},
        "xAxis": {"type": "value", "max": 100, "axisLabel": {"formatter": "{value}%"}},
        "yAxis": {"type": "category", "axisTick": {"show": False}},
        "series": [
            {
                "type": "bar",
                "encode": {"x": "accepted", "y": "category"},
                "barMaxWidth": 22,
                "label": {
                    "show": True,
                    "position": "right",
                    "formatter": "{@accepted}%",
                },
            }
        ],
    }
