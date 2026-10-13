"""Moodle-Backend: Login/Status/Logout über den offiziellen Moodle-Mobile-Webservice.

Geprüft am 08.10.2026 (siehe real-fixtures/NOTES.md, Abschnitt "HS Mannheim Moodle"):
`enablewebservices = 1`, `enablemobilewebservice = 1`, `typeoflogin = 1`
(Benutzername/Passwort), keine Identity Provider, kein 2FA.

Ablauf:
1. POST {base}/login/token.php            -> username, password, service=moodle_mobile_app
   Erfolg: {"token": ...}  ·  Fehler: {"error": ..., "errorcode": "invalidlogin"}
2. Verifikation: POST {base}/webservice/rest/server.php
   wstoken, wsfunction=core_webservice_get_site_info, moodlewsrestformat=json
   -> {"sitename", "username", "fullname", "userid", ...}  ·  {"exception": ..., "errorcode": "invalidtoken"}
3. Erst nach erfolgreicher Verifikation wird der Token gespeichert.
"""

from __future__ import annotations

import html
import re
import time
from typing import Any

from ..errors import (
    AuthenticationError,
    NetworkError,
    NotLoggedInError,
    ParserError,
    SessionExpiredError,
)
from ..http import HttpClient, json_body, json_value
from ..models import (
    Course,
    Credentials,
    FileNode,
    FolderNode,
    LogoutResult,
    Module,
    MoodleLoginResult,
    MoodleStatusResult,
    Section,
    SiteInfo,
    UrlNode,
)
from ..secrets import REDACTED
from ..timeutil import semester_from_timestamp, timestamp_to_iso
from ..tokens import TokenStore
from .base import Backend

TOKEN_PATH = "/login/token.php"
REST_PATH = "/webservice/rest/server.php"
SERVICE = "moodle_mobile_app"
SITE_INFO_FUNCTION = "core_webservice_get_site_info"
COURSES_FUNCTION = "core_enrol_get_users_courses"
CONTENTS_FUNCTION = "core_course_get_contents"
REST_FORMAT = "json"

# Fehlercodes, die einen abgelehnten Login bedeuten (Moodle: login/token.php)
_AUTH_ERROR_CODES = {"invalidlogin", "invaliduser", "invalidaccount"}
_DENIED_ERROR_CODES = {"accessexception", "nopermission", "nopermissions", "nopermtostore", "forbidden"}
_EXPIRED_ERROR_CODES = {"invalidtoken", "requireloginerror"}
# N2: Statusprüfung ist idempotent und darf einmal wiederholt werden
_SITE_INFO_ATTEMPTS = 2
_RETRY_BACKOFF_SECONDS = 0.5

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def scrub(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Entfernt Passwort/Token aus Servertexten (A1/A4)."""
    for secret in secrets:
        if secret and secret in text:
            text = text.replace(secret, REDACTED)
    return text


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _text(value: object) -> str:
    """Servertext säubern: HTML-Entities dekodieren (z. B. `&gt;`, `&amp;`)."""
    return html.unescape(_as_text(value))


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _natural_key(text: str) -> list[object]:
    """Natürliche, case-insensitive Sortierung: `blatt2` vor `blatt10`."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", text)]


def _plain_text(text: str, limit: int | None = None) -> str:
    """HTML entfernen, Entities dekodieren, Whitespace normalisieren, optional kürzen."""
    if not text:
        return ""
    cleaned = _WHITESPACE_RE.sub(" ", _HTML_TAG_RE.sub(" ", text)).strip()
    cleaned = html.unescape(cleaned)
    if limit is not None and len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip() + "…"
    return cleaned


def _file_node(content: dict[str, Any]) -> FileNode:
    size = _as_int(content.get("filesize"))
    return FileNode(
        name=_text(content.get("filename")) or "Datei",
        path=_as_text(content.get("filepath")) or "/",
        fileurl=_as_text(content.get("fileurl")),
        size=size,
        mimetype=_as_text(content.get("mimetype")) or None,
        timemodified=timestamp_to_iso(content.get("timemodified")),
    )


