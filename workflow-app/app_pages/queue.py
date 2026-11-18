"""Review queue: approve, adjust, hold, or reject the agent's purchase orders.

Edits in the table are drafts kept in Session State by proposal ID, so they
survive filtering and searching. Nothing is written until the planner submits,
and every draft is validated on the server first.
"""

from datetime import date

import pandas as pd
from utils import rules, seed, store, ui

import streamlit as st

EDITOR_HEIGHT = 492
OPEN = ("pending", "held")
VIEWS = {"Awaiting review": ("pending",), "On hold": ("held",), "Decided": None}

policies = store.load_policies()
queue = rules.enrich(store.load_proposals(), policies)
decisions = store.load_decisions()
today = pd.Timestamp(date.today())


def sync_drafts(ids: list[str], key: str) -> None:
    """Copy the table's edits into the drafts, keyed by proposal ID."""
    for position, changes in st.session_state[key]["edited_rows"].items():
        ui.drafts().setdefault(ids[int(position)], {}).update(changes)


def reset_editor() -> None:
    # A new key gives the table a fresh state; the drafts hold the edits.
    st.session_state.editor_version = st.session_state.get("editor_version", 0) + 1


def accept_low_risk(ids: list[str]) -> None:
    for proposal_id in ids:
        ui.drafts()[proposal_id] = {
            "decision": "Approve",
            "order_qty": int(queue.loc[proposal_id, "proposed_qty"]),
            "note": "",
        }
    reset_editor()


def clear_drafts() -> None:
    ui.drafts().clear()
    reset_editor()


def submit(ready: list[store.Decision]) -> None:
    # Validate again on the server: a disabled button is only a hint.
    current = store.load_policies()
    ready = [
        decision
        for decision in ready
        if rules.validate(decision, queue.loc[decision.proposal_id], current) is None
    ]
    written, skipped = store.record_decisions(ready, ui.reviewer())
    for decision in ready:
        ui.drafts().pop(decision.proposal_id, None)
    reset_editor()
    approved = sum(decision.action == "approve" for decision in ready)
    message = f"Submitted {written} decision{'s' * (written != 1)}"
    if approved:
        message += f" · {approved} purchase order{'s' * (approved != 1)} sent"
    if skipped:
        message += f" · {skipped} already decided"
    st.toast(message, icon=":material/send:")


# KPI row
open_queue = queue[queue["status"].isin(OPEN)]
due_today = open_queue[open_queue["cutoff"].dt.normalize() == today]
decided_today = decisions[decisions["decided_at"].dt.normalize() == today]
approved_today = decided_today[decided_today["action"].str.startswith("Approved")]
recent = decisions[decisions["decided_at"] >= today - pd.Timedelta(days=30)]
earlier = recent[recent["decided_at"] < today - pd.Timedelta(days=15)]
later = recent[recent["decided_at"] >= today - pd.Timedelta(days=15)]
critical = int((open_queue["priority"] == "Critical").sum())
trend = (rules.acceptance(later) - rules.acceptance(earlier)) * 100

with st.container(horizontal=True):
    st.metric(
        "Awaiting review",
        len(open_queue),
        f"{critical} critical" if critical else 0,
        delta_color="red" if critical else "gray",
        delta_arrow="off",
        icon=":material/inbox:",
        border=True,
    )
    st.metric(
        "Value awaiting review",
        rules.money(open_queue["value"].sum()),
        f"{int(open_queue['low_risk'].sum())} low risk",
        delta_color="gray",
        delta_arrow="off",
        icon=":material/payments:",
        help="Low-risk proposals are under the category's auto-approve limit, "
        "meet its confidence floor, and have no supplier or demand flags.",
        border=True,
    )
    st.metric(
        "Due today",
        len(due_today),
        f"cutoff {due_today['cutoff'].min():{store.HOUR}:%M %p}" if len(due_today) else 0,
        delta_color="orange" if len(due_today) else "gray",
        delta_arrow="off",
        icon=":material/schedule:",
        border=True,
    )
    st.metric(
        "Approved today",
        rules.money(approved_today["value"].sum()),
        f"{len(approved_today)} orders",
        delta_color="gray",
        delta_arrow="off",
        icon=":material/local_shipping:",
        border=True,
    )
    st.metric(
        "Agent acceptance",
        f"{rules.acceptance(recent):.0%}",
        f"{trend:+.0f} pts",
        delta_description="last 15 days",
        icon=":material/smart_toy:",
        help="Share of the last 30 days' proposals approved without changes.",
        border=True,
    )

