"""Zeitstempel in ISO 8601 mit Zeitzone Europe/Berlin (N5)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

try:  # pragma: no cover - plattformabhängig
    from zoneinfo import ZoneInfo

    _BERLIN = ZoneInfo("Europe/Berlin")
except Exception:  # pragma: no cover - ohne tzdata (z. B. Windows ohne tzdata)
    _BERLIN = timezone(timedelta(hours=2), "CEST")


def now() -> datetime:
    return datetime.now(_BERLIN)


def now_iso() -> str:
    """ISO-8601-Zeitstempel in Europe/Berlin, z. B. 2026-10-08T09:12:33+02:00."""
    return now().isoformat(timespec="seconds")


def to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=_BERLIN)
    return value.isoformat(timespec="seconds")


def timestamp_to_iso(value: object) -> str | None:
    """Unix-Sekunden -> ISO 8601 in Europe/Berlin; 0/fehlend/ungültig -> None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    return to_iso(datetime.fromtimestamp(value, tz=_BERLIN))


def semester_from_timestamp(value: object) -> str | None:
    """Semesterkennung aus dem Kurs-Startdatum in Europe/Berlin ableiten.

    Mrz-Aug -> ``SoSe YYYY`` · Sep-Dez -> ``WiSe YYYY/YY+1`` ·
    Jan-Feb -> ``WiSe YYYY-1/YY``. 0/fehlend -> None.

    Beispiele: 2026-03-16 -> ``SoSe 2026`` · 2024-09-24 -> ``WiSe 2024/25`` ·
    2026-01-10 -> ``WiSe 2025/26``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    moment = datetime.fromtimestamp(value, tz=_BERLIN)
    year = moment.year
    month = moment.month
    if 3 <= month <= 8:
        return f"SoSe {year}"
    if month >= 9:
        return f"WiSe {year}/{str(year + 1)[-2:]}"
    return f"WiSe {year - 1}/{str(year)[-2:]}"


def semester_order(semester: str | None) -> int:
    """Vergleichswert für die Sortierung (größer = neuer). None -> -1.

    WiSe zählt innerhalb des Jahres später als SoSe: ``WiSe 2026/27`` > ``SoSe 2026``.
    """
    if not semester:
        return -1
    parts = semester.split()
    if len(parts) != 2 or not parts[1]:
        return -1
    kind, year_part = parts
    try:
        if kind == "SoSe":
            year = int(year_part)
            return year * 2
        if kind == "WiSe":
            year = int(year_part.split("/")[0])
            return year * 2 + 1
    except (ValueError, IndexError):
        return -1
    return -1
