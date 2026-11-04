"""core.py - small things every part of TaskFlow uses."""

import re
from datetime import date, datetime


class TaskError(Exception):
    """A problem the user can fix - shown as a message on the page."""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_iso():
    return date.today().isoformat()


def parse_due(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise TaskError("Due date must be a valid date.")


def parse_duration(text):
    """'1.5' or '1,5' (hours), '90m', '2h', '1h 30m', '1:30' -> minutes. Empty -> None."""
    s = str(text or "").strip().lower().replace(",", ".")
    if not s:
        return None
    m = re.fullmatch(r"(\d{1,3}):([0-5]\d)", s)
    if m:
        minutes = int(m.group(1)) * 60 + int(m.group(2))
    else:
        m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?", s)
        if m and (m.group(1) or m.group(2)):
            minutes = round(float(m.group(1) or 0) * 60) + int(m.group(2) or 0)
        else:
            try:
                minutes = round(float(s) * 60)
            except (ValueError, OverflowError):              # also 'nan' and 'inf'
                raise TaskError("Write the time like 1.5, 90m or 1h 30m.")
    if not 1 <= minutes <= 1000 * 60:
        raise TaskError("The time must be between 1 minute and 1000 hours.")
    return minutes


# hour and minute signs in every language of the interface
TIME_UNITS = {"en": ("h", "m"), "ru": (" ч", " мин"), "fr": (" h", " min"), "es": (" h", " min"),
              "ar": (" س", " د"), "zh": ("小时", "分"), "rw": (" h", " min")}


def fmt_minutes(minutes, lang=None):
    """95 -> '1h 35m' (in Russian '1 ч 35 мин'), 60 -> '1h', 20 -> '20m'."""
    if lang is None:
        try:
            import world
            lang = world.current_lang()
        except ImportError:                         # pragma: no cover
            lang = "en"
    hs, ms = TIME_UNITS.get(lang, TIME_UNITS["en"])
    minutes = int(minutes or 0)
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}{hs} {m}{ms}"
    return f"{h}{hs}" if h else f"{m}{ms}"


def is_example_email(email):
    """Demo addresses (example.com, *.demo, *.test ...) belong to fictional people: never email them."""
    domain = str(email or "").rsplit("@", 1)[-1].lower()
    return (domain in ("example.com", "example.org", "example.net")
            or domain.endswith((".example", ".test", ".invalid", ".demo", ".localhost")))


def count_case(condition):
    """SUM(CASE ...) works the same on SQLite and PostgreSQL (SUM(a = b) does not)."""
    return f"COALESCE(SUM(CASE WHEN {condition} THEN 1 ELSE 0 END), 0)"
