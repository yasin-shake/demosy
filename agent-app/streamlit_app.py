"""Cairn Analyst: an AI analyst over a retailer's sales, demand, and inventory data.

An agent app: people ask questions in a chat, and the agent shows its work as
it goes. Its reasoning sits in a compact "Thought for N seconds" toggle over a
timeline of tool calls, the answer streams in, and each reply carries live
charts and metrics. The full analysis opens in a side drawer, the canvas, and
purchase orders the agent drafts are approved there by a person.

Demonstrates:
- ``st.chat_message`` with a custom avatar, and ``st.chat_input`` with file
  attachments and ``submit_mode="stop"``, so a long run can be stopped
- Recent chats in the sidebar as tertiary buttons with ``wrap=False``, in a
  ``st.container(gap=None)``, so long titles end in an ellipsis
- ``st.status(type="compact")`` over ``st.status(type="step")`` timelines, with
  ``:shimmer[...]`` labels while each step runs
- ``st.write_stream`` with a cursor, ``st.feedback`` on every answer, and
  follow-up suggestions with ``st.pills(wrap=False)`` above the input in
  ``st.bottom``
- A canvas in ``@st.dialog(position="right")`` with tabs, ``st.echarts_chart``
  bridges, ranges, and trends, ``st.metric`` sparklines, and an
  ``st.data_editor`` where people approve the agent's purchase orders
- Real profiles and charts for attached CSV, TSV, and JSON files
- A warm ivory light/dark theme with a serif for headings and a CVD-checked
  chart palette

No model is called: ``utils/agent.py`` plays back scripted analyses that
compute their numbers from synthetic data. Replace it with your model or agent
framework, and keep the ``Reply`` shape.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from utils import agent, ui
from utils.files import FILE_TYPES, Attachment

STATIC = Path(__file__).parent / "static"

st.set_page_config(
    page_title="Cairn Analyst",
    page_icon=STATIC / "icon.svg",
    layout="centered",
)

logo_variant = "dark" if st.context.theme.type == "dark" else "light"
st.logo(
    STATIC / f"logo-{logo_variant}.svg", icon_image=STATIC / "icon.svg", size="large"
)

SOURCES = pd.DataFrame(
    {
        "Source": [
            ":material/database: Sales",
            ":material/inventory_2: Inventory",
            ":material/model_training: Demand model",
        ],
        "Detail": [":gray[Certified]", ":gray[3 DCs]", ":gray[v3]"],
    }
)


def new_chat() -> None:
    if ui.messages():
        st.session_state.chat_id = ui.new_id()


def open_chat(chat_id: str) -> None:
    st.session_state.chat_id = chat_id


def retry() -> None:
    st.session_state.retry = True


with st.sidebar:
    st.button(
        "New chat", icon=":material/edit_square:", width="stretch", on_click=new_chat
    )
    recent = [(i, chat) for i, chat in reversed(ui.chats().items()) if chat]
    if recent:
        with st.container(gap="xsmall"):
            st.caption("Recent")
            with st.container(gap=None):
                for chat_id, chat in recent:
                    label = ui.title(chat)
                    st.button(
                        f"**{label}**" if chat_id == ui.chat_id() else label,
                        type="tertiary",
                        wrap=False,
                        key=f"chat_{chat_id}",
                        on_click=open_chat,
                        args=(chat_id,),
                    )
    with st.container(gap="xsmall"):
        st.caption("Connected data")
        st.table(SOURCES, border="horizontal", hide_index=True, hide_header=True)
    with st.popover("Settings", icon=":material/tune:", type="tertiary"):
        st.toggle("Show the agent's steps while it works", value=True, key="show_steps")
        st.segmented_control(
            "Pace", list(ui.PACES), default="Natural", required=True, key="pace"
        )
    st.caption(
        "Scripted answers, synthetic data, no model. Every number is computed in "
        "Python from that data."
    )
    st.caption(
        "Unofficial internal demo styled after Barclays' brand — not an "
        "official Barclays product or endorsement."
    )

# Created before the chat input, so the follow-ups sit right above it
followup_slot = st.bottom.empty()
submission = st.chat_input(
    "Ask about sales, demand, or inventory",
    accept_file="multiple",
    file_type=FILE_TYPES,
    max_upload_size=10,
    submit_mode="stop",
)

history = ui.messages()
text: str | None = None
attachments: list[Attachment] = []
retrying = (
    bool(st.session_state.pop("retry", False))
    and bool(history)
    and history[-1]["role"] == "user"
)

if submission is not None:
    if isinstance(submission, str):
        text = submission
    else:
        text = submission.text or ""
        attachments = [Attachment.from_upload(f) for f in submission.files or []]
elif retrying:
    text, attachments = history[-1]["text"], history[-1]["attachments"]

# A picked suggestion or follow-up is a prompt too. It's read from Session State
# before the pills render, so the pills are gone by the time the reply streams.
if text is None and not history and st.session_state.get("suggestion"):
    text = agent.SUGGESTIONS[st.session_state.suggestion]
latest = history[-1] if history else None
has_followups = bool(latest and "reply" in latest and latest["reply"].followups)
if text is None and latest and has_followups:
    picked = st.session_state.get(f"followup_{latest['id']}")
    text = latest["reply"].followups.get(picked) if picked else None
    if text is None:
        ui.followups(followup_slot, latest)

if not history and text is None:
    st.space("large")
    with st.container(horizontal_alignment="center", gap="small"):
        st.image(STATIC / "icon.svg", width=52)
        st.title(
            "What should we look into today?", anchor=False, text_alignment="center"
        )
        st.markdown(
            ":gray[Ask about Cairn's sales, demand, and inventory, or attach a CSV. "
            "I work from the same certified data as the dashboards.]",
            text_alignment="center",
        )
        st.space("small")
        st.pills(
            "Try asking",
            list(agent.SUGGESTIONS),
            label_visibility="collapsed",
            key="suggestion",
        )
elif history:
    with st.container(
        horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"
    ):
        st.markdown(f"**{ui.title(history)}**", width="content")
        with st.container(horizontal=True, width="content", gap="xsmall"):
            st.download_button(
                ":material/download:",
                ui.transcript(),
                file_name="cairn-analyst-chat.md",
                mime="text/markdown",
                type="tertiary",
                help="Download the conversation",
            )
            st.button(
                ":material/edit_square:",
                type="tertiary",
                help="New chat",
                on_click=new_chat,
            )

    for index, message in enumerate(history):
        last = index == len(history) - 1
        if message["role"] == "user":
            ui.render_user(message)
            if last and text is None:
                ui.stopped(retry)
        else:
            ui.render_assistant(message)

if text is not None:
    chats = ui.chats()
    chats[ui.chat_id()] = chats.pop(ui.chat_id())
    if not retrying:
        message = {
            "role": "user",
            "id": ui.new_id(),
            "text": text,
            "attachments": attachments,
        }
        history.append(message)
        ui.render_user(message)
    history.append(ui.respond(text, attachments))
    st.rerun()
