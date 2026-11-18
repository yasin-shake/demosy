"""Reads and writeback for Cairn Reorder desk, on SQLite through ``st.connection``.

This public demo gives every browser session its own database, seeded with
today's agent run, so one visitor's decisions never change what others see.
A real review queue shares one database: point ``connection`` at your
warehouse (for example ``st.connection("snowflake")``), keep the same
functions, and cache the reads with ``st.cache_data``, clearing them after
each write.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pandas as pd
from sqlalchemy import CursorResult, text

import streamlit as st
from streamlit.connections import SQLConnection
from utils import seed

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

ACTIONS = {"approve": "Approved", "hold": "Held", "reject": "Rejected"}
STATUSES = {"approve": "approved", "hold": "held", "reject": "rejected"}

# strftime's "no leading zero" flag is "-" on Linux/macOS (glibc) and "#" on
# Windows (MSVCRT); "%-d"/"%-I" raise ValueError there. Use these in a nested
# format spec, e.g. f"{dt:%b {DAY}}", to get "Mar 5" on every platform.
DAY = "%#d" if os.name == "nt" else "%-d"
HOUR = "%#I" if os.name == "nt" else "%-I"

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS proposals (
        id TEXT PRIMARY KEY, created_at TEXT, sku TEXT, product TEXT,
        category TEXT, location TEXT, supplier TEXT, new_supplier INTEGER,
        on_hand INTEGER, on_order INTEGER, weekly_forecast REAL,
        lead_time_days INTEGER, case_pack INTEGER, unit_cost REAL,
        proposed_qty INTEGER, confidence REAL, priority TEXT,
        demand_spike INTEGER, cutoff TEXT, reasoning TEXT, history TEXT,
        forecast TEXT, status TEXT, final_qty INTEGER, decided_by TEXT,
        decided_at TEXT, note TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT,
        decided_at TEXT, reviewer TEXT, action TEXT, product TEXT,
        category TEXT, location TEXT, agent_qty INTEGER, final_qty INTEGER,
        value REAL, note TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS policies (
        category TEXT PRIMARY KEY, auto_approve_limit REAL,
        order_cap REAL, max_change_pct REAL, min_confidence REAL,
        updated_by TEXT, updated_at TEXT
    )
    """,
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
]


_STALE_AFTER_S = 24 * 60 * 60


def _database_dir() -> Path:
    # Tests point this at a temporary folder with REORDER_DESK_DB_DIR.
    folder = Path(
        os.environ.get("REORDER_DESK_DB_DIR")
        or Path(tempfile.gettempdir()) / "cairn-reorder-desk"
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _remove_stale_databases(folder: Path) -> None:
    cutoff = time.time() - _STALE_AFTER_S
    for path in folder.glob("*.db"):
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)


def connection() -> SQLConnection:
    """This session's database, created on first use."""
    if "database" not in st.session_state:
        folder = _database_dir()
        _remove_stale_databases(folder)
        st.session_state.database = str(folder / f"{uuid.uuid4().hex}.db")
    path = Path(st.session_state.database)
    # One connection per session, closed after an idle hour or when 500
    # newer sessions have opened theirs. A closed one reopens its file.
    return st.connection(
        f"reorder_desk_{path.stem}",
        type="sql",
        url=f"sqlite:///{path}",
        max_entries=500,
        ttl=3600,
    )


