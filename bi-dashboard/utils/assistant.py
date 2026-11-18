"""Ask AI: a simulated assistant in a side drawer that answers from the dashboard's data.

No model is called. Each question maps to an analysis that runs on the same
cached data and toolbar filters as the cards, so the assistant's numbers match
what's on screen. Like a real agent, it shows its steps (a compact "Thought for
N seconds" toggle over a step timeline), streams its answer, and asks for
feedback. To use a model, build ``Answer`` objects from its tool calls and
stream its text instead of ``Answer.text``.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

import streamlit as st
from utils import charts, data
from utils.definitions import DEFINITIONS
from utils.formatting import DAY, money_md

AVATAR = str(Path(__file__).parent.parent / "static" / "icon.svg")
STEP_PAUSE_S = 0.55
WORD_PAUSE_S = 0.018


@dataclass
class Step:
    label: str  # When it's done
    running: str  # While it runs
    seconds: float  # How long it takes in the demo's story
    detail: str = ""
    code: str | None = None


@dataclass
class Answer:
    text: str
    steps: list[Step]
    chart: dict[str, Any] | None = None
    chart_alt: str = ""
    table: pd.DataFrame | None = None
    followups: list[str] = field(default_factory=list)

    @property
    def thinking_seconds(self) -> int:
        return max(1, round(sum(step.seconds for step in self.steps)))


QUESTIONS = {
    "summary": "Summarize revenue for this view",
    "mrr_change": "What drove the change in MRR?",
    "plans": "Which plan is growing fastest?",
    "at_risk": "Which accounts are at risk?",
    "industry": "Which industries bring the most revenue?",
    "usage": "When are customers most active?",
    "pipeline": "How healthy is the pipeline?",
}

# The questions suggested for each place the drawer opens from, best first.
CONTEXTS = {
    "Overview": ["summary", "mrr_change", "plans", "industry"],
    "Accounts": ["at_risk", "industry", "plans"],
    "Pipeline": ["pipeline", "summary"],
    "mrr_by_plan": ["plans", "summary"],
    "mrr_bridge": ["mrr_change", "plans"],
    "usage": ["usage", "summary"],
    "industry_mix": ["industry", "at_risk"],
    "health_scatter": ["at_risk", "industry"],
    "bookings": ["pipeline", "summary"],
    "quota": ["pipeline", "summary"],
    "pipeline_flow": ["pipeline"],
    "stage_funnel": ["pipeline"],
}

ROUTES = [
    ("pipeline", r"pipeline|deal|booking|quota|win rate|weighted|opportunit"),
    ("at_risk", r"risk|health|renewal|save|retain"),
    ("usage", r"usage|session|active|hour|busiest|when"),
    ("industry", r"industr|vertical|sector"),
    ("plans", r"plan|enterprise|starter|\bpro\b|tier|fastest"),
    ("mrr_change", r"why|drove|driver|bridge|expan|contract|churn|change|move"),
    ("summary", r"summar|overview|recap|how are we|kpi|arr|nrr|revenue"),
]


DAY_NAMES = dict(
    zip(
        data.WEEKDAYS,
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        strict=True,
    )
)


def route(question: str) -> str:
    lowered = question.lower()
    return next(
        (intent for intent, pattern in ROUTES if re.search(pattern, lowered)), "help"
    )


def pct(value: float) -> str:
    return f"{value:+.1%}".replace("-", "−")


# Analyses --------------------------------------------------------------------


def _book() -> tuple[data.Filters, pd.DataFrame, pd.DataFrame]:
    filters = data.current_filters()
    accounts, history = data.load_book()
    return filters, data.filter_accounts(accounts, filters), history


def _query_step(definition: str, filters: data.Filters, what: str) -> Step:
    return Step(
        f"Queried {what} with your filters",
        f"Querying {what}",
        1.6,
        filters.describe(),
        code=DEFINITIONS[definition].sql,
    )


def _definition_step(definition: str) -> Step:
    return Step(
        "Read the certified definition",
        "Reading the certified definition",
        0.9,
        f"**{DEFINITIONS[definition].title}:** {DEFINITIONS[definition].measures}",
    )


def summary() -> Answer:
    filters, accounts, history = _book()
    kpis = data.monthly_kpis(history, accounts.index, filters.months)
    movements = data.mrr_movements(history, accounts.index, filters.months)
    arr_first, arr_last = kpis["arr"].iloc[0], kpis["arr"].iloc[-1]
    net_new = kpis["net_new_mrr"].iloc[-1]
    net_new_average = kpis["net_new_mrr"].iloc[1:-1].mean()
    churn_last, churn_average = (
        kpis["gross_churn"].iloc[-1],
        kpis["gross_churn"].iloc[1:].mean(),
    )
    drivers = {
        "new customers": movements["new"],
        "expansion": movements["expansion"],
    }
    driver = max(drivers, key=drivers.get)
    text = (
        f"**ARR is {money_md(arr_last)}, {pct(arr_last / arr_first - 1)} over the last "
        f"{filters.months} months.** Net new MRR was {money_md(net_new)} last month, "
        f"{'above' if net_new >= net_new_average else 'below'} the period average of "
        f"{money_md(net_new_average)}.\n\n"
        f"- **Retention:** net revenue retention is {kpis['nrr'].iloc[-1]:.1f}%.\n"
        f"- **Churn:** monthly gross churn was {churn_last:.1f}%, against an average "
        f"of {churn_average:.1f}%.\n"
        f"- **Growth:** most of it came from {driver}, {money_md(drivers[driver])} of MRR "
        "over the period.\n\n"
        "The numbers use the same certified definitions as the KPI cards."
    )
    return Answer(
        text,
        [
            _definition_step("revenue"),
            _query_step("revenue", filters, "monthly MRR"),
            Step(
                "Compared last month with the period",
                "Comparing last month with the period",
                1.1,
                f"{len(accounts)} accounts · {filters.months + 1} months",
            ),
        ],
        followups=[QUESTIONS["mrr_change"], QUESTIONS["at_risk"]],
    )


def mrr_change() -> Answer:
    filters, accounts, history = _book()
    m = data.mrr_movements(history, accounts.index, filters.months)
    gained, lost = m["new"] + m["expansion"], m["contraction"] + m["churn"]
    biggest = max(
        ("expansion", m["expansion"]), ("new customers", m["new"]), key=lambda x: x[1]
    )
    text = (
        f"**MRR grew from {money_md(m['start'])} to {money_md(m['end'])} over the last "
        f"{filters.months} months**, {pct(m['end'] / m['start'] - 1)}. The biggest driver "
        f"was {biggest[0]}, at {money_md(biggest[1])}.\n\n"
        f"- **Gained {money_md(gained)}:** {money_md(m['new'])} from new customers and "
        f"{money_md(m['expansion'])} from expansion.\n"
        f"- **Lost {money_md(lost)}:** {money_md(m['contraction'])} to contraction and "
        f"{money_md(m['churn'])} to churn.\n\n"
        f"For every dollar lost, it gained {money_md(gained / max(lost, 1))}."
    )
    return Answer(
        text,
        [
            _definition_step("mrr_bridge"),
            _query_step("mrr_bridge", filters, "MRR movements"),
            Step(
                "Split the change into movements",
                "Splitting the change into movements",
                1.0,
                "New, expansion, contraction, and churn, per account.",
            ),
        ],
        chart=charts.mrr_bridge(m),
        chart_alt="MRR bridge from the start to the end of the period",
        followups=[QUESTIONS["plans"], QUESTIONS["at_risk"]],
    )


def plans() -> Answer:
    filters, _, history = _book()
    accounts, _ = data.load_book()
    regional = accounts[accounts["region"].isin(filters.regions)]
    monthly = data.mrr_by_plan(history, regional, filters.months)[list(filters.plans)]
    growth = monthly.iloc[-1] / monthly.iloc[0].replace(0, float("nan")) - 1
    share = monthly.iloc[-1] / monthly.iloc[-1].sum()
    fastest = growth.astype(float).idxmax()
    forecast = data.mrr_forecast(monthly).iloc[-1][fastest]
    table = pd.DataFrame(
        {
            "Plan": monthly.columns,
            "MRR now": monthly.iloc[-1].values,
            "Growth": growth.astype(float).values,
            "Share": share.values,
        }
    ).sort_values("Growth", ascending=False)
    text = (
        f"**{fastest} is growing fastest: up {float(growth[fastest]):.1%} over the last "
        f"{filters.months} months, to {money_md(monthly.iloc[-1][fastest])} of MRR.** At "
        f"its recent pace, it reaches {money_md(forecast)} in three months.\n\n"
        + "\n".join(
            f"- **{row.Plan}:** {pct(row.Growth)}, {row.Share:.0%} of MRR"
            for row in table.itertuples()
        )
    )
    return Answer(
        text,
        [
            _definition_step("mrr_by_plan"),
            _query_step("mrr_by_plan", filters, "MRR by plan"),
            Step(
                "Projected each plan's recent growth",
                "Projecting each plan's growth",
                1.0,
                "Average monthly growth over the last six months, three months ahead.",
            ),
        ],
        table=table,
        followups=[QUESTIONS["mrr_change"], QUESTIONS["industry"]],
    )


def at_risk() -> Answer:
    filters, accounts, _ = _book()
    active = accounts[accounts["mrr"] > 0]
    risky = active[active["status"] == "At risk"].sort_values("mrr", ascending=False)
    soon = risky[
        pd.to_datetime(risky["renewal_date"])
        <= pd.Timestamp(date.today() + timedelta(days=90))
    ]
    if risky.empty:
        text = "**No accounts are at risk in this view.** Every active account has a healthy or watch-level score."
    else:
        top = risky.iloc[0]
        text = (
            f"**{len(risky)} accounts are at risk, with {money_md(risky['mrr'].sum())} of MRR**, "
            f"{risky['mrr'].sum() / active['mrr'].sum():.0%} of the total. "
            f"{len(soon)} of them renew in the next 90 days.\n\n"
            f"The largest is **{top['name']}** ({top['plan']}, {money_md(top['mrr'])} MRR, "
            f"health {top['health']}/100), owned by {top['owner']}. It renews "
            f"{pd.Timestamp(top['renewal_date']):%b {DAY}}."
        )
    table = risky.head(6)[["name", "plan", "mrr", "health", "renewal_date", "owner"]]
    return Answer(
        text,
        [
            _definition_step("health_scatter"),
            _query_step("accounts", filters, "the customer book"),
            Step(
                "Checked upcoming renewals",
                "Checking upcoming renewals",
                0.9,
                f"{len(soon)} at-risk renewals in the next 90 days.",
            ),
        ],
        table=table,
        followups=[QUESTIONS["industry"], QUESTIONS["summary"]],
    )


def industry() -> Answer:
    filters, accounts, _ = _book()
    active = accounts[accounts["mrr"] > 0]
    totals = (
        active.groupby("industry")["mrr"]
        .agg(["sum", "count"])
        .sort_values("sum", ascending=False)
    )
    share = totals["sum"] / totals["sum"].sum()
    average = totals["sum"] / totals["count"]
    first, last = totals.index[0], totals.index[-1]
    text = (
        f"**{first} brings the most revenue: {money_md(totals.loc[first, 'sum'])} of MRR, "
        f"{share[first]:.0%} of the total**, across {totals.loc[first, 'count']} accounts. "
        f"{last} brings the least, {share[last]:.0%}.\n\n"
        f"The biggest accounts are in {average.idxmax()}, at {money_md(average.max())} of MRR "
        "each on average."
    )
    return Answer(
        text,
        [
            _definition_step("industry_mix"),
            _query_step("industry_mix", filters, "MRR by industry"),
        ],
        chart=charts.industry_mix(totals["sum"].reindex(data.INDUSTRIES, fill_value=0)),
        chart_alt="Share of MRR by industry",
        followups=[QUESTIONS["at_risk"], QUESTIONS["plans"]],
    )


def usage() -> Answer:
    filters = data.current_filters()
    matrix = data.usage_matrix(data.load_usage(), filters.regions)
    day, hour = matrix.stack().idxmax()
    weekday_share = (
        matrix.loc[data.WEEKDAYS[:5]].to_numpy().sum() / matrix.to_numpy().sum()
    )
    quietest = matrix.sum(axis=1).idxmin()
    text = (
        f"**Customers are most active on {DAY_NAMES[day]}s at {hour}:00 UTC.** "
        f"Weekdays carry {weekday_share:.0%} of sessions, and {DAY_NAMES[quietest]} is "
        "the quietest day.\n\n"
        f"That makes {DAY_NAMES[quietest]} the safest window for maintenance, and "
        f"{DAY_NAMES[day]} the best day to ship visible changes."
    )
    return Answer(
        text,
        [
            _definition_step("usage"),
            _query_step("usage", filters, "product sessions"),
        ],
        followups=[QUESTIONS["summary"]],
    )


def pipeline() -> Answer:
    filters = data.current_filters()
    deals = data.with_deal_metrics(
        data.filter_deals(data.apply_deal_overrides(data.load_deals()), filters)
    )
    open_deals = deals[deals["stage"].isin(data.OPEN_STAGES)]
    closed = deals[deals["stage"].isin(["Closed won", "Closed lost"])]
    win_rate = (closed["stage"] == "Closed won").mean() if len(closed) else 0
    if open_deals.empty:
        return Answer(
            "**There are no open deals in this view.** Widen the filters to see the pipeline.",
            [],
        )
    by_stage = (
        open_deals.groupby("stage")["amount"]
        .sum()
        .reindex(data.OPEN_STAGES, fill_value=0)
    )
    largest = open_deals.sort_values("amount", ascending=False).iloc[0]
    text = (
        f"**Open pipeline is {money_md(open_deals['amount'].sum())} across "
        f"{len(open_deals)} deals, with a weighted forecast of "
        f"{money_md(open_deals['weighted'].sum())}.** The win rate on closed deals is "
        f"{win_rate:.0%}.\n\n"
        f"- **Most value sits in {by_stage.idxmax()}:** {money_md(by_stage.max())}.\n"
        f"- **Late stage:** {money_md(by_stage['Negotiation'])} is in negotiation.\n"
        f"- **Largest open deal:** {largest['account']}, {money_md(largest['amount'])} in "
        f"{largest['stage'].lower()}, owned by {largest['owner']}."
    )
    return Answer(
        text,
        [
            _definition_step("pipeline"),
            _query_step("pipeline", filters, "open and closed deals"),
            Step(
                "Weighted each deal by its stage",
                "Weighting deals by stage",
                0.9,
                " · ".join(
                    f"{s} {p:.0%}"
                    for s, p in data.STAGE_PROBABILITY.items()
                    if s in data.OPEN_STAGES
                ),
            ),
        ],
        followups=[QUESTIONS["summary"]],
    )


def help_answer(context: str) -> Answer:
    return Answer(
        "This demo plays back a few analyses of the dashboard's data, so it can't answer "
        "that one yet. It doesn't call a model. Try one of the questions below. Each "
        "answer uses your current filters.",
        [],
        followups=[QUESTIONS[i] for i in CONTEXTS.get(context, CONTEXTS["Overview"])],
    )


ANALYSES: dict[str, Callable[[], Answer]] = {
    "summary": summary,
    "mrr_change": mrr_change,
    "plans": plans,
    "at_risk": at_risk,
    "industry": industry,
    "usage": usage,
    "pipeline": pipeline,
}


def answer(question: str, context: str) -> Answer:
    intent = route(question)
    return ANALYSES[intent]() if intent in ANALYSES else help_answer(context)


# The drawer ------------------------------------------------------------------


def _title(context: str) -> str:
    return DEFINITIONS[context].title if context in DEFINITIONS else f"{context} page"


def _words(text: str) -> Iterator[str]:
    for token in re.findall(r"\s*\S+", text):
        time.sleep(WORD_PAUSE_S)
        yield token


TABLE_COLUMNS = {
    "name": st.column_config.TextColumn("Account"),
    "plan": st.column_config.TextColumn("Plan"),
    "mrr": st.column_config.NumberColumn("MRR", format="$%,d"),
    "health": st.column_config.ProgressColumn(
        "Health", min_value=0, max_value=100, format="%d", color="auto", width="small"
    ),
    "renewal_date": st.column_config.DateColumn("Renewal", format="MMM D"),
    "owner": st.column_config.TextColumn("Owner"),
    "MRR now": st.column_config.NumberColumn(format="$%,d"),
    "Growth": st.column_config.NumberColumn(format="percent"),
    "Share": st.column_config.NumberColumn(format="percent"),
}


def _steps(reply: Answer, *, live: bool) -> None:
    if not reply.steps:
        return
    done = f"Thought for {reply.thinking_seconds} seconds"
    if not live:
        with st.status(done, type="compact", state="complete"):
            for step in reply.steps:
                with st.status(step.label, type="step", state="complete"):
                    _step_details(step)
        return
    with st.status(":shimmer[Thinking]", type="compact", expanded=True) as thinking:
        for step in reply.steps:
            with st.status(
                f":shimmer[{step.running}]", type="step", expanded=True
            ) as status:
                time.sleep(STEP_PAUSE_S)
                _step_details(step)
                status.update(label=step.label, state="complete")
        thinking.update(label=done, state="complete", expanded=False)


def _step_details(step: Step) -> None:
    if step.detail:
        st.caption(step.detail)
    if step.code:
        st.code(step.code, language="sql", wrap_lines=True)


def _extras(reply: Answer) -> None:
    if reply.chart is not None:
        st.echarts_chart(reply.chart, height=240, alt=reply.chart_alt)
    if reply.table is not None:
        st.dataframe(reply.table, hide_index=True, column_config=TABLE_COLUMNS)


def _feedback(message_id: str) -> None:
    if st.session_state.get(f"ai_feedback_{message_id}") is not None:
        st.toast("Thanks for the feedback.", icon=":material/check:")


def _render(message: dict[str, Any]) -> None:
    if message["role"] == "user":
        with st.chat_message("user"):
            st.markdown(message["text"])
        return
    reply: Answer = message["answer"]
    with st.chat_message("assistant", avatar=AVATAR):
        _steps(reply, live=False)
        st.markdown(reply.text)
        _extras(reply)
        if reply.steps:
            st.feedback(
                "thumbs",
                key=f"ai_feedback_{message['id']}",
                on_change=_feedback,
                args=(message["id"],),
            )


@st.dialog("Ask AI", position="right", width=560, icon=":material/auto_awesome:")
def ask_ai_drawer(context: str) -> None:
    """A chat about one card or page, with the toolbar filters as its scope."""
    filters = data.current_filters()
    chats: dict[str, list[dict[str, Any]]] = st.session_state.setdefault("ai_chats", {})
    messages = chats.setdefault(context, [])

    with st.container(horizontal=True, gap="small"):
        st.badge(_title(context), icon=":material/dashboard:", color="primary")
        st.badge(filters.describe(), icon=":material/filter_alt:", color="gray")

    if not messages:
        st.markdown(
            f"Ask about **{_title(context)}**. Answers use the same certified metrics "
            "and your current filters."
        )
    for message in messages:
        _render(message)

    if not messages:
        options = [QUESTIONS[i] for i in CONTEXTS.get(context, CONTEXTS["Overview"])]
    else:
        last = messages[-1]
        options = last["answer"].followups if last["role"] == "assistant" else []

    # The pills sit in a container inside an ``st.empty`` slot. While an answer
    # runs, the slot stays empty, which removes the pills right away instead of
    # leaving them faded on screen until the answer finishes streaming. A pick
    # is read from Session State before the pills would render.
    live = st.container()
    slot = st.empty()
    typed = st.chat_input(
        "Ask about this view",
        key=f"ai_input_{context}",
        submit_mode="disable",
    )
    pills_key = f"ai_pills_{context}_{len(messages)}"
    question = typed or st.session_state.get(pills_key)
    if not question:
        if options:
            with slot.container():
                st.pills(
                    "Suggestions", options, label_visibility="collapsed", key=pills_key
                )
        return

    with live:
        with st.chat_message("user"):
            st.markdown(question)
        reply = answer(question, context)
        message_id = uuid.uuid4().hex[:8]
        with st.chat_message("assistant", avatar=AVATAR):
            _steps(reply, live=True)
            st.write_stream(_words(reply.text), cursor=":primary[▍]")
            _extras(reply)
    messages.extend(
        [
            {"role": "user", "text": question},
            {"role": "assistant", "id": message_id, "answer": reply},
        ]
    )
    st.rerun(scope="fragment")
