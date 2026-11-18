"""Shared UI pieces for Cairn Reorder desk: headers, badges, drafts, and the review drawer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypeAlias, TypedDict

import streamlit as st
from utils import charts, rules, store

if TYPE_CHECKING:
    import pandas as pd

    from streamlit.delta_generator import DeltaGenerator

BadgeColor: TypeAlias = Literal[
    "red", "orange", "yellow", "blue", "green", "violet", "gray", "primary"
]

PRIORITY_COLORS: dict[str, BadgeColor] = {
    "Critical": "red",
    "High": "orange",
    "Normal": "gray",
}
FLAG_COLORS: dict[str, BadgeColor] = {
    "Over cap": "red",
    "Low confidence": "orange",
    "New supplier": "violet",
    "Demand spike": "yellow",
    "Due today": "blue",
}
ACTION_BADGES: dict[str, BadgeColor] = {
    "Approved": "green",
    "Approved with changes": "blue",
    "Rejected": "orange",
    "Held": "gray",
}
DECISION_LABELS = {"approve": "Approve", "hold": "Hold", "reject": "Reject"}
REVIEW = ":material/side_navigation: Review"


def reviewer() -> str:
    """The signed-in planner, or a guest name when the app has no sign-in."""
    name = st.user.get("name")
    return str(name) if name else "Guest planner"


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


class Draft(TypedDict, total=False):
    """An unsubmitted decision, with the queue table's column names."""

    order_qty: int | None
    decision: str | None  # A value of DECISION_LABELS
    note: str | None


def drafts() -> dict[str, Draft]:
    """Unsubmitted decisions by proposal ID, kept across reruns and filters."""
    return st.session_state.setdefault("drafts", {})


def draft_quantity(proposal_id: str, proposal: pd.Series) -> int:
    """The drafted order quantity, or the agent's when there's no draft."""
    quantity = drafts().get(proposal_id, {}).get("order_qty")
    return int(proposal["proposed_qty"] if quantity is None else quantity)


def draft_decision(proposal_id: str, proposal: pd.Series) -> store.Decision | None:
    draft = drafts().get(proposal_id, {})
    label = draft.get("decision")
    if not label:
        return None
    action = {value: key for key, value in DECISION_LABELS.items()}[label]
    return store.Decision(
        proposal_id=proposal_id,
        action=action,
        final_qty=draft_quantity(proposal_id, proposal),
        note=draft.get("note") or "",
    )


def _write(decision: store.Decision) -> bool:
    written, skipped = store.record_decisions([decision], reviewer())
    drafts().pop(decision.proposal_id, None)
    if skipped:
        st.toast(
            "This proposal was already decided. The queue is up to date now.",
            icon=":material/sync_problem:",
        )
        return False
    verb = {"approve": "Approved", "hold": "Held", "reject": "Rejected"}[
        decision.action
    ]
    st.toast(f"{verb} **{decision.proposal_id}**", icon=":material/check_circle:")
    return written > 0


