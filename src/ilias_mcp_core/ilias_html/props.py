"""Objekt-Eigenschaften (Größe, Datum, Endung, Semester) als reine Funktionen.

Kein I/O. ``parse_size`` akzeptiert Punkt **und** Komma als Dezimaltrenner
(HHN live: Punkt, z. B. ``203.45 KB``), Basis 1024. Datumsangaben deutsch,
Ausgabe ISO 8601 in Europe/Berlin. Semester nur aus Titel bzw. Zeitraum, nie
aus Eigenschaften wie "Anmeldungsende".
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime, timedelta

from ..timeutil import _BERLIN, now

_WHITESPACE_RE = re.compile(r"\s+")
_SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(bytes?|kib|kb|mib|mb|gib|gb|tib|tb)\b", re.IGNORECASE)
_DATE_RE = re.compile(
    r"(\d{1,2})\.\s*(Jan|Feb|M(?:ä|ae)r|Mrz|Apr|Mai|Jun|Jul|Aug|Sep|Okt|Nov|Dez)\.?\s*(\d{4}),?\s*(\d{1,2}):(\d{2})",
    re.IGNORECASE,
)
_RELATIVE_RE = re.compile(r"^(Heute|Gestern|Morgen),\s*(\d{1,2}):(\d{2})$", re.IGNORECASE)
_ZEITRAUM_START_RE = re.compile(
    r"(\d{1,2})\.\s*(Jan|Feb|M(?:ä|ae)r|Mrz|Apr|Mai|Jun|Jul|Aug|Sep|Okt|Nov|Dez)\.?\s*(\d{4})",
    re.IGNORECASE,
)
_SUFFIX_RE = re.compile(r"^[A-Za-z0-9]{1,12}$")

_MONTHS = {
    "jan": 1, "feb": 2, "mär": 3, "maer": 3, "mrz": 3, "apr": 4, "mai": 5,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dez": 12,
}
_UNIT_FACTORS = {
    "byte": 1, "bytes": 1,
    "kb": 1024, "kib": 1024,
    "mb": 1024**2, "mib": 1024**2,
    "gb": 1024**3, "gib": 1024**3,
    "tb": 1024**4, "tib": 1024**4,
}


def decode_text(value: str | None) -> str:
    """HTML-Entities dekodieren, NFC-normalisieren, Leerraum normalisieren, nie kürzen."""

    if not value:
        return ""
    text = html.unescape(value)
    text = unicodedata.normalize("NFC", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def parse_size(text: str | None) -> int | None:
    """Dateigröße in Bytes; Punkt **und** Komma als Dezimaltrenner, Basis 1024."""

    if not text:
        return None
    match = _SIZE_RE.search(text)
    if not match:
        return None
    number = match.group(1).replace(",", ".")
    unit = match.group(2).lower()
    factor = _UNIT_FACTORS.get(unit)
    if factor is None:
        return None
    try:
        value = float(number)
    except ValueError:  # pragma: no cover - durch Regex ausgeschlossen
        return None
    return round(value * factor)


def parse_date(text: str | None, *, reference: datetime | None = None) -> str | None:
    """``D. Mon YYYY, HH:MM`` bzw. ``Heute/Gestern/Morgen, HH:MM`` -> ISO 8601 (Berlin)."""

    if not text:
        return None
    relative = _RELATIVE_RE.match(text.strip())
    if relative:
        day, hour, minute = relative.group(1).lower(), int(relative.group(2)), int(relative.group(3))
        base = reference or now()
        delta = {"heute": 0, "gestern": -1, "morgen": 1}[day]
        moment = (base + timedelta(days=delta)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return moment.isoformat(timespec="minutes")
    match = _DATE_RE.search(text)
    if not match:
        return None
    month = _MONTHS.get(match.group(2).lower())
    if month is None:
        return None
    try:
        moment = datetime(
            int(match.group(3)), month, int(match.group(1)),
            int(match.group(4)), int(match.group(5)), tzinfo=_BERLIN,
        )
    except ValueError:
        return None
    return moment.isoformat(timespec="minutes")


def parse_suffix(props: list[str]) -> str | None:
    """Endung = erste Eigenschaft, wenn sie wie eine Dateiendung aussieht."""

    for prop in props:
        text = (prop or "").strip()
        if _SUFFIX_RE.match(text):
            return text.lower()
        return None
    return None


def parse_file_props(props: list[str]) -> tuple[str | None, int | None, str | None, str | None]:
    """(suffix, size, size_text, timemodified) aus allen Eigenschaften, nicht per Position."""

    suffix = None
    for prop in props:
        if _SUFFIX_RE.match((prop or "").strip()):
            suffix = prop.strip().lower()
            break
    size = size_text = timemodified = None
    for prop in props:
        if size is None:
            parsed = parse_size(prop)
            if parsed is not None:
                size = parsed
                size_text = prop.strip()
                continue
        if timemodified is None:
            moment = parse_date(prop)
            if moment is not None:
                timemodified = moment
    return (suffix, size, size_text, timemodified)


# ------------------------------------------------------------------ Semester
def _expand_year(value: str) -> int:
    year = int(value)
    return 2000 + year if year < 100 else year


def semester_from_title(title: str | None) -> str | None:
    """Semester aus dem Titel (WiSe/SoSe-Formen laut Spec §5.3/§13), sonst ``None``."""

    if not title:
        return None
    text = title.replace("_", " ")
    match = re.search(r"(?:WiSe|WS|Wintersemester)\s*(\d{2,4})\s*[/\-]\s*(\d{2,4})", text, re.IGNORECASE)
    if match:
        start = _expand_year(match.group(1))
        end = _expand_year(match.group(2))
        return f"WiSe {start}/{str(end)[-2:]}"
    match = re.search(r"(\d{4})\s*(?:WiSe|WS|Wintersemester)", text, re.IGNORECASE)
    if match:
        start = int(match.group(1))
        return f"WiSe {start}/{str(start + 1)[-2:]}"
    match = re.search(r"(?:WiSe|WS|Wintersemester)\s*(\d{2,4})\s+(\d{2,4})", text, re.IGNORECASE)
    if match:
        start = _expand_year(match.group(1))
        end = _expand_year(match.group(2))
        return f"WiSe {start}/{str(end)[-2:]}"
    match = re.search(r"(?:SoSe|SS|Sommersemester)\s*(\d{2,4})", text, re.IGNORECASE)
    if match:
        return f"SoSe {_expand_year(match.group(1))}"
    match = re.search(r"(\d{4})\s*(?:SoSe|SS|Sommersemester)", text, re.IGNORECASE)
    if match:
        return f"SoSe {int(match.group(1))}"
    return None


def semester_from_zeitraum(value: str | None) -> str | None:
    """Semester aus der Startadresse eines Zeitraums (März–Aug = SoSe, sonst WiSe)."""

    if not value:
        return None
    match = _ZEITRAUM_START_RE.search(value)
    if not match:
        return None
    month = _MONTHS.get(match.group(2).lower())
    if month is None:
        return None
    year = int(match.group(3))
    if 3 <= month <= 8:
        return f"SoSe {year}"
    if month >= 9:
        return f"WiSe {year}/{str(year + 1)[-2:]}"
    return f"WiSe {year - 1}/{str(year)[-2:]}"
