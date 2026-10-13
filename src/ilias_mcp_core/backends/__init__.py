"""Backend-Registry: `lms = "moodle"` in der Config wählt das Backend.

ILIAS bleibt der Default für alle Instanzen ohne Angabe.
"""

from __future__ import annotations

from collections.abc import Callable

from ..config import BACKEND_ILIAS, BACKEND_MOODLE, Instance
from ..errors import ConfigError
from .base import Backend

_REGISTRY: dict[str, Callable[[Instance], Backend]] = {}


def register(name: str, factory: Callable[[Instance], Backend]) -> None:
    _REGISTRY[name] = factory


def available_backends() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def get_backend(instance: Instance) -> Backend:
    """Backend-Objekt für eine aufgelöste Instanz."""
    factory = _REGISTRY.get(instance.lms)
    if factory is None:
        raise ConfigError(
            f"Unbekanntes Backend {instance.lms!r} (bekannt: {', '.join(available_backends())})."
        )
    return factory(instance)


def _lazy_ilias(instance: Instance) -> Backend:
    from .ilias import IliasBackend

    return IliasBackend(instance)


def _lazy_moodle(instance: Instance) -> Backend:
    from .moodle import MoodleBackend

    return MoodleBackend(instance)


register(BACKEND_ILIAS, _lazy_ilias)
register(BACKEND_MOODLE, _lazy_moodle)
