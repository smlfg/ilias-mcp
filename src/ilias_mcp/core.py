"""Bridge to the ilias-cli core (``ilias_core``). All calls are blocking and read-only.

Only public-ish ilias_core building blocks are used: ``service.open_service``,
``config``, ``session.SessionStore``, ``tokens.store_for`` and the read-only GET
``ilias_html.fetch.Fetcher``. Login, logout, setup and download are never called.
"""

from __future__ import annotations

import os
import tomllib
from typing import Any

from ilias_core.auth.parsers import is_ilias_logged_in
from ilias_core.config import BACKEND_ILIAS, BUILTIN_INSTANCES, default_config_dir, load_config
from ilias_core.errors import ConfigError, IliasError, ParserError
from ilias_core.ilias_html.fetch import Fetcher
from ilias_core.service import Service, open_service
from ilias_core.session import SessionStore
from ilias_core.tokens import store_for

#: Dashboard page used to verify an ILIAS session (same check as ``ilias status``),
#: fetched through the redirect-guarded Fetcher so no IdP is ever contacted (CT-5).
DASHBOARD_PATH = "/ilias.php?baseClass=ilDashboardGUI"
#: Hard request budget per tool call (SEC-19). Applied via ilias-cli's own
#: ``ILIAS_CLI_MAX_REQUESTS`` unless the user configured a lower value.
DEFAULT_MAX_REQUESTS = 60
#: Maximum number of tree nodes returned by ilias_list_contents.
MAX_NODES = 1500


def apply_request_budget() -> None:
    raw = os.environ.get("ILIAS_CLI_MAX_REQUESTS", "")
    try:
        current = int(raw)
    except ValueError:
        current = 0
    if current <= 0 or current > DEFAULT_MAX_REQUESTS:
        os.environ["ILIAS_CLI_MAX_REQUESTS"] = str(DEFAULT_MAX_REQUESTS)


def _config_tables() -> dict[str, Any]:
    path = default_config_dir() / "config.toml"
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError("config.toml ungültig") from exc
    tables = data.get("instances")
    return tables if isinstance(tables, dict) else {}


def _session_file_present(cfg: Any) -> bool:
    if cfg.lms == BACKEND_ILIAS:
        return cfg.session_file.is_file()
    return store_for(cfg).path.is_file()


class Core:
    """Thin, injectable facade so tests can swap the service factory."""

    def __init__(self, service_factory=open_service) -> None:
        self._open = service_factory

    # -- helpers ----------------------------------------------------------
    def open(self, instance: str | None) -> Service:
        return self._open(instance)

    def secrets(self, svc: Service) -> list[str]:
        """Values that must never appear in any output (CT-1)."""
        cfg = svc.instance
        out: list[str] = []
        try:
            if cfg.lms == BACKEND_ILIAS:
                out.extend((SessionStore(cfg).load() or {}).values())
            else:
                stored = store_for(cfg).load()
                token = getattr(stored, "token", None)
                if isinstance(token, str):
                    out.append(token)
        except Exception:  # noqa: BLE001 - redaction is best effort, never fatal
            pass
        return out

    # -- tools ------------------------------------------------------------
    def list_instances(self) -> dict[str, Any]:
        tables = _config_tables()
        default = load_config().instance
        keys = list(BUILTIN_INSTANCES) + sorted(k for k in tables if k not in BUILTIN_INSTANCES)
        rows = []
        for key in keys:
            try:
                cfg = load_config(instance=key)
            except IliasError:
                continue
            rows.append({
                "key": cfg.instance,
                "lms": cfg.lms,
                "base_url": cfg.normalized_base_url,
                "label": cfg.label or "",
                "builtin": key in BUILTIN_INSTANCES,
                "session_file_present": _session_file_present(cfg),
            })
        return {"default_instance": default, "instances": rows}

    def get_status(self, svc: Service) -> dict[str, Any]:
        cfg = svc.instance
        if cfg.lms == BACKEND_ILIAS:
            fetcher = Fetcher(cfg, SessionStore(cfg))
            try:
                page = fetcher.get(DASHBOARD_PATH, label="Session-Prüfung")
            finally:
                fetcher.close()
            if not is_ilias_logged_in(page.html):
                raise ParserError("dashboard without logout marker")
        else:
            svc.status()
        return {
            "instance": cfg.instance,
            "lms": cfg.lms,
            "base_url": cfg.normalized_base_url,
            "authenticated": True,
        }

    def list_courses(self, svc: Service) -> dict[str, Any]:
        result = svc.courses()
        courses = [
            {
                "id": c.id,
                "title": c.fullname,
                "shortname": c.shortname or "",
                "type": c.type or "",
                "semester": c.semester,
                "visible": bool(c.visible),
                "url": c.url or "",
            }
            for c in result.courses
        ]
        return {"instance": result.instance, "lms": result.lms, "count": len(courses),
                "courses": courses}

    def list_contents(self, svc: Service, course: str, depth: int) -> dict[str, Any]:
        result = svc.ls(course, depth)
        sections, count, truncated = prune([s.to_json_dict() for s in result.sections], MAX_NODES)
        ref = result.course
        return {
            "instance": result.instance,
            "lms": result.lms,
            "course": {"id": int(ref.get("id") or 0), "title": str(ref.get("fullname") or ""),
                       "shortname": str(ref.get("shortname") or "")},
            "depth": depth,
            "sections": sections,
            "node_count": count,
            "truncated": truncated,
        }


def prune(sections: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], int, bool]:
    """Depth-first copy of the tree keeping at most ``limit`` nodes."""
    count = 0
    truncated = False

    def visit(node: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal count, truncated
        if count >= limit:
            truncated = True
            return None
        count += 1
        out = dict(node)
        for key in ("modules", "children"):
            kids = node.get(key)
            if isinstance(kids, list):
                kept = []
                for child in kids:
                    if isinstance(child, dict):
                        copy = visit(child)
                        if copy is None:
                            break
                        kept.append(copy)
                out[key] = kept
        return out

    kept_sections = []
    for section in sections:
        copy = visit(section)
        if copy is None:
            break
        kept_sections.append(copy)
    return kept_sections, count, truncated
