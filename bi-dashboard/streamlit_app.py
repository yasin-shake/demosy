"""Nimbus BI: a revenue intelligence dashboard for a fictional B2B SaaS company.

Demonstrates:
- Layout-first loading: every page paints its frames, titles, and controls
  first, and ``st.skeleton`` holds each slow card at its final size. Cached
  reruns update cards in place, and slow loads show their spinner text on the
  skeleton. The overview loads its cards concurrently in
  ``@st.fragment(parallel=True)`` fragments; the accounts and pipeline pages
  cross-filter between cards, so they draw every skeleton first and fill
  them once the shared data has loaded.
- A toolbar of global filters bound to the URL with ``bind="query-params"``
  (period, regions and plans in a popover, and chart annotations)
- Certified-metric badges, section SQL popovers, and ``st.menu_button`` card
  menus with definition drawers, SQL, feedback, and chart toggles
- ``st.echarts_chart`` for stacked areas with a forecast and a year-earlier
  comparison, milestone annotations, hatched projections, waterfalls, donuts,
  heatmaps, funnels, sankeys, gauges, and brushable scatter plots, including
  selection-driven cross-filtering
- ``st.dataframe`` with ``ButtonColumn`` row actions, ``MultiselectColumn``
  colored badges, and sparkline / progress columns
- ``@st.dialog(position="right")`` side drawers for account and metric details
- An Ask AI drawer, from the header or any card's ⋮ menu: a simulated
  assistant with ``st.chat_message``, ``st.chat_input``, a compact
  ``st.status`` over a step timeline, ``st.write_stream``, and ``st.feedback``.
  It answers from the same data and filters as the cards, without a model.
- ``streamlit-pivot`` (a Components v2 package) in a large ``@st.dialog``
  for drag-and-drop pivoting and cell drill-down of the accounts and deals
  tables
- Dual light/dark custom theme (``.streamlit/config.toml``) with validated
  chart palettes, Geist fonts, and compact dashboard padding

The layout-first loading needs a Streamlit build with the ``st.skeleton``
changes from streamlit/streamlit#17920 and #17924 (see ``requirements.txt``).

All data is synthetic. Replace the generators in ``utils/data.py`` with your
own queries.
"""

from pathlib import Path

from utils import assistant, data

import streamlit as st

STATIC = Path(__file__).parent / "static"

st.set_page_config(
    page_title="Nimbus BI",
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
            "app_pages/overview.py",
            title="Overview",
            icon=":material/insights:",
            default=True,
        ),
        st.Page("app_pages/accounts.py", title="Accounts", icon=":material/groups:"),
        st.Page(
            "app_pages/pipeline.py", title="Pipeline", icon=":material/filter_alt:"
        ),
    ],
    position="top",
)


def reset_filters() -> None:
    # Bound widgets are reset by assigning their keys in a callback.
    st.session_state.period = data.DEFAULT_PERIOD
    st.session_state.regions = []
    st.session_state.plans = []
    st.session_state.annotations = False


# The entrypoint draws the page title and the global filters, so every page
# shares them. The filter summary is filled in once the widgets have run.
with st.container(horizontal=True, vertical_alignment="center"):
    with st.container(gap=None):
        st.title(page.title, icon=page.icon, anchor=False)
        summary = st.empty()
    st.button(
        "Reset",
        icon=":material/restart_alt:",
        type="tertiary",
        on_click=reset_filters,
        help="Reset the period, filters, and annotations",
    )
    if st.button(
        "Ask AI",
        icon=":material/auto_awesome:",
        help="Ask about this page, using the current filters",
    ):
        assistant.ask_ai_drawer(page.title)

with st.container(horizontal=True, vertical_alignment="center", gap="medium"):
    st.segmented_control(
        "Period",
        list(data.PERIODS),
        default=data.DEFAULT_PERIOD,
        required=True,
        key="period",
        bind="query-params",
        label_visibility="collapsed",
    )
    with st.popover("Filters", icon=":material/tune:"):
        st.pills(
            "Regions",
            data.REGIONS,
            selection_mode="multi",
            key="regions",
            help="Leave empty to include all regions.",
            bind="query-params",
        )
        st.pills(
            "Plans",
            data.PLANS,
            selection_mode="multi",
            key="plans",
            help="Leave empty to include all plans.",
            bind="query-params",
        )
        st.caption("Filters are saved in the URL, so you can share the current view.")
    st.toggle(
        "Annotations",
        value=False,
        key="annotations",
        bind="query-params",
        help="Mark product and pricing milestones on time-series charts.",
    )

summary.caption(data.current_filters().describe())

page.run()

st.caption(
    "Unofficial internal demo styled after Barclays' brand — not an official "
    "Barclays product or endorsement."
)
