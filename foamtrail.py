#!/usr/bin/env python3
"""FoamTrail — dial in espresso without guessing.

A single-file, standard-library-only command line tool that keeps a local log
of beans (name + roast date, so freshness is always one glance away) and the
shots pulled against them, and renders a terminal grind-versus-yield chart so
the next grind change has a direction.

State lives in a human-readable JSON file: ~/.foamtrail/state.json
(override the folder with FOAMTRAIL_DATA_DIR).

No network, no third-party packages, no accounts.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

APP_NAME = "FoamTrail"
__version__ = "1.0.0"

DATA_DIR_ENV = "FOAMTRAIL_DATA_DIR"
STATE_FILENAME = "state.json"
STATE_VERSION = 1

# --------------------------------------------------------------------------
# Design direction (committed): calm-precise, monospace, terminal dark,
# one accent — oklch(0.58 0.12 198) == #008f94. These hex tokens are the
# source of truth for every coloured surface; a terminal cannot parse
# oklch(), so the accent is carried here in hex.
# --------------------------------------------------------------------------
THEME = {
    "accent": "#008f94",  # primary status: fresh beans, chart markers, headers
    "accent_dim": "#00767a",  # secondary accent: axis rules, quiet headings
    "text": "#e6edf0",  # body text on a terminal-dark surface
    "muted": "#8fa3aa",  # labels, units, hints
    "fresh": "#008f94",
    "rested": "#d0a215",
    "stale": "#c4553f",
    "rule": "#3a4a50",  # box-drawing separators
}

# Freshness windows (D4: computed on read, never stored).
FRESH_MAX_DAYS = 7
STALE_AFTER_DAYS = 30

# Accepted input ranges — deliberately generous, still rejecting nonsense.
GRIND_MIN, GRIND_MAX = 1, 60  # clicks
DOSE_MIN, DOSE_MAX = 5.0, 40.0  # grams
YIELD_MIN, YIELD_MAX = 5.0, 150.0  # grams
BREW_MIN, BREW_MAX = 1.0, 120.0  # seconds
NAME_MAX_LEN = 60
NOTES_MAX_LEN = 200

# Chart geometry (D5: fixed 40x16 text grid).
CHART_WIDTH = 40
CHART_HEIGHT = 16
MARKER = "\u25cf"  # ● one shot
OVERLAP = "\u2736"  # ✶ two or more shots in the same cell

LOG_EXAMPLE = "foamtrail log --grind 12 --dose 18 --yield 36 --time 28"

# ANSI SGR sequences are zero-width; column maths must ignore them.
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")

ROOT_EPILOG = f"""\
examples:
  foamtrail beans add "House Blend" --roast 2025-06-28
  foamtrail beans
  {LOG_EXAMPLE} --notes "sweet, balanced"
  foamtrail log --bean "El Paraiso" --grind 14 --dose 18 --yield 40 --time 30
  foamtrail shots
  foamtrail chart

state: ~/.foamtrail/state.json  (set FOAMTRAIL_DATA_DIR to use another folder)
"""


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
class FoamTrailError(Exception):
    """A user-facing failure: printed as `error: ...` on stderr, exit code 1."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


