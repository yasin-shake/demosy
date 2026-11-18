"""Guardrails: the limits every decision is checked against, edited in place.

The preview recomputes the queue with the unsaved limits on every edit, so a
planner sees the effect before anything is written.
"""

import pandas as pd
from utils import rules, store, ui

import streamlit as st

policies = store.load_policies()
proposals = store.load_proposals()
open_proposals = proposals[proposals["status"].isin(("pending", "held"))]
COLUMNS = ["auto_approve_limit", "order_cap", "max_change_pct", "min_confidence"]


def discard() -> None:
    st.session_state.policy_version = st.session_state.get("policy_version", 0) + 1


def counted(frame: pd.DataFrame, flag: str | None) -> int:
    """Low-risk proposals, or proposals with ``flag``."""
    if flag is None:
        return int(frame["low_risk"].sum())
    return int(frame["flags"].map(lambda flags: flag in flags).sum())


editor_col, preview_col = st.columns([3, 2])
with editor_col.container(border=True, height="stretch"):
    updated = policies["updated_at"].max()
    ui.card_header(
        "Limits by category",
        f"Last changed by {policies.loc[policies['updated_at'].idxmax(), 'updated_by']} "
        f"on {updated:%b {store.DAY}}. Edit a cell, check the preview, then save.",
    )
    edited = st.data_editor(
        policies[COLUMNS],
        key=f"policies_{st.session_state.get('policy_version', 0)}",
        disabled=["_index"],
        column_config={
            "_index": st.column_config.TextColumn("Category"),
            "auto_approve_limit": st.column_config.NumberColumn(
                "Auto-approve under",
                format="dollar",
                min_value=0,
                max_value=100_000,
                step=500,
                help="Proposals under this value count as low risk when nothing "
                "else flags them, so planners can accept them in one click.",
            ),
            "order_cap": st.column_config.NumberColumn(
                "Order cap",
                format="dollar",
                min_value=1_000,
                max_value=500_000,
                step=1_000,
                help="No single order above this value can be approved.",
            ),
            "max_change_pct": st.column_config.NumberColumn(
                "Note above change",
                format="%d%%",
                min_value=0,
                max_value=100,
                step=5,
                help="Changing the agent's quantity by more than this needs a note.",
            ),
            "min_confidence": st.column_config.NumberColumn(
                "Confidence floor",
                format="%d%%",
                min_value=50,
                max_value=99,
                step=1,
                help="Proposals below this confidence are flagged.",
            ),
        },
        alt="Approval limits for each product category",
    )
    st.table(
        {
            ":material/auto_awesome: Auto-approve under": "Below this value, with "
            "no flags and enough confidence, a proposal is low risk and can be "
            "accepted in one click.",
            ":material/block: Order cap": "No order above this value can be "
            "approved. Planners reduce the quantity or reject it.",
            ":material/edit_note: Note above change": "Changing the agent's "
            "quantity by more than this needs a note in the audit log.",
            ":material/help: Confidence floor": "Proposals below it are flagged "
            "and never count as low risk.",
        },
        border="horizontal",
        alt="What each limit controls",
    )

    problems = [
        f"**{category}**: the auto-approve limit can't be above the order cap"
        for category, row in edited.iterrows()
        if row["auto_approve_limit"] > row["order_cap"]
    ]
    for problem in problems:
        st.markdown(f":red[:material/error:] {problem}")
    changed = not edited.equals(policies[COLUMNS])
    with st.container(horizontal=True, horizontal_alignment="right"):
        st.button(
            "Discard changes",
            type="tertiary",
            icon=":material/undo:",
            on_click=discard,
            disabled=not changed,
            key="discard_policies",
        )
        save = st.button(
            "Save limits",
            type="primary",
            icon=":material/save:",
            disabled=not changed or bool(problems),
            key="save_policies",
        )

before = rules.enrich(open_proposals, policies)
after = rules.enrich(open_proposals, edited.assign(updated_by="", updated_at=updated))
with preview_col.container(border=True, height="stretch"):
    ui.card_header(
        "Effect on the open queue",
        f"{len(open_proposals)} proposals awaiting review or on hold, with "
        + ("the edited limits" if changed else "the saved limits"),
    )
    for label, flag, icon, color in (
        ("Low risk, one-click approval", None, ":material/auto_awesome:", "normal"),
        ("Over the order cap", "Over cap", ":material/block:", "inverse"),
        ("Below the confidence floor", "Low confidence", ":material/help:", "inverse"),
    ):
        now, then = counted(after, flag), counted(before, flag)
        st.metric(
            label,
            now,
            (now - then) if now != then else None,
            delta_color=color,
            delta_description="vs. saved limits",
            icon=icon,
            border=True,
        )

# Checked again on the server: a disabled button is only a hint.
if save and changed and not problems:
    store.save_policies(edited, ui.reviewer())
    discard()
    st.toast("Saved the new limits. The queue uses them now.", icon=":material/shield:")
    st.rerun()
