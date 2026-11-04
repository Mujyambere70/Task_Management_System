"""
world.py - languages of the interface and dates written the way each language writes them.

Seven languages: the six official languages of the United Nations (English, Russian, French,
Spanish, Arabic, Chinese) and Kinyarwanda. Arabic pages are shown right to left.
Month and day names come from the Unicode CLDR data (Babel library).
"""

from contextvars import ContextVar
from datetime import date, datetime

from babel.dates import format_date

LANGUAGES = {
    "en": "English",
    "ru": "Русский",
    "fr": "Français",
    "es": "Español",
    "ar": "العربية",
    "zh": "中文",
    "rw": "Ikinyarwanda",
}
RTL_LANGUAGES = {"ar"}
BABEL_LOCALE = {"en": "en_GB", "ru": "ru", "fr": "fr", "es": "es", "ar": "ar", "zh": "zh_Hans_CN", "rw": "rw"}

_lang = ContextVar("lang", default="en")


def set_lang(lang):
    _lang.set(lang if lang in LANGUAGES else "en")


def current_lang():
    return _lang.get()


def best_language(accept_header_languages):
    """The first browser language we have, else English."""
    for code in accept_header_languages:
        code = (code or "").lower()[:2]
        if code in LANGUAGES:
            return code
    return "en"


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)[:10]).date()


def fmt_date(value, pattern="medium", lang=None):
    """A date in the current language: 'medium' -> 4 Nov 2026 / 4 нояб. 2026 г. / 2026年11月4日."""
    if not value:
        return ""
    lang = lang or _lang.get()
    try:
        return format_date(_as_date(value), pattern, locale=BABEL_LOCALE.get(lang, "en_GB"))
    except Exception:                       # a locale without that pattern: fall back to ISO
        return _as_date(value).isoformat()


def fmt_datetime(value, lang=None):
    """'2026-11-04 14:05:00' -> '4 Nov 2026, 14:05' in the current language."""
    if not value:
        return ""
    d = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    return f"{fmt_date(d, 'medium', lang)}, {d:%H:%M}"


def month_title(d, lang=None):
    return fmt_date(d, "LLLL y", lang).capitalize()


def weekday_names(lang=None):
    """Short names Monday..Sunday."""
    base = date(2024, 1, 1)                 # a Monday
    return [fmt_date(base.replace(day=1 + i), "EEE", lang) for i in range(7)]