# --------------------------------------------------------------------------
# Colour
# --------------------------------------------------------------------------
class Color:
    """Minimal truecolor helper; silently no-ops when colour is off."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def paint(self, text: str, hex_color: str) -> str:
        if not self.enabled or not text:
            return text
        red, green, blue = _hex_to_rgb(hex_color)
        return f"\x1b[38;2;{red};{green};{blue}m{text}\x1b[0m"

    def bold(self, text: str) -> str:
        if not self.enabled:
            return text
        return f"\x1b[1m{text}\x1b[0m"


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def make_color(no_color: bool) -> Color:
    if no_color or os.environ.get("NO_COLOR"):
        return Color(False)
    try:
        return Color(bool(sys.stdout.isatty()))
    except (AttributeError, ValueError):
        return Color(False)


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------
def display_width(text: str) -> int:
    """Width of `text` in terminal cells (ANSI colour and wide CJK handled)."""
    width = 0
    for char in ANSI_PATTERN.sub("", text):
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def pad(text: str, width: int, align: str = "left") -> str:
    gap = max(0, width - display_width(text))
    return " " * gap + text if align == "right" else text + " " * gap


def fmt_num(value: float) -> str:
    """18.0 -> '18', 18.5 -> '18.5'."""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}".rstrip("0").rstrip(".")


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]], aligns: Sequence[str]) -> str:
    """Aligned columns with a box-drawing rule — the signature look."""
    widths = []
    for index, header in enumerate(headers):
        widest = display_width(header)
        for row in rows:
            widest = max(widest, display_width(row[index]))
        widths.append(widest)
    header_line = "  ".join(pad(head.upper(), widths[i], aligns[i]) for i, head in enumerate(headers))
    body = [
        "  ".join(pad(cell, widths[i], aligns[i]) for i, cell in enumerate(row))
        for row in rows
    ]
    total = sum(widths) + 2 * (len(widths) - 1)
    rule = "\u2500" * total
    return "\n".join(line.rstrip() for line in [header_line, rule, *body])


def humanize_when(moment: datetime, now: datetime | None = None) -> str:
    """'just now' / '12 min ago' / '3 h ago' / '2 days ago' / ISO date."""
    now = now or datetime.now()
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 90:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h ago"
    days = hours // 24
    if days < 14:
        return f"{days} day{'s' if days != 1 else ''} ago"
    return moment.strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# Domain
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Bean:
    id: str
    name: str
    roast_date: date
    added_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "roast_date": self.roast_date.isoformat(),
            "added_at": self.added_at.isoformat(),
        }

    def as_json(self, today: date) -> dict[str, Any]:
        days = days_off_roast(self.roast_date, today)
        return {
            "id": self.id,
            "name": self.name,
            "roast_date": self.roast_date.isoformat(),
            "added_at": self.added_at.isoformat(timespec="seconds"),
            "days_off_roast": days,
            "freshness_band": freshness_band(days),
        }


@dataclass(frozen=True)
class Shot:
    id: str
    bean_id: str
    grind_clicks: int
    dose_g: float
    yield_g: float
    brew_seconds: float
    notes: str
    logged_at: datetime
    days_off_roast_at_log: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "bean_id": self.bean_id,
            "grind_clicks": self.grind_clicks,
            "dose_g": self.dose_g,
            "yield_g": self.yield_g,
            "brew_seconds": self.brew_seconds,
            "notes": self.notes,
            "logged_at": self.logged_at.isoformat(),
            "days_off_roast_at_log": self.days_off_roast_at_log,
        }

    def as_json(self, bean_name: str) -> dict[str, Any]:
        payload = self.to_dict()
        payload["bean"] = bean_name
        return payload


@dataclass
class State:
    beans: list[Bean] = field(default_factory=list)
    shots: list[Shot] = field(default_factory=list)

    def bean_by_id(self) -> dict[str, Bean]:
        return {bean.id: bean for bean in self.beans}

    def bean_name(self, bean_id: str) -> str:
        bean = self.bean_by_id().get(bean_id)
        return bean.name if bean else "(removed bean)"

    def newest_first(self) -> list[Shot]:
        """Newest shot first; ties keep the most recently appended on top."""
        indexed = list(enumerate(self.shots))
        indexed.sort(key=lambda pair: (pair[1].logged_at, pair[0]))
        return [shot for _, shot in reversed(indexed)]


def days_off_roast(roast: date, today: date | None = None) -> int:
    return ((today or date.today()) - roast).days


def freshness_band(days: int) -> str:
    if days <= FRESH_MAX_DAYS:
        return "fresh"
    if days <= STALE_AFTER_DAYS:
        return "rested"
    return "stale"


def band_color(band: str) -> str:
    return THEME.get(band, THEME["muted"])


def describe_freshness(days: int) -> str:
    band = freshness_band(days)
    unit = "day" if days == 1 else "days"
    return f"{days} {unit} off roast, {band}"


# --------------------------------------------------------------------------
# State file I/O
# --------------------------------------------------------------------------
def data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".foamtrail"


def state_path() -> Path:
    return data_dir() / STATE_FILENAME


def _read_date(value: Any, field_name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise FoamTrailError(
            f"the state file has an unreadable {field_name} ({value!r}).",
            hint="fix or delete the state file to start a fresh log.",
        ) from None


def _read_timestamp(value: Any, field_name: str) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise FoamTrailError(
            f"the state file has an unreadable {field_name} ({value!r}).",
            hint="fix or delete the state file to start a fresh log.",
        ) from None


def _as_tuple(kind: type | tuple[type, ...]) -> tuple[type, ...]:
    return kind if isinstance(kind, tuple) else (kind,)


def _type_name(kind: type | tuple[type, ...]) -> str:
    return " or ".join(item.__name__ for item in _as_tuple(kind))


def _require(mapping: Any, key: str, kind: type | tuple[type, ...]) -> Any:
    if not isinstance(mapping, dict) or key not in mapping:
        raise FoamTrailError(
            f"the state file is missing the {key!r} field of a record.",
            hint="fix or delete the state file to start a fresh log.",
        )
    value = mapping[key]
    kinds = _as_tuple(kind)
    if not isinstance(value, kinds) or (isinstance(value, bool) and bool not in kinds):
        raise FoamTrailError(
            f"the state file has a bad {key!r} value ({value!r}); expected {_type_name(kind)}."
        )
    return value


def bean_from_dict(raw: Any) -> Bean:
    return Bean(
        id=str(_require(raw, "id", str)),
        name=str(_require(raw, "name", str)),
        roast_date=_read_date(_require(raw, "roast_date", str), "roast_date"),
        added_at=_read_timestamp(_require(raw, "added_at", str), "added_at"),
    )


def shot_from_dict(raw: Any) -> Shot:
    return Shot(
        id=str(_require(raw, "id", str)),
        bean_id=str(_require(raw, "bean_id", str)),
        grind_clicks=int(_require(raw, "grind_clicks", int)),
        dose_g=float(_require(raw, "dose_g", (int, float))),
        yield_g=float(_require(raw, "yield_g", (int, float))),
        brew_seconds=float(_require(raw, "brew_seconds", (int, float))),
        notes=str(_require(raw, "notes", str)),
        logged_at=_read_timestamp(_require(raw, "logged_at", str), "logged_at"),
        days_off_roast_at_log=int(_require(raw, "days_off_roast_at_log", int)),
    )


def state_from_dict(raw: Any) -> State:
    if not isinstance(raw, dict):
        raise FoamTrailError(
            "the state file does not contain a FoamTrail log.",
            hint="fix or delete the state file to start a fresh log.",
        )
    beans_raw = raw.get("beans", [])
    shots_raw = raw.get("shots", [])
    if not isinstance(beans_raw, list) or not isinstance(shots_raw, list):
        raise FoamTrailError("the state file's 'beans' and 'shots' entries must be lists.")
    state = State(
        beans=[bean_from_dict(item) for item in beans_raw],
        shots=[shot_from_dict(item) for item in shots_raw],
    )
    known = set(state.bean_by_id())
    for shot in state.shots:
        if shot.bean_id not in known:
            raise FoamTrailError(
                f"the state file has a shot pointing at an unknown bean ({shot.bean_id}).",
                hint="fix or delete the state file to start a fresh log.",
            )
    return state


def state_to_dict(state: State) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "beans": [bean.to_dict() for bean in state.beans],
        "shots": [shot.to_dict() for shot in state.shots],
    }


def load_state(path: Path | None = None) -> State:
    """Load the log, creating an empty state file on first use (T1 acceptance)."""
    target = path or state_path()
    if not target.exists():
        state = State()
        save_state(state, target)
        return state
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FoamTrailError(
            f"could not read the FoamTrail state file at {target} ({exc}).",
            hint="check the file permissions, or delete the file to start a fresh log.",
        ) from exc
    if not text.strip():
        return State()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FoamTrailError(
            f"the FoamTrail state file at {target} is not valid JSON ({exc.msg}, line {exc.lineno}).",
            hint="fix or delete the state file to start a fresh log.",
        ) from exc
    return state_from_dict(raw)


def save_state(state: State, path: Path | None = None) -> None:
    """Write the log atomically so a crash mid-write cannot truncate it."""
    target = path or state_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        scratch = target.with_name(target.name + ".tmp")
        scratch.write_text(json.dumps(state_to_dict(state), indent=2) + "\n", encoding="utf-8")
        os.replace(scratch, target)
    except OSError as exc:
        raise FoamTrailError(
            f"could not write the FoamTrail state file at {target} ({exc}).",
            hint="check that the folder is writable, or set FOAMTRAIL_DATA_DIR elsewhere.",
        ) from exc


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------
# Input validation — every message names the offending field
# --------------------------------------------------------------------------
def parse_int_field(raw: str, field_name: str, low: int, high: int, unit: str) -> int:
    text = str(raw).strip()
    try:
        value = int(text, 10)
    except ValueError:
        raise FoamTrailError(
            f"{field_name} must be a whole number of {unit} between {low} and {high} (got {text!r})."
        ) from None
    if not low <= value <= high:
        raise FoamTrailError(
            f"{field_name} must be between {low} and {high} {unit} (got {value})."
        )
    return value


def parse_float_field(raw: str, field_name: str, low: float, high: float, unit: str) -> float:
    text = str(raw).strip()
    try:
        value = float(text)
    except ValueError:
        raise FoamTrailError(
            f"{field_name} must be a number of {unit} between {fmt_num(low)} and {fmt_num(high)} (got {text!r})."
        ) from None
    if not math.isfinite(value):
        raise FoamTrailError(f"{field_name} must be a finite number of {unit} (got {text!r}).")
    if not low <= value <= high:
        raise FoamTrailError(
            f"{field_name} must be between {fmt_num(low)} and {fmt_num(high)} {unit} (got {fmt_num(value)})."
        )
    return value


def parse_roast_date(raw: str, today: date | None = None) -> date:
    text = str(raw).strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise FoamTrailError(
            f"roast date must look like YYYY-MM-DD (got {text!r}).",
            hint="for example: --roast 2025-06-28",
        ) from None
    reference = today or date.today()
    if parsed > reference:
        raise FoamTrailError(
            f"roast date {parsed.isoformat()} is in the future (today is {reference.isoformat()}); "
            "a bag cannot be roasted after it is logged.",
        )
    return parsed


def parse_bean_name(raw: str) -> str:
    name = " ".join(str(raw).split())
    if not name:
        raise FoamTrailError("bean name must not be empty.")
    if len(name) > NAME_MAX_LEN:
        raise FoamTrailError(f"bean name must be at most {NAME_MAX_LEN} characters (got {len(name)}).")
    return name


def parse_notes(raw: str | None) -> str:
    notes = " ".join((raw or "").split())
    if len(notes) > NOTES_MAX_LEN:
        raise FoamTrailError(f"notes must be at most {NOTES_MAX_LEN} characters (got {len(notes)}).")
    return notes


def beans_add_hint() -> str:
    sample_roast = (date.today() - timedelta(days=14)).isoformat()
    return f'foamtrail beans add "House Blend" --roast {sample_roast}'


def resolve_bean(state: State, name: str | None) -> Bean:
    """Named bean, or the most recently added bag when no name is given (F1)."""
    if name is None:
        if not state.beans:
            raise FoamTrailError(
                "no beans registered yet, so there is nothing to pull a shot from.",
                hint=beans_add_hint(),
            )
        return _most_recent(state.beans)
    wanted = parse_bean_name(name)
    matches = [bean for bean in state.beans if bean.name.casefold() == wanted.casefold()]
    if not matches:
        known = ", ".join(sorted({bean.name for bean in state.beans})) or "none yet"
        raise FoamTrailError(
            f"no bean named {wanted!r} is registered.",
            hint=f"registered beans: {known}. Add a bag with `{beans_add_hint()}`.",
        )
    return _most_recent(matches)


def _most_recent(beans: Sequence[Bean]) -> Bean:
    """Newest by added_at; insertion order breaks a tie inside the same microsecond."""
    indexed = list(enumerate(beans))
    newest = max(indexed, key=lambda pair: (pair[1].added_at, pair[0]))
    return newest[1]


# --------------------------------------------------------------------------
# Chart rendering (F2)
# --------------------------------------------------------------------------
def _position(value: float, low: float, high: float, span: int) -> int:
    """Map `value` onto 0..span; a flat range sits in the middle."""
    if high == low or span <= 0:
        return max(span, 0) // 2
    return round((value - low) / (high - low) * span)


def _axis_value(row: int, height: int, low: int, high: int) -> int:
    """Grind value belonging to a chart row (row 0 is the top = coarsest)."""
    if high == low or height <= 1:
        return high
    ratio = (height - 1 - row) / (height - 1)
    return round(low + ratio * (high - low))


def _place(chars: list[str], start: int, text: str) -> None:
    """Write `text` into a fixed-width character canvas, ignoring overflow."""
    for offset, char in enumerate(text):
        position = start + offset
        if 0 <= position < len(chars):
            chars[position] = char


def render_chart(shots: Sequence[Shot], color: Color, width: int = CHART_WIDTH, height: int = CHART_HEIGHT) -> str:
    """Text scatter plot: grind clicks vertical, yield grams horizontal (D5)."""
    grinds = [shot.grind_clicks for shot in shots]
    yields_g = [shot.yield_g for shot in shots]
    grind_min, grind_max = min(grinds), max(grinds)
    yield_min, yield_max = min(yields_g), max(yields_g)

    cells: dict[tuple[int, int], int] = {}
    for shot in shots:
        column = _position(shot.yield_g, yield_min, yield_max, width - 1)
        row = (height - 1) - _position(shot.grind_clicks, grind_min, grind_max, height - 1)
        cells[(row, column)] = cells.get((row, column), 0) + 1

    gutter = max(len(str(grind_max)), 2)
    accent = color.paint(MARKER, THEME["accent"])
    overlap = color.paint(OVERLAP, THEME["accent_dim"])
    bar = color.paint("\u2502", THEME["rule"])
    corner = color.paint("\u2514", THEME["rule"])
    axis_rule = color.paint("\u2500" * width, THEME["rule"])
    plot_indent = " " * (gutter + 2)  # y-label gutter, one space, then the axis bar

    flat_grind = grind_min == grind_max
    labelled_rows = {height // 2} if flat_grind else {0, height // 2, height - 1}
    shot_word = "shot" if len(shots) == 1 else "shots"
    lines = [f"{color.bold('Grind size versus yield')} \u2014 {len(shots)} {shot_word}"]
    for row in range(height):
        label = ""
        if row in labelled_rows:
            label = pad(str(_axis_value(row, height, grind_min, grind_max)), gutter, "right")
        line = []
        for column in range(width):
            count = cells.get((row, column), 0)
            if count == 1:
                line.append(accent)
            elif count > 1:
                line.append(overlap)
            else:
                line.append(" ")
        lines.append(color.paint(label, THEME["muted"]) + pad("", gutter - len(label)) + " " + bar + "".join(line).rstrip())

    tick_line = list(" " * width)
    min_text, max_text = fmt_num(yield_min), fmt_num(yield_max)
    if min_text == max_text:
        _place(tick_line, max(0, (width - len(min_text)) // 2), min_text)
    else:
        _place(tick_line, 0, min_text)
        _place(tick_line, width - len(max_text), max_text)
        _place(tick_line, max(0, (width - len(fmt_num((yield_min + yield_max) / 2))) // 2),
               fmt_num((yield_min + yield_max) / 2))
    lines.append(" " * (gutter + 1) + corner + axis_rule)
    lines.append(plot_indent + "".join(tick_line).rstrip())

    grind_axis = (
        f"{grind_min} clicks on every shot"
        if flat_grind
        else f"{grind_min}\u2013{grind_max}, higher = coarser"
    )
    yield_axis = (
        f"{min_text} g on every shot"
        if min_text == max_text
        else f"{min_text}\u2013{max_text}"
    )
    lines.append(color.paint(
        f"\u2500\u2500 vertical: grind clicks ({grind_axis})  \u00b7  horizontal: yield grams ({yield_axis})",
        THEME["muted"],
    ))
    legend = f"{accent} = one shot"
    if any(count > 1 for count in cells.values()):
        legend += f"   {overlap} = two or more shots in the same cell"
    lines.append(legend)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_beans_list(args: argparse.Namespace, color: Color) -> int:
    state = load_state()
    today = date.today()
    if getattr(args, "json", False):
        print(json.dumps([bean.as_json(today) for bean in state.beans], indent=2))
        return 0
    if not state.beans:
        print("No beans registered yet.")
        print("Add the bag you are pulling from:")
        print(f"  {color.paint(beans_add_hint(), THEME['accent'])}")
        return 0

    shot_counts: dict[str, int] = {}
    for shot in state.shots:
        shot_counts[shot.bean_id] = shot_counts.get(shot.bean_id, 0) + 1
    rows = []
    for bean in sorted(state.beans, key=lambda item: item.roast_date, reverse=True):
        days = days_off_roast(bean.roast_date, today)
        band = freshness_band(days)
        rows.append(
            [
                bean.name,
                bean.roast_date.isoformat(),
                f"{days} d",
                color.paint(band, band_color(band)),
                str(shot_counts.get(bean.id, 0)),
            ]
        )
    print(render_table(
        ["Bean", "Roasted", "Off roast", "Band", "Shots"],
        rows,
        ["left", "left", "right", "left", "right"],
    ))
    print()
    print(color.paint(
        "fresh \u2264 7 days \u00b7 rested 8\u201330 days \u00b7 stale past 30 days off roast",
        THEME["muted"],
    ))
    return 0


def _stale_note(bean_name: str, days: int) -> str:
    return (
        f"note: {bean_name} is {days} days off roast \u2014 past the {STALE_AFTER_DAYS}-day freshness window "
        "(stale). Expect flatter, woody shots; a fresher bag will dial in differently."
    )


def cmd_beans_add(args: argparse.Namespace, color: Color) -> int:
    name = parse_bean_name(args.name)
    roast = parse_roast_date(args.roast)
    state = load_state()
    bean = Bean(id=new_id("bean"), name=name, roast_date=roast, added_at=datetime.now())
    state.beans.append(bean)
    save_state(state)
    days = days_off_roast(roast)
    band = freshness_band(days)
    if getattr(args, "json", False):
        print(json.dumps(bean.as_json(date.today()), indent=2))
        return 0
    print(
        color.paint(f"Added {bean.name}", THEME["accent"])
        + f" \u2014 roasted {roast.isoformat()}, {describe_freshness(days)}."
    )
    if band == "stale":
        print(color.paint(_stale_note(bean.name, days), THEME["stale"]))
    print(color.paint(f'Log a shot against it: {LOG_EXAMPLE} --bean "{bean.name}"', THEME["muted"]))
    return 0


def cmd_log(args: argparse.Namespace, color: Color) -> int:
    grind = parse_int_field(args.grind, "grind size", GRIND_MIN, GRIND_MAX, "clicks")
    dose = parse_float_field(args.dose, "dose", DOSE_MIN, DOSE_MAX, "grams")
    shot_yield = parse_float_field(args.yield_g, "yield", YIELD_MIN, YIELD_MAX, "grams")
    brew = parse_float_field(args.brew_seconds, "brew time", BREW_MIN, BREW_MAX, "seconds")
    notes = parse_notes(args.notes)

    state = load_state()
    bean = resolve_bean(state, args.bean)
    days = days_off_roast(bean.roast_date)
    shot = Shot(
        id=new_id("shot"),
        bean_id=bean.id,
        grind_clicks=grind,
        dose_g=dose,
        yield_g=shot_yield,
        brew_seconds=brew,
        notes=notes,
        logged_at=datetime.now(),
        days_off_roast_at_log=days,
    )
    state.shots.append(shot)
    save_state(state)

    if getattr(args, "json", False):
        print(json.dumps(shot.as_json(bean.name), indent=2))
        return 0

    band = freshness_band(days)
    print(
        color.paint(f"Logged shot {shot.id}", THEME["accent"])
        + f" against {bean.name} ({describe_freshness(days)})."
    )
    print(f"  grind   {grind} clicks")
    print(f"  dose    {fmt_num(dose)} g")
    print(f"  yield   {fmt_num(shot_yield)} g")
    print(f"  time    {fmt_num(brew)} s")
    if notes:
        print(f"  notes   {notes}")
    if band == "stale":
        print(color.paint(_stale_note(bean.name, days), THEME["stale"]))
    total = len(state.shots)
    print(color.paint(
        f"Next: `foamtrail chart` plots grind against yield for all {total} logged shot{'s' if total != 1 else ''}.",
        THEME["muted"],
    ))
    return 0


def cmd_shots(args: argparse.Namespace, color: Color) -> int:
    state = load_state()
    ordered = state.newest_first()
    if getattr(args, "json", False):
        print(json.dumps([shot.as_json(state.bean_name(shot.bean_id)) for shot in ordered], indent=2))
        return 0
    if not ordered:
        print("No shots logged yet.")
        print("Log your first shot:")
        print(f"  {color.paint(LOG_EXAMPLE, THEME['accent'])}")
        return 0

    rows = []
    for shot in ordered:
        rows.append(
            [
                state.bean_name(shot.bean_id),
                str(shot.grind_clicks),
                f"{fmt_num(shot.dose_g)} g",
                f"{fmt_num(shot.yield_g)} g",
                f"{fmt_num(shot.brew_seconds)} s",
                f"{shot.days_off_roast_at_log} d",
                humanize_when(shot.logged_at),
                shot.notes or "\u2014",
            ]
        )
    print(render_table(
        ["Bean", "Grind", "Dose", "Yield", "Time", "Roast", "Logged", "Notes"],
        rows,
        ["left", "right", "right", "right", "right", "right", "left", "left"],
    ))
    print()
    print(color.paint(
        "grind in clicks \u00b7 dose/yield in grams \u00b7 roast = days off roast when the shot was "
        "pulled \u00b7 newest first",
        THEME["muted"],
    ))
    return 0


def cmd_chart(args: argparse.Namespace, color: Color) -> int:
    state = load_state()
    shots = state.newest_first()
    if getattr(args, "json", False):
        payload: dict[str, Any] = {"shots": len(shots)}
        if shots:
            payload["grind_clicks"] = {
                "min": min(shot.grind_clicks for shot in shots),
                "max": max(shot.grind_clicks for shot in shots),
            }
            payload["yield_g"] = {
                "min": min(shot.yield_g for shot in shots),
                "max": max(shot.yield_g for shot in shots),
            }
            payload["points"] = [
                {
                    "bean": state.bean_name(shot.bean_id),
                    "grind_clicks": shot.grind_clicks,
                    "yield_g": shot.yield_g,
                }
                for shot in shots
            ]
        print(json.dumps(payload, indent=2))
        return 0
    if not shots:
        print("No shots to chart yet.")
        print("Log your first shot:")
        print(f"  {color.paint(LOG_EXAMPLE, THEME['accent'])}")
        return 0
    print(render_chart(shots, color))
    return 0


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------
def _color_parent() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--no-color",
        action="store_true",
        default=argparse.SUPPRESS,
        help="never colour output (NO_COLOR is honoured too)",
    )
    return parent


def _json_parent() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print machine-readable JSON instead of text",
    )
    return parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foamtrail",
        description=(
            "FoamTrail \u2014 an espresso dial-in log: register beans with their roast date, "
            "log the shots you pull, and chart grind size against yield."
        ),
        epilog=ROOT_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=argparse.SUPPRESS,
        help="never colour output (NO_COLOR is honoured too)",
    )
    subcommands = parser.add_subparsers(dest="command", metavar="<command>", title="commands")

    color_parent = _color_parent()
    json_parent = _json_parent()

    beans = subcommands.add_parser(
        "beans",
        parents=[color_parent, json_parent],
        help="register a bag of beans, or list beans with freshness",
        description="Register a bag of beans (`beans add`) or list every bean with days off roast.",
        epilog=(
            "examples:\n"
            '  foamtrail beans add "House Blend" --roast 2025-06-28\n'
            "  foamtrail beans\n"
            "  foamtrail beans --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    beans.set_defaults(handler=cmd_beans_list)
    beans_actions = beans.add_subparsers(dest="beans_command", metavar="<action>", title="actions")
    beans_add = beans_actions.add_parser(
        "add",
        parents=[color_parent, json_parent],
        help="register a bean with its roast date",
        description="Register a bag of beans with the date it was roasted.",
        epilog=(
            "examples:\n"
            '  foamtrail beans add "House Blend" --roast 2025-06-28\n'
            '  foamtrail beans add "El Paraiso Geisha" --roast 2025-05-20\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    beans_add.add_argument("name", help='bean name, e.g. "House Blend"')
    beans_add.add_argument(
        "--roast",
        required=True,
        metavar="YYYY-MM-DD",
        help="roast date (cannot be in the future)",
    )
    beans_add.set_defaults(handler=cmd_beans_add)

    log = subcommands.add_parser(
        "log",
        parents=[color_parent, json_parent],
        help="log a shot against a bean",
        description="Log one espresso shot against a bean. Defaults to the most recently added bean.",
        epilog=(
            "examples:\n"
            "  foamtrail log --grind 12 --dose 18 --yield 36 --time 28\n"
            '  foamtrail log --bean "El Paraiso Geisha" --grind 14 --dose 18 --yield 40 '
            '--time 30 --notes "jasmine, bright"\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    log.add_argument("--grind", required=True, metavar="CLICKS", help=f"grind size in clicks ({GRIND_MIN}-{GRIND_MAX})")
    log.add_argument("--dose", required=True, metavar="GRAMS", help=f"dose in grams ({fmt_num(DOSE_MIN)}-{fmt_num(DOSE_MAX)})")
    log.add_argument("--yield", dest="yield_g", required=True, metavar="GRAMS", help=f"yield in grams ({fmt_num(YIELD_MIN)}-{fmt_num(YIELD_MAX)})")
    log.add_argument("--time", dest="brew_seconds", required=True, metavar="SECONDS", help=f"brew time in seconds ({fmt_num(BREW_MIN)}-{fmt_num(BREW_MAX)})")
    log.add_argument("--bean", metavar="NAME", help="bean name (default: the most recently added bean)")
    log.add_argument("--notes", metavar="TEXT", help="optional tasting notes")
    log.set_defaults(handler=cmd_log)

    shots = subcommands.add_parser(
        "shots",
        parents=[color_parent, json_parent],
        help="list logged shots, newest first",
        description="List every logged shot with the settings used, newest first.",
        epilog="examples:\n  foamtrail shots\n  foamtrail shots --json\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    shots.set_defaults(handler=cmd_shots)

    chart = subcommands.add_parser(
        "chart",
        parents=[color_parent, json_parent],
        help="draw the grind-size versus yield chart",
        description=(
            "Draw a text scatter plot of every logged shot: grind clicks on the vertical "
            "axis, yield grams on the horizontal axis."
        ),
        epilog="examples:\n  foamtrail chart\n  foamtrail chart --json\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    chart.set_defaults(handler=cmd_chart)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "command", None) is None:
        parser.print_help(sys.stdout)
        return 0
    color = make_color(getattr(args, "no_color", False))
    try:
        return args.handler(args, color)
    except FoamTrailError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(f"hint: {exc.hint}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
