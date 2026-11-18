"""Files people attach in the chat input: real profiles, no model needed.

Tables are read with pandas and profiled for real: their columns, gaps, and a
time series to chart if there is one. Images and text get their basic facts.
Understanding what an image shows takes a vision model, which the demo
doesn't call.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import pandas as pd
from PIL import Image

from utils.format import DAY, pct

if TYPE_CHECKING:
    from streamlit.typing import UploadedFile

FILE_TYPES = ["csv", "tsv", "json", "txt", "md", "png", "jpg", "jpeg"]
PREFERRED_VALUES = (
    "revenue",
    "sales",
    "amount",
    "value",
    "total",
    "price",
    "units",
    "count",
)


@dataclass(frozen=True)
class Attachment:
    name: str
    type: str
    size: int
    data: bytes

    @classmethod
    def from_upload(cls, upload: UploadedFile) -> Attachment:
        return cls(upload.name, upload.type or "", upload.size, upload.getvalue())

    @property
    def extension(self) -> str:
        return self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""

    @property
    def is_image(self) -> bool:
        return self.extension in ("png", "jpg", "jpeg")

    @property
    def size_label(self) -> str:
        if self.size >= 1_000_000:
            return f"{self.size / 1_000_000:.1f} MB"
        return f"{max(self.size / 1_000, 0.1):.1f} kB"


@dataclass(frozen=True)
class Series:
    date: str
    value: str
    points: int
    frame: pd.DataFrame  # Two columns, the date and the value, sorted by date


@dataclass(frozen=True)
class Profile:
    name: str
    kind: Literal["table", "image", "text", "other"]
    summary: str
    frame: pd.DataFrame | None = None
    columns: pd.DataFrame | None = None
    series: Series | None = None
    image: bytes | None = None
    text: str | None = None


def read_table(attachment: Attachment) -> pd.DataFrame:
    buffer = io.BytesIO(attachment.data)
    if attachment.extension == "json":
        payload = json.loads(attachment.data)
        return pd.json_normalize(payload if isinstance(payload, list) else [payload])
    separator = "\t" if attachment.extension == "tsv" else None
    return pd.read_csv(buffer, sep=separator, engine="python")


def _date_columns(frame: pd.DataFrame) -> list[str]:
    dates = [c for c in frame.columns if pd.api.types.is_datetime64_any_dtype(frame[c])]
    for column in [c for c in frame.columns if pd.api.types.is_string_dtype(frame[c])]:
        parsed = pd.to_datetime(frame[column], errors="coerce", format="mixed")
        if parsed.notna().mean() >= 0.8:
            frame[column] = parsed
            dates.append(column)
    return dates


def _series(frame: pd.DataFrame, dates: list[str]) -> Series | None:
    numeric = list(frame.select_dtypes(include="number").columns)
    if not dates or not numeric:
        return None
    preferred = [
        column
        for word in PREFERRED_VALUES
        for column in numeric
        if word in str(column).lower()
    ]
    value = (preferred or numeric)[0]
    date = dates[0]
    series = frame[[date, value]].dropna().groupby(date, as_index=False)[value].sum()
    series = series.sort_values(date)
    if len(series) < 3:
        return None
    return Series(str(date), str(value), len(series), series)


def _column_profile(frame: pd.DataFrame) -> pd.DataFrame:
    def kind(column: str) -> str:
        dtype = frame[column].dtype
        if pd.api.types.is_datetime64_any_dtype(dtype):
            return "Date"
        if pd.api.types.is_bool_dtype(dtype):
            return "Yes/no"
        if pd.api.types.is_numeric_dtype(dtype):
            return "Number"
        return "Text"

    return pd.DataFrame(
        {
            "column": [str(c) for c in frame.columns],
            "type": [kind(c) for c in frame.columns],
            "missing": [float(frame[c].isna().mean()) for c in frame.columns],
            "distinct": [int(frame[c].nunique()) for c in frame.columns],
            "example": [
                str(frame[c].dropna().iloc[0]) if frame[c].notna().any() else ""
                for c in frame.columns
            ],
        }
    )


def profile(attachment: Attachment) -> Profile:
    if attachment.extension in ("csv", "tsv", "json"):
        try:
            frame = read_table(attachment)
        except (ValueError, UnicodeDecodeError, pd.errors.ParserError) as error:
            return Profile(
                attachment.name, "other", f"Couldn't read it as a table: {error}"
            )
        dates = _date_columns(frame)
        return Profile(
            attachment.name,
            "table",
            f"{len(frame):,} rows · {len(frame.columns)} columns · {attachment.size_label}",
            frame=frame,
            columns=_column_profile(frame),
            series=_series(frame, dates),
        )
    if attachment.is_image:
        with Image.open(io.BytesIO(attachment.data)) as image:
            width, height = image.size
        return Profile(
            attachment.name,
            "image",
            f"{width} × {height} px · {attachment.size_label}",
            image=attachment.data,
        )
    if attachment.extension in ("txt", "md"):
        text = attachment.data.decode("utf-8", errors="replace")
        words = len(text.split())
        lines = text.count("\n") + 1
        return Profile(
            attachment.name,
            "text",
            f"{words:,} words · {lines:,} lines · {attachment.size_label}",
            text=text,
        )
    return Profile(attachment.name, "other", attachment.size_label)


def number(value: float) -> str:
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.4g}"


def describe(profile: Profile, question: str) -> str:
    """The agent's answer about an attached file, computed from its profile."""
    if (
        profile.kind == "table"
        and profile.frame is not None
        and profile.columns is not None
    ):
        columns = profile.columns
        counts = columns["type"].value_counts()
        parts = [
            f"{counts.get(kind, 0)} {label if counts.get(kind, 0) == 1 else plural}"
            for kind, label, plural in (
                ("Number", "numeric", "numeric"),
                ("Date", "date", "dates"),
                ("Text", "text", "text"),
                ("Yes/no", "yes/no", "yes/no"),
            )
            if counts.get(kind, 0)
        ]
        answer = (
            f"**{profile.name} has {len(profile.frame):,} rows and "
            f"{len(columns)} columns:** {', '.join(parts)}."
        )
        gaps = columns[columns["missing"] > 0].sort_values("missing", ascending=False)
        if gaps.empty:
            answer += " No values are missing."
        else:
            worst = gaps.iloc[0]
            answer += (
                f" {len(gaps)} {'column has' if len(gaps) == 1 else 'columns have'} gaps; "
                f"`{worst['column']}` is missing the most ({worst['missing']:.0%})."
            )
        if profile.series is not None:
            series = profile.series.frame[profile.series.value]
            first, last = float(series.iloc[0]), float(series.iloc[-1])
            change = (last - first) / abs(first) if first else 0.0
            peak = profile.series.frame.loc[series.idxmax(), profile.series.date]
            answer += (
                f"\n\n`{profile.series.value}` runs from {number(first)} to {number(last)} "
                f"over {profile.series.points} dates ({pct(change)}), and peaks on "
                f"{pd.Timestamp(peak):%b {DAY}, %Y}. I charted it below."
            )
        answer += "\n\nOpen the canvas to see every row and the full column profile."
    elif profile.kind == "image":
        answer = (
            f"**{profile.name} is a {profile.summary.split(' · ')[0]} image.** Reading what "
            "it shows takes a vision model, which this demo doesn't call. With one, the "
            "same message could describe the chart or pull out its numbers."
        )
    elif profile.kind == "text":
        answer = (
            f"**{profile.name} has {profile.summary.split(' · ')[0]}.** Summarizing it "
            "takes a model, which this demo doesn't call, so here's how it starts:\n\n"
            + "\n".join(
                f"> {line}" for line in (profile.text or "").strip().splitlines()[:4]
            )
        )
    else:
        answer = f"**I received {profile.name}** ({profile.summary}), but can't read this kind of file."
    if question.strip() and profile.kind == "table":
        answer += " The demo profiles files instead of answering questions about them."
    return answer
