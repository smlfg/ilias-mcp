"""Geheimnisse (Passwort, Token) als eigene Klasse (A1/A4).

Ein `Secret` gibt sein `repr()`/`str()` nie preis: taucht ein Passwort in einem
Fehler-Traceback, einem Log oder einem `repr()` eines Dataclass auf, steht dort
nur `***`. Ausgepackt wird ausschließlich über `reveal()` an der Stelle, die den
Wert wirklich braucht (HTTP-Request).
"""

from __future__ import annotations

REDACTED = "***"


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __str__(self) -> str:
        return REDACTED

    def __repr__(self) -> str:
        return f"Secret({REDACTED})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Secret):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:  # pragma: no cover
        return hash((Secret, self._value))
