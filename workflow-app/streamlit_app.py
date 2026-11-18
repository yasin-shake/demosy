"""Cairn Reorder desk: planners review the purchase orders an AI agent proposes.

A workflow app: the decision happens in the same app as the data. Every
morning a replenishment agent proposes purchase orders and explains each one.
Planners adjust quantities and approve, hold, or reject them in an editable
table or a review drawer. Every decision is validated against guardrails on
the server and written back to the database, so the queue, the audit log, and
the agent's scorecard stay current.

Demonstrates:
- ``st.data_editor`` as a review queue: editable quantities, a decision
  ``SelectboxColumn``, notes, badge columns, and a ``ButtonColumn`` that opens
  a review drawer (``@st.dialog(position="right")``)
- Server-side validation (whole cases, order caps, notes for large changes and
  rejections) before writeback through ``st.connection`` (SQLAlchemy on SQLite)
- The agent's reasoning as an ``st.status(type="step")`` timeline, plus stock
  projection and demand charts with ``st.echarts_chart``
- Guardrails edited in place with a live preview of their effect on the queue
- Filters bound to the URL with ``bind="query-params"``, live search, and an
  audit log with CSV export
- A teal light/dark brand theme with a CVD-checked chart palette that the
  charts take their colors from

All data is synthetic, and every session gets its own database seeded with
today's agent run. Replace ``utils/seed.py`` with your agent's output and
``store.connection`` with your warehouse.
"""

from pathlib import Path

from utils import seed, store
from utils.store import DAY, HOUR

import streamlit as st

# Without a sidebar, the header shows the full logo, so there is no icon_image.
STATIC = Path(__file__).parent / "static"

st.set_page_config(
    page_title="Cairn Reorder desk",
    page_icon=STATIC / "icon.svg",
    layout="wide",
)

logo_variant = "dark" if st.context.theme.type == "dark" else "light"
st.logo(STATIC / f"logo-{logo_variant}.svg", size="large")

store.ensure_ready()

page = st.navigation(
    [
        st.Page(
            "app_pages/queue.py",
            title="Review queue",
            icon=":material/inbox:",
            default=True,
        ),
        st.Page("app_pages/activity.py", title="Activity", icon=":material/history:"),
        st.Page(
            "app_pages/guardrails.py", title="Guardrails", icon=":material/shield:"
        ),
    ],
    position="top",
)

proposals = store.load_proposals()
pending = proposals[proposals["status"] == "pending"]
run = proposals["created_at"].min()


def reset_demo() -> None:
    store.reset_demo()
    st.session_state.pop("drafts", None)
    st.toast("Reset to a fresh agent run", icon=":material/restart_alt:")


with st.container(horizontal=True, vertical_alignment="center"):
    with st.container(gap=None):
        st.title(page.title, icon=page.icon, anchor=False)
        st.caption(
            f"Replenishment agent run {run:%a %b {DAY}, {HOUR}:%M %p} · "
            f"{len(proposals)} proposals across {proposals['location'].nunique()} "
            f"distribution centers · {len(pending)} awaiting review"
        )
    with st.container(
        horizontal=True,
        horizontal_alignment="right",
        vertical_alignment="center",
        width="content",
    ):
        st.badge("Agent run complete", icon=":material/smart_toy:", color="green")
        action = st.menu_button(
            ":material/more_vert:",
            [":material/restart_alt: Reset demo data"],
            type="tertiary",
            help="More options",
            key="app_menu",
        )
if action:
    reset_demo()
    st.rerun()

page.run()

st.caption(
    f"Synthetic data for a fictional retailer: {len(seed.ITEMS)} SKUs, "
    f"{len(seed.LOCATIONS)} distribution centers. Your decisions stay in this "
    "session, and a new session starts from today's agent run."
)
st.caption(
    "Unofficial internal demo styled after Barclays' brand — not an official "
    "Barclays product or endorsement."
)