def _insert(session: Session, table: str, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    columns = list(rows[0])
    session.execute(
        text(
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({', '.join(f':{column}' for column in columns)})"
        ),
        rows,
    )


def reset_demo(today: date | None = None) -> None:
    """Replace everything with a fresh agent run and a month of history."""
    today = today or date.today()
    with connection().session as session:
        for statement in _SCHEMA:
            session.execute(text(statement))
        for table in ("proposals", "decisions", "policies", "meta"):
            session.execute(text(f"DELETE FROM {table}"))
        _insert(session, "proposals", seed.proposals(today))
        _insert(session, "decisions", seed.decision_history(today))
        _insert(
            session,
            "policies",
            [
                {
                    "category": category,
                    "auto_approve_limit": auto,
                    "order_cap": cap,
                    "max_change_pct": change,
                    "min_confidence": confidence,
                    "updated_by": "Cairn planning",
                    "updated_at": f"{today:%Y-%m-01}T09:00:00",
                }
                for category, (
                    auto,
                    cap,
                    change,
                    confidence,
                ) in seed.DEFAULT_POLICIES.items()
            ],
        )
        _insert(session, "meta", [{"key": "seeded_for", "value": today.isoformat()}])
        session.commit()


def ensure_ready() -> None:
    """Seed this session's database with today's agent run, so cutoffs are current."""
    with connection().session as session:
        for statement in _SCHEMA:
            session.execute(text(statement))
        session.commit()
        seeded = session.execute(
            text("SELECT value FROM meta WHERE key = 'seeded_for'")
        ).scalar()
    if seeded != date.today().isoformat():
        reset_demo()


# Each session reads its own small SQLite file, so the reads aren't cached.
def _read(sql: str) -> pd.DataFrame:
    with connection().engine.connect() as db:
        return pd.read_sql(text(sql), db)


def load_proposals() -> pd.DataFrame:
    frame = _read("SELECT * FROM proposals")
    for column in ("created_at", "cutoff", "decided_at"):
        frame[column] = pd.to_datetime(frame[column])
    for column in ("reasoning", "history", "forecast"):
        frame[column] = frame[column].map(json.loads)
    return frame.set_index("id")


def load_decisions() -> pd.DataFrame:
    frame = _read("SELECT * FROM decisions ORDER BY decided_at DESC")
    frame["decided_at"] = pd.to_datetime(frame["decided_at"])
    return frame


def load_policies() -> pd.DataFrame:
    frame = _read("SELECT * FROM policies")
    frame["updated_at"] = pd.to_datetime(frame["updated_at"])
    return frame.set_index("category").loc[seed.CATEGORIES]


@dataclass(frozen=True)
class Decision:
    proposal_id: str
    action: str  # A key of ACTIONS
    final_qty: int
    note: str


def record_decisions(decisions: list[Decision], reviewer: str) -> tuple[int, int]:
    """Write decisions back and log them. Returns (written, already decided).

    A proposal that is already decided is skipped, so a repeated submit or a
    second planner on a shared database can't decide it twice.
    """
    proposals = load_proposals()
    now = datetime.now().isoformat(timespec="seconds")
    written = 0
    with connection().session as session:
        for decision in decisions:
            row = proposals.loc[decision.proposal_id]
            action = ACTIONS[decision.action]
            if action == "Approved" and decision.final_qty != row["proposed_qty"]:
                action = "Approved with changes"
            final = 0 if decision.action == "reject" else decision.final_qty
            result = session.execute(
                text(
                    "UPDATE proposals SET status = :status, final_qty = :final, "
                    "decided_by = :reviewer, decided_at = :now, note = :note "
                    "WHERE id = :id AND status IN ('pending', 'held')"
                ),
                {
                    "status": STATUSES[decision.action],
                    "final": final,
                    "reviewer": reviewer,
                    "now": now,
                    "note": decision.note or None,
                    "id": decision.proposal_id,
                },
            )
            if not cast("CursorResult[Any]", result).rowcount:
                continue
            written += 1
            _insert(
                session,
                "decisions",
                [
                    {
                        "proposal_id": decision.proposal_id,
                        "decided_at": now,
                        "reviewer": reviewer,
                        "action": action,
                        "product": row["product"],
                        "category": row["category"],
                        "location": row["location"],
                        "agent_qty": int(row["proposed_qty"]),
                        "final_qty": final,
                        "value": final * float(row["unit_cost"]),
                        "note": decision.note or None,
                    }
                ],
            )
        session.commit()
    return written, len(decisions) - written


def save_policies(policies: pd.DataFrame, reviewer: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with connection().session as session:
        for category, row in policies.iterrows():
            session.execute(
                text(
                    "UPDATE policies SET auto_approve_limit = :auto, order_cap = :cap, "
                    "max_change_pct = :change, min_confidence = :confidence, "
                    "updated_by = :reviewer, updated_at = :now WHERE category = :category"
                ),
                {
                    "auto": float(row["auto_approve_limit"]),
                    "cap": float(row["order_cap"]),
                    "change": float(row["max_change_pct"]),
                    "confidence": float(row["min_confidence"]),
                    "reviewer": reviewer,
                    "now": now,
                    "category": category,
                },
            )
        session.commit()