# Toolbar with filters bound to the URL
with st.container(horizontal=True, vertical_alignment="center", gap="medium"):
    view = st.segmented_control(
        "View",
        list(VIEWS),
        default="Awaiting review",
        required=True,
        key="view",
        bind="query-params",
        label_visibility="collapsed",
    )
    categories = st.pills(
        "Category",
        seed.CATEGORIES,
        selection_mode="multi",
        key="category",
        bind="query-params",
        label_visibility="collapsed",
    )
    search = st.text_input(
        "Search",
        placeholder="Search products, SKUs, suppliers, or POs",
        type="search",
        live=True,
        icon=":material/search:",
        key="search",
        label_visibility="collapsed",
        width=320,
    )

statuses = VIEWS[view]
rows = (
    queue[queue["status"].isin(statuses)]
    if statuses
    else queue[~queue["status"].isin(OPEN)]
)
if categories:
    rows = rows[rows["category"].isin(categories)]
if search:
    haystack = (
        rows.index.to_series()
        + " "
        + rows[["product", "sku", "supplier", "location"]].agg(" ".join, axis=1)
    )
    rows = rows[haystack.str.contains(search, case=False, regex=False)]
priority_rank = {priority: rank for rank, priority in enumerate(seed.PRIORITIES)}
rows = rows.sort_values(
    ["priority", "cutoff", "days_to_stockout"],
    key=lambda column: (
        column.map(priority_rank) if column.name == "priority" else column
    ),
)

if rows.empty:
    st.info(
        "Nothing here. Try another view, or clear the filters and search.",
        icon=":material/inbox:",
    )
    st.stop()

# Short lists get a short table: about 32px per row, plus the header and borders.
table_height = min(EDITOR_HEIGHT, 32 * (len(rows) + 1) + 4)

if statuses is None:
    # Decided proposals are read-only.
    decided = pd.DataFrame(
        {
            "review": ui.REVIEW,
            "po": rows.index,
            "product": rows["product"],
            "location": rows["location"],
            "status": [[status.title()] for status in rows["status"]],
            "final_qty": rows["final_qty"],
            "value": rows["final_qty"] * rows["unit_cost"] / 1e3,
            "decided_by": rows["decided_by"],
            "decided_at": rows["decided_at"],
            "note": rows["note"].fillna(""),
        }
    )
    st.dataframe(
        decided,
        column_config={
            "review": st.column_config.ButtonColumn(
                "", type="tertiary", pinned=True, key="decided_click"
            ),
            "po": st.column_config.TextColumn("PO", pinned=True),
            "product": st.column_config.TextColumn("Product", pinned=True),
            "location": "Location",
            "status": st.column_config.MultiselectColumn(
                "Decision",
                options=["Approved", "Held", "Rejected"],
                color=["green", "gray", "orange"],
            ),
            "final_qty": st.column_config.NumberColumn("Ordered", format="localized"),
            "value": st.column_config.NumberColumn("Value", format="$%.1fk"),
            "decided_by": "Reviewer",
            "decided_at": st.column_config.DatetimeColumn(
                "Decided", format="MMM D, h:mm a"
            ),
            "note": st.column_config.TextColumn("Note", width="large"),
        },
        hide_index=True,
        height=table_height,
        alt="Proposals decided from today's agent run",
    )
    click = st.session_state.get("decided_click")
    if click is not None:
        ui.review_drawer(decided["po"].iloc[click.row])
    st.stop()