def _folder_tree(contents: list[dict[str, Any]], prefix: str = "/") -> list[Any]:
    """Datei-Inhalte anhand ihrer `filepath` zu verschachtelten Ordnerknoten bauen."""
    folders: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    files: list[Any] = []
    for content in contents:
        filepath = _as_text(content.get("filepath")) or "/"
        if not filepath.endswith("/"):
            filepath += "/"
        if filepath == prefix:
            files.append(_file_node(content))
            continue
        if not filepath.startswith(prefix):
            continue
        segment = filepath[len(prefix):].split("/", 1)[0]
        if not segment:
            continue
        if segment not in folders:
            folders[segment] = []
            order.append(segment)
        folders[segment].append(content)
    nodes: list[Any] = []
    for segment in sorted(order, key=_natural_key):
        sub = prefix + segment + "/"
        nodes.append(FolderNode(name=segment, path=sub, children=_folder_tree(folders[segment], sub)))
    files.sort(key=lambda node: _natural_key(node.name))
    nodes.extend(files)
    return nodes


def _children_from_contents(contents: list[dict[str, Any]], module_name: str = "") -> list[Any]:
    urls: list[Any] = []
    files: list[dict[str, Any]] = []
    for content in contents:
        ctype = content.get("type")
        if ctype == "url":
            name = module_name or _text(content.get("filename")) or "Link"
            urls.append(UrlNode(name=name, url=_as_text(content.get("fileurl"))))
        elif ctype == "file":
            files.append(content)
    return _folder_tree(files) + urls


