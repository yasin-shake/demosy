"""Cairn Demand lab: a what-if demand and pricing simulator for a fictional outdoor retailer.

A forecasting app: the value is the computation, and it runs in Python
next to the data. Every lever change reruns a Monte Carlo simulation of the
season (thousands of simulated futures with demand, price, and elasticity
uncertainty), a lever sensitivity analysis, a price-response curve, and a
profit surface. The interface and the computation are the same code.

Demonstrates:
- Scenario levers in the sidebar, bound to the URL with ``bind="query-params"``,
  so every scenario is a shareable link, plus a reset callback
- Layout-first loading: ``st.skeleton`` holds each card at its final size while
  ``@st.fragment(parallel=True)`` fragments load, and ``st.cache_data`` makes
  repeat scenarios instant
- ``st.echarts_chart`` for a forecast fan chart, an outcome distribution, a
  tornado chart, a price-response curve, and a clickable profit heatmap whose
  ``on_select`` callback applies the clicked levers
- ``st.metric`` cards with sparklines, a plain-language forecast brief, and a
  scenario comparison with ``ButtonColumn`` row actions and an ECharts boxplot
- ``@st.dialog`` to save scenarios, ``st.download_button``, and a warm
  editorial light/dark theme (serif headings, paper tones, pill buttons) with a
  CVD-checked chart palette

All data is synthetic. Replace ``model.load_history`` with your own query and
fit the ``Product`` parameters from it.
"""

from pathlib import Path

from utils import model, ui

import streamlit as st

STATIC = Path(__file__).parent / "static"

st.set_page_config(
    page_title="Cairn Demand lab",
    page_icon=STATIC / "icon.svg",
    layout="wide",
)

logo_variant = "dark" if st.context.theme.type == "dark" else "light"
st.logo(
    STATIC / f"logo-{logo_variant}.svg", icon_image=STATIC / "icon.svg", size="large"
)

page = st.navigation(
    [
        st.Page(
            "app_pages/forecast.py",
            title="Forecast",
            icon=":material/insights:",
            default=True,
        ),
        st.Page("app_pages/pricing.py", title="Pricing", icon=":material/sell:"),
        st.Page(
            "app_pages/scenarios.py",
            title="Scenarios",
            icon=":material/compare_arrows:",
        ),
    ],
    position="top",
)

# The entrypoint draws the title, the product toolbar, and the levers, so every
# page shares them. Each lever is bound to the URL.
with st.container(horizontal=True, vertical_alignment="center"):
    with st.container(gap=None):
        st.title(page.title, icon=page.icon, anchor=False)
        summary = st.empty()
    save = st.button("Save scenario", icon=":material/bookmark_add:", key="save")
    st.button(
        "Reset levers",
        icon=":material/restart_alt:",
        type="tertiary",
        on_click=ui.reset_levers,
        help="Back to the plan: list price, no promo, planned marketing and supply",
    )

product = st.segmented_control(
    "Product line",
    list(model.PRODUCTS),
    default="Jackets",
    required=True,
    key="product",
    bind="query-params",
    on_change=ui.reset_levers,
    label_visibility="collapsed",
)
plan = ui.plan_settings(product)
item = model.PRODUCTS[product]

with st.sidebar:
    st.markdown("**Scenario levers**")
    st.slider(
        "Price vs. list",
        min_value=model.PRICE_RANGE[0],
        max_value=model.PRICE_RANGE[1],
        value=plan["price"],
        step=1,
        format="%+d%%",
        key="price",
        bind="query-params",
        help=f"List price is ${item.price:,.0f}.",
    )
    promo = st.slider(
        "Promo discount",
        min_value=model.PROMO_RANGE[0],
        max_value=model.PROMO_RANGE[1],
        value=plan["promo"],
        step=5,
        format="%d%% off",
        key="promo",
        bind="query-params",
    )
    st.select_slider(
        "Promo weeks",
        model.WEEK_LABELS,
        value=plan["window"],
        key="window",
        bind="query-params",
        disabled=promo == 0,
        help="Week starting dates. Black Friday falls in the week of Nov 23.",
    )
    st.slider(
        "Marketing per week",
        min_value=model.MARKETING_RANGE[0],
        max_value=model.MARKETING_RANGE[1],
        value=plan["marketing"],
        step=2.5,
        format="$%.1fk",
        key="marketing",
        bind="query-params",
        help=f"The plan spends ${item.marketing:g}k per week.",
    )
    st.slider(
        "Weekly supply",
        min_value=model.SUPPLY_RANGE[0],
        max_value=model.SUPPLY_RANGE[1],
        value=plan["supply"],
        step=5,
        format="%d%% of plan",
        key="supply",
        bind="query-params",
        help=f"The plan supplies {item.supply:,} units per week.",
    )
    with st.expander("Model settings", icon=":material/tune:"):
        st.segmented_control(
            "Demand uncertainty",
            list(model.UNCERTAINTY),
            default="Normal",
            required=True,
            key="uncertainty",
            bind="query-params",
        )
        st.segmented_control(
            "Simulations",
            list(model.SIMULATIONS),
            default="5k",
            required=True,
            key="simulations",
            bind="query-params",
            help="Simulated seasons per scenario. More is smoother and slower.",
        )
    st.caption("The levers are saved in the URL, so you can share this scenario.")
    st.caption(
        "Unofficial internal demo styled after Barclays' brand — not an "
        "official Barclays product or endorsement."
    )

levers = ui.current_levers()
summary.caption(
    f"{item.description} · {levers.describe()} · 26 weeks from {model.AS_OF:%b {model.DAY}, %Y}"
)
if save:
    ui.save_scenario_dialog(levers)

page.run()