drafts = ui.drafts()
ids = list(rows.index)
editor_key = f"queue_{st.session_state.get('editor_version', 0)}"
order_qty = [ui.draft_quantity(pid, rows.loc[pid]) for pid in ids]
table = pd.DataFrame(
    {
        "review": ui.REVIEW,
        "product": rows["product"],
        "location": rows["location"].str.removesuffix(" DC"),
        "priority": [[priority] for priority in rows["priority"]],
        "days_left": rows["days_to_stockout"].astype(int),
        "agent_qty": rows["proposed_qty"],
        "order_qty": order_qty,
        "value": [qty * cost / 1e3 for qty, cost in zip(order_qty, rows["unit_cost"])],
        "decision": [drafts.get(pid, {}).get("decision") for pid in ids],
        "note": [drafts.get(pid, {}).get("note") or "" for pid in ids],
        "confidence": (rows["confidence"] * 100).round(),
        "flags": rows["flags"],
        "po": ids,
    },
    index=rows.index,
)
st.data_editor(
    table,
    key=editor_key,
    on_change=sync_drafts,
    args=(ids, editor_key),
    column_config={
        "review": st.column_config.ButtonColumn(
            "",
            type="tertiary",
            pinned=True,
            key="review_click",
            help="See the agent's reasoning and decide in a drawer",
        ),
        "product": st.column_config.TextColumn("Product", pinned=True),
        "location": st.column_config.TextColumn("DC", width="small"),
        "priority": st.column_config.MultiselectColumn(
            "Priority",
            options=seed.PRIORITIES,
            color=[ui.PRIORITY_COLORS[p] for p in seed.PRIORITIES],
            width="small",
        ),
        "days_left": st.column_config.NumberColumn(
            "Stock left",
            format="%d days",
            help="Days until stock runs out at the current forecast",
            width="small",
        ),
        "agent_qty": st.column_config.NumberColumn(
            "Agent qty",
            format="localized",
            help="Units the agent proposed",
            width="small",
        ),
        "order_qty": st.column_config.NumberColumn(
            "Order qty",
            format="localized",
            min_value=0,
            max_value=100_000,
            step=1,
            help="Edit to change the order. Use whole cases.",
        ),
        "value": st.column_config.NumberColumn("Value", format="$%.1fk", width="small"),
        "decision": st.column_config.SelectboxColumn(
            "Decision",
            options=list(ui.DECISION_LABELS.values()),
            help="Approve, hold, or reject. Submit below to write it back.",
        ),
        "note": st.column_config.TextColumn(
            "Note", max_chars=300, width="medium", help="Why you changed or rejected it"
        ),
        "confidence": st.column_config.ProgressColumn(
            "Confidence", min_value=0, max_value=100, format="%d%%", width="small"
        ),
        "flags": st.column_config.MultiselectColumn(
            "Flags",
            options=rules.FLAGS,
            color=[ui.FLAG_COLORS[flag] for flag in rules.FLAGS],
            width="medium",
        ),
        "po": st.column_config.TextColumn("PO", width="small"),
    },
    placeholder="Select",
    disabled=[
        column
        for column in table.columns
        if column not in ("order_qty", "decision", "note")
    ],
    hide_index=True,
    height=table_height,
    alt="Purchase orders proposed by the replenishment agent, with editable quantity, "
    "decision, and note",
)
# Decision bar: what will be written, and anything that blocks it
open_ids = set(queue.index[queue["status"].isin(OPEN)])
ready: list[store.Decision] = []
problems: list[str] = []
for proposal_id in list(drafts):
    if proposal_id not in open_ids:
        drafts.pop(proposal_id)
        continue
    decision = ui.draft_decision(proposal_id, queue.loc[proposal_id])
    if decision is None:
        continue
    problem = rules.validate(decision, queue.loc[proposal_id], policies)
    if problem:
        problems.append(
            f"**{proposal_id}** {queue.loc[proposal_id, 'product']}: {problem}"
        )
    ready.append(decision)
low_risk = [
    pid
    for pid in ids
    if rows.loc[pid, "low_risk"]
    and rows.loc[pid, "status"] == "pending"
    and pid not in drafts
]

with st.container(border=True):
    with st.container(horizontal=True, vertical_alignment="center"):
        if ready:
            approvals = [d for d in ready if d.action == "approve"]
            value = sum(
                d.final_qty * queue.loc[d.proposal_id, "unit_cost"] for d in approvals
            )
            counts = {
                action: sum(d.action == action for d in ready)
                for action in ("approve", "hold", "reject")
            }
            parts = [f"{n} to {action}" for action, n in counts.items() if n]
            st.markdown(
                f":primary[:material/edit_note:] **{len(ready)} decision"
                f"{'s' * (len(ready) != 1)} ready** · {', '.join(parts)} · "
                f"{rules.money(value)} in orders"
            )
        else:
            st.markdown(
                ":gray[:material/edit_note:] Set a row's **Decision** to approve, hold, "
                "or reject it, or open **Review** for the agent's reasoning."
            )
        with st.container(
            horizontal=True, horizontal_alignment="right", width="content"
        ):
            # Opens the drawer for the top row, so a planner (or an agent using
            # the app) can work through the queue one proposal at a time.
            review_next = st.button(
                "Review next",
                icon=":material/play_arrow:",
                type="tertiary",
                help="Open the most urgent proposal in this view",
                key="review_next",
            )
            st.button(
                f"Accept {len(low_risk)} low-risk" if low_risk else "Accept low-risk",
                icon=":material/auto_awesome:",
                type="tertiary",
                on_click=accept_low_risk,
                args=(low_risk,),
                disabled=not low_risk,
                help="Approve the agent's quantity for every low-risk proposal shown",
                key="accept_low_risk",
            )
            st.button(
                "Clear",
                icon=":material/close:",
                type="tertiary",
                on_click=clear_drafts,
                disabled=not drafts,
                key="clear_drafts",
            )
            st.button(
                f"Submit {len(ready)}" if ready else "Submit",
                icon=":material/send:",
                type="primary",
                on_click=submit,
                args=(ready,),
                disabled=not ready or bool(problems),
                key="submit",
            )
    for problem in problems:
        st.markdown(f":red[:material/error:] {problem}")

click = st.session_state.get("review_click")
if click is not None:
    ui.review_drawer(ids[click.row])
elif review_next:
    ui.review_drawer(ids[0])