class MoodleBackend(Backend):
    name = "moodle"
    supports_login = True

    def __init__(self, instance) -> None:
        super().__init__(instance)
        self.store = TokenStore(instance.key, instance.lms, instance.base_url)

    # -- Operationen ----------------------------------------------------
    def login(self, credentials: Credentials, otp_callback=None) -> MoodleLoginResult:
        del otp_callback  # Moodle-Mobile-Login: kein TOTP
        with HttpClient(self.base_url) as client:
            token = self._request_token(client, credentials)
            info = self._site_info(client, token, label="Verifikation des Webservice")
        # erst nach erfolgreicher Verifikation wird der Token gespeichert
        self.store.save(token, username=info.username or credentials.username)
        return MoodleLoginResult(
            instance=self.instance.key,
            lms=self.instance.lms,
            base_url=self.base_url,
            username=info.username or credentials.username,
            fullname=info.fullname,
            sitename=info.sitename,
            userid=info.userid,
            verified=True,
            token_stored=True,
        )

    def status(self) -> MoodleStatusResult:
        session = self.store.load()
        if session is None:
            raise NotLoggedInError(
                f"Keine gespeicherte Session für {self.instance.key}.",
                hint=f"Erst `ilias login --instance {self.instance.key}` aufrufen.",
            )
        try:
            with HttpClient(self.base_url) as client:
                info = self._site_info(client, session.token, label="Statusprüfung")
        except SessionExpiredError:
            self.store.delete()
            raise
        return MoodleStatusResult(
            instance=self.instance.key,
            lms=self.instance.lms,
            base_url=self.base_url,
            username=info.username,
            fullname=info.fullname,
            sitename=info.sitename,
            userid=info.userid,
            logged_in=True,
        )

    def logout(self) -> LogoutResult:
        removed = self.store.delete()
        return LogoutResult(instance=self.instance.key, lms=self.instance.lms, token_removed=removed)

    # -- F2: Kurse -------------------------------------------------------
    def courses(self) -> list[Course]:
        session = self._require_session()
        try:
            with HttpClient(self.base_url) as client:
                info = self._site_info(client, session.token, label="Kursliste")
                if info.userid is None:
                    raise ParserError("Kursliste: Benutzer-ID fehlt in core_webservice_get_site_info.")
                data = self._rest_call(
                    client, session.token, COURSES_FUNCTION, {"userid": info.userid}, label="Kursliste"
                )
        except SessionExpiredError:
            self.store.delete()
            raise
        if not isinstance(data, list):
            raise ParserError("Kursliste: core_enrol_get_users_courses lieferte kein Array.")
        courses = [self._course_from_json(item) for item in data if isinstance(item, dict)]
        courses.sort(key=lambda course: course.sort_key())
        return courses

    # -- F3: Kursinhalt --------------------------------------------------
    def course_contents(self, course_id: int, depth: int | None = None) -> list[Section]:
        del depth  # Moodle liefert den ganzen Baum; Kürzen übernimmt trim_sections
        session = self._require_session()
        try:
            with HttpClient(self.base_url) as client:
                data = self._rest_call(
                    client, session.token, CONTENTS_FUNCTION, {"courseid": course_id}, label="Kursinhalt"
                )
        except SessionExpiredError:
            self.store.delete()
            raise
        if not isinstance(data, list):
            raise ParserError("Kursinhalt: core_course_get_contents lieferte kein Array.")
        return [
            self._section_from_json(item, index)
            for index, item in enumerate(data)
            if isinstance(item, dict)
        ]

    def _require_session(self):
        session = self.store.load()
        if session is None:
            raise NotLoggedInError(
                f"Keine gespeicherte Session für {self.instance.key}.",
                hint=f"Erst `ilias login --instance {self.instance.key}` aufrufen.",
            )
        return session

    # -- Moodle-spezifisch ----------------------------------------------
    def _rest_call(
        self,
        client: HttpClient,
        token: str,
        function: str,
        params: dict[str, Any] | None = None,
        *,
        label: str,
        attempts: int = 1,
    ) -> Any:
        """REST-Aufruf mit Token im POST-Body; Moodle-Fehlerobjekte werden abgebildet."""
        payload: dict[str, str] = {
            "wstoken": token,
            "wsfunction": function,
            "moodlewsrestformat": REST_FORMAT,
        }
        if params:
            payload.update({key: str(value) for key, value in params.items()})
        data: Any = None
        for attempt in range(attempts):
            try:
                response = client.post_form(REST_PATH, payload, label=label)
            except NetworkError:
                if attempt == attempts - 1:
                    raise
                time.sleep(_RETRY_BACKOFF_SECONDS)
                continue
            data = json_value(response, label=label)
            if isinstance(data, dict):
                self._raise_for_error(data, label=label, secrets=(token,))
            return data
        raise ParserError(f"{label}: keine Antwort.")  # pragma: no cover

    def _course_from_json(self, item: dict[str, Any]) -> Course:
        course_id = _as_int(item.get("id"))
        if course_id is None:
            raise ParserError("Kursliste: Kurs ohne gültige id.")
        start = item.get("startdate")
        return Course(
            id=course_id,
            fullname=_text(item.get("fullname")),
            shortname=_text(item.get("shortname")),
            category=_as_int(item.get("category")),
            semester=semester_from_timestamp(start),
            visible=bool(item.get("visible", 1)),
            startdate=timestamp_to_iso(start),
            enddate=timestamp_to_iso(item.get("enddate")),
            url=f"{self.base_url}/course/view.php?id={course_id}",
        )

    def _section_from_json(self, item: dict[str, Any], index: int) -> Section:
        number = _as_int(item.get("section"))
        raw_modules = item.get("modules")
        modules = (
            [self._module_from_json(module) for module in raw_modules if isinstance(module, dict)]
            if isinstance(raw_modules, list)
            else []
        )
        return Section(
            id=_as_int(item.get("id")) or 0,
            number=number if number is not None else index,
            name=_text(item.get("name")),
            visible=bool(item.get("visible", 1)),
            uservisible=bool(item.get("uservisible", True)),
            modules=modules,
        )

    def _module_from_json(self, item: dict[str, Any]) -> Module:
        modname = _as_text(item.get("modname"))
        name = _text(item.get("name"))
        if modname == "label":
            name = _plain_text(name, limit=60)
        raw_contents = item.get("contents")
        contents = (
            [content for content in raw_contents if isinstance(content, dict)]
            if isinstance(raw_contents, list)
            else []
        )
        availability = _plain_text(_as_text(item.get("availabilityinfo"))) or None
        return Module(
            id=_as_int(item.get("id")) or 0,
            name=name,
            modname=modname,
            url=_as_text(item.get("url")) or None,
            visible=bool(item.get("visible", 1)),
            uservisible=bool(item.get("uservisible", True)),
            availability=availability,
            children=_children_from_contents(contents, name),
        )

    def _request_token(self, client: HttpClient, credentials: Credentials) -> str:
        label = "Login (login/token.php)"
        password = credentials.password.reveal()
        response = client.post_form(
            TOKEN_PATH,
            {"username": credentials.username, "password": password, "service": SERVICE},
            label=label,
        )
        data = json_body(response, label=label)
        self._raise_for_error(data, label=label, secrets=(password,))
        token = data.get("token")
        if not isinstance(token, str) or not token.strip():
            raise ParserError(f"{label}: Antwort enthält kein Token.")
        return token.strip()

    def _site_info(self, client: HttpClient, token: str, *, label: str) -> SiteInfo:
        """core_webservice_get_site_info: prüft den Token und liefert Konto + Plattform."""
        data = self._rest_call(
            client, token, SITE_INFO_FUNCTION, label=label, attempts=_SITE_INFO_ATTEMPTS
        )
        if not isinstance(data, dict):
            raise ParserError(f"{label}: core_webservice_get_site_info lieferte kein Objekt.")
        info = SiteInfo.from_moodle_json(data)
        if not (info.username or info.fullname or info.sitename):
            raise ParserError(f"{label}: Antwort enthält weder username noch fullname noch sitename.")
        return info

    def _raise_for_error(self, data: dict[str, Any], *, label: str, secrets: tuple[str, ...] = ()) -> None:
        """Moodle-Fehlerobjekte (errorcode/exception) auf unsere Fehler abbilden.

        `secrets` wird aus Servertexten entfernt (A1/A4): ein Server, der das
        Passwort oder den Token in einer Meldung zurückspiegelt, darf sie nicht
        bis in die CLI-Ausgabe durchschlagen.
        """
        errorcode = data.get("errorcode")
        error = data.get("error") or data.get("message") or data.get("exception")
        if not isinstance(errorcode, str) and not isinstance(error, str):
            return
        code = errorcode if isinstance(errorcode, str) else "unknown"
        detail = scrub(error if isinstance(error, str) else "", secrets)
        suffix = f": {detail}" if detail else ""
        if code in _EXPIRED_ERROR_CODES:
            raise SessionExpiredError(
                f"{label}: Token ungültig oder abgelaufen (errorcode {code}){suffix}.",
                hint="Erneut mit `ilias login` anmelden (kein automatischer Re-Login).",
            )
        if code in _AUTH_ERROR_CODES:
            raise AuthenticationError(
                f"Anmeldung abgelehnt (errorcode {code}){suffix}.",
                hint="Benutzername oder Passwort prüfen.",
            )
        if code in _DENIED_ERROR_CODES:
            raise AuthenticationError(
                f"{label}: Webservice verweigert (errorcode {code}){suffix}.",
                hint="Der Webservice 'moodle_mobile_app' muss auf der Moodle-Instanz aktiv sein.",
            )
        raise ParserError(f"{label}: unerwartete Moodle-Fehlermeldung '{code}'{suffix}.")
