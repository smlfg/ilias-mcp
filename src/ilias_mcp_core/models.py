"""Strukturierte Datenmodelle der ILIAS-/Moodle-CLI (ANFORDERUNGEN.md §1).

Die Modelle enthalten bewusst **keine** Cookies, Passwörter, Tokens oder Secrets.
Sie sind die einzige Oberfläche, die an CLI oder MCP weitergegeben wird.

- ILIAS (Web-Session): ``LoginResult``, ``SessionStatus`` mit ``to_dict()``.
- Moodle (Webservice-Token): ``MoodleLoginResult``, ``MoodleStatusResult``, ``LogoutResult``.
- F2/F3 (backend-neutral): ``Course``, ``CoursesResult``, ``Section``, ``Module``,
  ``FolderNode``/``FileNode``/``UrlNode``, ``CourseContentsResult``.
- Fehler: ``ErrorResult`` (gemeinsame ``--json``-Form für alle Befehle).
`to_json_dict()` liefert die stabile Form für `--json`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .secrets import Secret
from .timeutil import now_iso, semester_order


@dataclass(frozen=True)
class LoginResult:
    """Ergebnis eines erfolgreichen, verifizierten Logins."""

    authenticated: bool
    base_url: str
    client_id: str
    method: str = "oidc-keycloak"
    message: str = ""
    instance: str = "hhn"
    verified: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SessionStatus:
    """Ergebnis einer Session-Gültigkeitsprüfung."""

    authenticated: bool
    base_url: str
    client_id: str
    message: str = ""
    instance: str = "hhn"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------- Moodle + F2/F3

@dataclass(frozen=True)
class SiteInfo:
    """Ergebnis von core_webservice_get_site_info (Moodle-REST)."""

    sitename: str
    username: str
    fullname: str
    userid: int | None = None
    siteurl: str | None = None
    release: str | None = None
    version: str | None = None
    lang: str | None = None

    @classmethod
    def from_moodle_json(cls, data: dict[str, Any]) -> SiteInfo:
        def _s(key: str) -> str | None:
            value = data.get(key)
            return value if isinstance(value, str) and value else None

        userid = data.get("userid")
        if not isinstance(userid, int) or isinstance(userid, bool):
            userid = None
        return cls(
            sitename=_s("sitename") or "",
            username=_s("username") or "",
            fullname=_s("fullname") or "",
            userid=userid,
            siteurl=_s("siteurl"),
            release=_s("release"),
            version=_s("version"),
            lang=_s("lang"),
        )


@dataclass(frozen=True)
class MoodleLoginResult:
    instance: str
    lms: str
    base_url: str
    username: str
    fullname: str
    sitename: str
    userid: int | None = None
    verified: bool = True
    token_stored: bool = True
    timestamp: str = field(default_factory=now_iso)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "command": "login",
            "instance": self.instance,
            "lms": self.lms,
            "base_url": self.base_url,
            "username": self.username,
            "fullname": self.fullname,
            "sitename": self.sitename,
            "userid": self.userid,
            "verified": self.verified,
            "token_stored": self.token_stored,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class MoodleStatusResult:
    instance: str
    lms: str
    base_url: str
    username: str
    fullname: str
    sitename: str
    userid: int | None = None
    logged_in: bool = True
    timestamp: str = field(default_factory=now_iso)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "command": "status",
            "instance": self.instance,
            "lms": self.lms,
            "base_url": self.base_url,
            "username": self.username,
            "fullname": self.fullname,
            "sitename": self.sitename,
            "userid": self.userid,
            "logged_in": self.logged_in,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class LogoutResult:
    instance: str
    lms: str
    token_removed: bool = False
    timestamp: str = field(default_factory=now_iso)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "command": "logout",
            "instance": self.instance,
            "lms": self.lms,
            "token_removed": self.token_removed,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class Credentials:
    """Benutzername + Passwort. `repr()` gibt das Passwort nie preis (A1)."""

    username: str
    password: Secret

    def __repr__(self) -> str:
        return f"Credentials(username={self.username!r}, password=***)"


@dataclass(frozen=True)
class ErrorResult:
    command: str
    error_code: str
    message: str
    exit_code: int
    error_type: str | None = None
    hint: str | None = None
    instance: str | None = None
    lms: str | None = None
    candidates: list[dict[str, Any]] | None = None
    timestamp: str = field(default_factory=now_iso)

    def to_json_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.error_code, "message": self.message}
        if self.error_type:
            error["type"] = self.error_type
        if self.hint:
            error["hint"] = self.hint
        if self.candidates:
            error["candidates"] = self.candidates
        data: dict[str, Any] = {
            "ok": False,
            "command": self.command,
            "instance": self.instance,
            "lms": self.lms,
            "error": error,
            "message": self.message,
            "exit_code": self.exit_code,
            "timestamp": self.timestamp,
        }
        return data


# --------------------------------------------------------------- F2/F3: Kurse
@dataclass(frozen=True)
class Course:
    """Ein Kurs aus core_enrol_get_users_courses (Moodle-REST)."""

    id: int
    fullname: str
    shortname: str
    category: int | None = None
    semester: str | None = None
    visible: bool = True
    startdate: str | None = None
    enddate: str | None = None
    url: str = ""
    #: ILIAS-Objekttyp (``crs``/``grp``); Moodle lässt das leer.
    type: str = ""
    #: Beschreibung (nur ILIAS; für die Kursnummern-Suche in ``resolve_course``).
    description: str = ""

    def to_json_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "fullname": self.fullname,
            "shortname": self.shortname,
            "category": self.category,
            "semester": self.semester,
            "visible": self.visible,
            "startdate": self.startdate,
            "enddate": self.enddate,
            "url": self.url,
        }
        if self.type:
            data["type"] = self.type
        return data

    def to_ref_dict(self) -> dict[str, Any]:
        return {"id": self.id, "fullname": self.fullname, "shortname": self.shortname}

    def sort_key(self) -> tuple[bool, int, str]:
        """Sortierung: Semester neueste zuerst, None zuletzt, dann fullname."""
        return (self.semester is None, -semester_order(self.semester), self.fullname.lower())


@dataclass(frozen=True)
class CoursesResult:
    instance: str
    lms: str
    courses: list[Course] = field(default_factory=list)
    timestamp: str = field(default_factory=now_iso)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "lms": self.lms,
            "count": len(self.courses),
            "courses": [course.to_json_dict() for course in self.courses],
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class FolderNode:
    name: str
    path: str
    children: list[ContentNode] = field(default_factory=list)
    ref_id: int | None = None
    visible: bool = True

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "type": "folder",
            "name": self.name,
            "path": self.path,
            "ref_id": self.ref_id,
            "visible": self.visible,
            "children": [child.to_json_dict() for child in self.children],
        }


@dataclass(frozen=True)
class FileNode:
    name: str
    path: str
    fileurl: str
    size: int | None = None
    mimetype: str | None = None
    timemodified: str | None = None
    ref_id: int | None = None
    suffix: str | None = None
    size_text: str | None = None
    visible: bool = True

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "type": "file",
            "name": self.name,
            "path": self.path,
            "ref_id": self.ref_id,
            "size": self.size,
            "size_text": self.size_text,
            "suffix": self.suffix,
            "mimetype": self.mimetype,
            "timemodified": self.timemodified,
            "fileurl": self.fileurl,
            "visible": self.visible,
        }


@dataclass(frozen=True)
class UrlNode:
    name: str
    url: str

    def to_json_dict(self) -> dict[str, Any]:
        return {"type": "url", "name": self.name, "url": self.url}


#: ILIAS-Weblink (Moodle nennt sein Pendant ``url``).
WEBLINK_MODNAME = "webr"


@dataclass(frozen=True)
class ItemNode:
    """Nicht-Datei/Ordner-Objekt innerhalb eines Ordners (Übung, Test, Forum …)."""

    name: str
    modname: str
    ref_id: int | None = None
    url: str | None = None
    visible: bool = True
    children: list[ContentNode] | None = None

    def to_json_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "type": "item",
            "name": self.name,
            "modname": self.modname,
            "ref_id": self.ref_id,
            "url": self.url,
            "visible": self.visible,
            "children": (
                None if self.children is None else [child.to_json_dict() for child in self.children]
            ),
        }
        if self.modname == WEBLINK_MODNAME:
            data["target_url"] = None
        return data


ContentNode = FolderNode | FileNode | UrlNode | ItemNode


@dataclass(frozen=True)
class Module:
    id: int
    name: str
    modname: str
    url: str | None = None
    visible: bool = True
    uservisible: bool = True
    availability: str | None = None
    #: ``None`` bedeutet "nicht aufgeklappt" (ILIAS-Sitzungen/Kurslinks).
    children: list[ContentNode] | None = field(default_factory=list)
    #: Datei-Eigenschaften nur an ILIAS-Datei-Modulen (Moodle lässt sie leer).
    size: int | None = None
    size_text: str | None = None
    suffix: str | None = None
    mimetype: str | None = None
    timemodified: str | None = None
    fileurl: str | None = None
    #: ILIAS: ref_id des Objekts (gleich ``id``, Spec §6.2); Moodle lässt es leer.
    ref_id: int | None = None

    def to_json_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id}
        if self.ref_id is not None:
            data["ref_id"] = self.ref_id
        data |= {
            "name": self.name,
            "modname": self.modname,
            "url": self.url,
            "visible": self.visible,
            "uservisible": self.uservisible,
            "availability": self.availability,
            "children": (
                None if self.children is None else [child.to_json_dict() for child in self.children]
            ),
        }
        for key in ("size", "size_text", "suffix", "mimetype", "timemodified", "fileurl"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        if self.modname == WEBLINK_MODNAME:
            # Ziel nur per Redirect von calldirectlink abrufbar; diese Runde nicht aufgelöst (§6.2).
            data["target_url"] = None
        return data


@dataclass(frozen=True)
class Section:
    id: int
    number: int
    name: str
    visible: bool = True
    uservisible: bool = True
    modules: list[Module] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "number": self.number,
            "name": self.name,
            "visible": self.visible,
            "uservisible": self.uservisible,
            "modules": [module.to_json_dict() for module in self.modules],
        }


@dataclass(frozen=True)
class CourseContentsResult:
    instance: str
    lms: str
    course: dict[str, Any]
    sections: list[Section] = field(default_factory=list)
    depth: int | None = None
    timestamp: str = field(default_factory=now_iso)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "lms": self.lms,
            "course": self.course,
            "depth": self.depth,
            "sections": [section.to_json_dict() for section in self.sections],
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------- F5: Download
#: Status je Datei: geladen, übersprungen (aktuell), geplant (--dry-run), fehlgeschlagen
DOWNLOAD_STATUSES = ("downloaded", "skipped", "planned", "failed")


@dataclass(frozen=True)
class DownloadItem:
    ref_id: int | None
    name: str
    #: lokaler Pfad relativ zum Zielordner (``<Kurs>/<Ordner>/<Datei>``)
    path: str
    status: str
    size: int | None = None
    timemodified: str | None = None
    error_code: str | None = None
    message: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "ref_id": self.ref_id,
            "name": self.name,
            "path": self.path,
            "status": self.status,
            "size": self.size,
            "timemodified": self.timemodified,
            "error_code": self.error_code,
            "message": self.message,
        }


@dataclass(frozen=True)
class DownloadResult:
    instance: str
    lms: str
    course: dict[str, Any]
    #: absoluter Zielordner des Kurses
    target_dir: str
    selector: str | None = None
    dry_run: bool = False
    items: list[DownloadItem] = field(default_factory=list)
    timestamp: str = field(default_factory=now_iso)

    def count(self, status: str) -> int:
        return sum(1 for item in self.items if item.status == status)

    @property
    def ok(self) -> bool:
        return self.count("failed") == 0

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "instance": self.instance,
            "lms": self.lms,
            "course": self.course,
            "target_dir": self.target_dir,
            "selector": self.selector,
            "dry_run": self.dry_run,
            "summary": {status: self.count(status) for status in DOWNLOAD_STATUSES},
            "items": [item.to_json_dict() for item in self.items],
            "timestamp": self.timestamp,
        }