@st.dialog("Review proposal", position="right", width=680, icon=":material/inventory:")
def review_drawer(proposal_id: str) -> None:
    proposals = rules.enrich(store.load_proposals(), store.load_policies())
    proposal = proposals.loc[proposal_id]
    policy = store.load_policies().loc[proposal["category"]]

    # Dialogs scale headings down, so the product name uses the title size.
    st.title(proposal["product"], anchor=False)
    with st.container(horizontal=True, gap="small"):
        st.badge(
            proposal["priority"],
            icon=":material/priority_high:",
            color=PRIORITY_COLORS[proposal["priority"]],
        )
        st.badge(proposal_id, icon=":material/receipt_long:", color="gray")
        st.badge(proposal["location"], icon=":material/warehouse:", color="gray")
        st.badge(proposal["supplier"], icon=":material/local_shipping:", color="gray")
        for flag in proposal["flags"]:
            st.badge(flag, color=FLAG_COLORS[flag])

    cases = proposal["proposed_qty"] // proposal["case_pack"]
    with st.container(border=True):
        st.markdown(
            f":primary[:material/smart_toy:] The agent recommends ordering "
            f"**{proposal['proposed_qty']:,} units** ({cases} cases of "
            f"{proposal['case_pack']}) for **{rules.money(proposal['value'])}**. They "
            f"arrive **{proposal['arrives']:%b {store.DAY}}**, and stock runs out around "
            f"**{proposal['stockout_date']:%b {store.DAY}}** without them."
        )
        st.caption(
            f"Confidence {proposal['confidence']:.0%} · order cutoff "
            f"{proposal['cutoff']:%a %b {store.DAY}, {store.HOUR}:%M %p} · proposed "
            f"{proposal['created_at']:%b {store.DAY}, {store.HOUR}:%M %p}"
        )

    with st.container(horizontal=True):
        st.metric("On hand", f"{proposal['on_hand']:,}", border=True)
        st.metric("Weekly demand", f"{proposal['weekly_forecast']:,.0f}", border=True)
        st.metric("Days of stock", f"{proposal['days_to_stockout']:.0f}", border=True)
        st.metric("Lead time", f"{proposal['lead_time_days']} days", border=True)

    draft = draft_decision(proposal_id, proposal)
    quantity = draft.final_qty if draft else int(proposal["proposed_qty"])

    with st.container(border=True):
        st.markdown("**Projected stock**")
        st.echarts_chart(
            charts.stock_projection(proposal, quantity),
            height=240,
            alt=f"Projected stock of {proposal['product']} at {proposal['location']} "
            "with and without the order",
        )
    with st.container(border=True):
        st.markdown("**Demand**")
        st.echarts_chart(
            charts.demand(proposal),
            height=200,
            alt=f"Weekly units sold and forecast for {proposal['product']}",
        )

    st.markdown("**How the agent got here**")
    for title, detail in proposal["reasoning"]:
        # A flagged step is still complete; its orange title draws the eye.
        label = f":orange[{title}]" if title.startswith("Flagged") else title
        with st.status(label, type="step", state="complete", expanded=True):
            st.markdown(detail)

    if proposal["status"] not in ("pending", "held"):
        st.info(
            f"{proposal['status'].title()} by {proposal['decided_by']} on "
            f"{proposal['decided_at']:%b {store.DAY}, {store.HOUR}:%M %p}.",
            icon=":material/task_alt:",
        )
        return

    st.markdown("**Your decision**")
    with st.form(f"decide_{proposal_id}", border=False):
        final_qty = st.number_input(
            "Order quantity",
            min_value=0,
            value=quantity,
            step=int(proposal["case_pack"]),
            help=f"Whole cases of {proposal['case_pack']}. The "
            f"{proposal['category'].lower()} cap is {rules.money(policy['order_cap'])}.",
            key=f"qty_{proposal_id}",
        )
        note = st.text_area(
            "Note",
            value=draft.note if draft else "",
            placeholder="Required to reject, or to change the quantity by more than "
            f"{policy['max_change_pct']:.0f}%",
            max_chars=300,
            height=80,
            key=f"note_{proposal_id}",
        )
        with st.container(horizontal=True, horizontal_alignment="right"):
            reject = st.form_submit_button(
                "Reject", icon=":material/block:", key=f"reject_{proposal_id}"
            )
            hold = st.form_submit_button(
                "Hold",
                icon=":material/pause_circle:",
                type="tertiary",
                key=f"hold_{proposal_id}",
            )
            approve = st.form_submit_button(
                "Approve",
                icon=":material/check:",
                type="primary",
                key=f"approve_{proposal_id}",
            )
    action = "approve" if approve else "reject" if reject else "hold" if hold else None
    if action:
        decision = store.Decision(
            proposal_id=proposal_id,
            action=action,
            final_qty=int(final_qty or 0),
            note=note or "",
        )
        problem = rules.validate(decision, proposal, store.load_policies())
        if problem:
            st.error(problem, icon=":material/error:")
        else:
            _write(decision)
            st.rerun()
