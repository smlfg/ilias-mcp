"""ILIAS-Backend: Adapter über :class:`ilias_core.client.IliasClient`.

Login (OIDC/Keycloak + TOTP für HHN, SAML/Shibboleth für Uni Mannheim), Verifikation
gegen das Dashboard, Session-Cookies im Keyring bzw. in einer 0600-Datei – alles aus
dem bestehenden ILIAS-Kern.

Kurse (F2) und Kursinhalt (F3) werden per HTML gelesen (Spec §5/§6/§13): die
Kursliste ausschließlich aus "Meine Kurse und Gruppen", Container-Seiten aus der
Legacy-Liste. Alle Parser sind reine Funktionen in
:mod:`ilias_core.ilias_html`; dieses Modul verbindet sie mit dem read-only
GET-Helfer und baut die Baum-Modelle.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urljoin

from ..client import IliasClient
from ..errors import ParserError
from ..ilias_html import container as container_parser
from ..ilias_html import membership as membership_parser
from ..ilias_html import props as props_parser
from ..ilias_html.fetch import Fetcher, safe_url
from ..models import (
    Course,
    Credentials,
    FileNode,
    FolderNode,
    ItemNode,
    LoginResult,
    LogoutResult,
    Module,
    Section,
    SessionStatus,
)
from .base import Backend

MEMBERSHIP_PATH = "/ilias.php?baseClass=ilmembershipoverviewgui"
CONTAINER_PATH = "/ilias.php?baseClass=ilrepositorygui&ref_id={ref}"

#: Typen, in die rekursiv abgestiegen wird ("Ordner": Ordner und Untergruppen).
CONTAINER_TYPES = ("fold", "grp")
#: Typen, die bewusst **nicht** aufgeklappt werden (``children: null``).
COLLAPSED_TYPES = ("sess", "crsr")


class IliasBackend(Backend):
    name = "ilias"
    supports_login = True
    supports_courses = True
    supports_download = True

    def __init__(self, instance, *, client: IliasClient | None = None) -> None:
        super().__init__(instance)
        self.client = client or IliasClient(instance)
        self._fetcher: Fetcher | None = None

    @property
    def uses_totp(self) -> bool:  # type: ignore[override]
        return self.client.uses_totp

    def login(
        self, credentials: Credentials, otp_callback: Callable[[], str] | None = None
    ) -> LoginResult:
        return self.client.login(credentials.username, credentials.password.reveal(), otp_callback)

    def login_with_browser(self) -> LoginResult:
        return self.client.login_with_browser()

    def status(self) -> SessionStatus:
        return self.client.status()

    def logout(self) -> LogoutResult:
        removed = self.client.logout()
        return LogoutResult(instance=self.instance.key, lms=self.instance.lms, token_removed=removed)

    # -- F2: Kurse -------------------------------------------------------
    def courses(self) -> list[Course]:
        fetcher = self._get_fetcher()
        page = fetcher.get(MEMBERSHIP_PATH, label="Meine Kurse und Gruppen")
        memberships = membership_parser.parse_memberships(page.html, self.base_url)
        if not memberships and not membership_parser.is_empty_membership(page.html):
            raise ParserError(
                f"Kursliste: erwartete Struktur fehlt in {safe_url(page.url)}.",
                hint=f"Mit `ilias courses --instance {self.instance.key}` erneut versuchen; Seite ggf. geändert.",
            )
        courses = [
            Course(
                id=membership.ref_id,
                fullname=membership.fullname,
                shortname="",
                semester=membership.semester,
                visible=membership.visible,
                url=membership.url,
                type=membership.type,
                description=membership.description,
            )
            for membership in memberships
        ]
        courses.sort(key=lambda course: course.sort_key())
        return courses

    # -- F3: Kursinhalt --------------------------------------------------
    def course_contents(self, course_id: int, depth: int | None = None) -> list[Section]:
        fetcher = self._get_fetcher()
        return self._load_sections(fetcher, course_id, level=2, depth=depth, visited={course_id})

    # -- F5: Download ----------------------------------------------------
    def download_file(self, fileurl: str, dest, *, label: str = "Datei"):
        """Eine Datei über ihren Titel-Link (``cmd=sendfile``) laden; Session wie bei ``ls``."""
        return self._get_fetcher().download(fileurl, dest, label=label)

    # -- Aufbau ----------------------------------------------------------
    def _get_fetcher(self) -> Fetcher:
        if self._fetcher is None:
            self._fetcher = Fetcher(self.client.config, self.client.store)
        return self._fetcher

    def _resolve_url(self, href: str) -> str:
        return urljoin(self.base_url + "/", href) if href else ""

    def _load_blocks(self, fetcher: Fetcher, ref: int, label: str):
        page = fetcher.get(CONTAINER_PATH.format(ref=ref), label=label, expect_ref=ref)
        blocks = container_parser.parse_container(page.html, self.base_url)
        if not blocks and not container_parser.is_empty_container(page.html):
            raise ParserError(
                f"{label}: erwartete Container-Struktur fehlt in {safe_url(page.url)}.",
                hint=f"`ilias ls <kurs> --instance {self.instance.key}` erneut versuchen; Seite ggf. geändert.",
            )
        return blocks

    def _load_sections(
        self, fetcher: Fetcher, ref: int, *, level: int, depth: int | None, visited: set[int]
    ) -> list[Section]:
        blocks = self._load_blocks(fetcher, ref, label="Kurs-/Gruppenseite")
        sections: list[Section] = []
        for index, block in enumerate(blocks):
            modules: list[Module] = []
            for item in block.items:
                modules.append(
                    self._module_from_item(fetcher, item, level=level, depth=depth, visited=visited)
                )
            sections.append(
                Section(
                    id=block.ref_id or (index + 1),
                    number=index,
                    name=block.name,
                    modules=modules,
                )
            )
        return sections

    def _module_from_item(
        self, fetcher: Fetcher, item, *, level: int, depth: int | None, visited: set[int]
    ) -> Module:
        typ = item.type
        name = props_parser.decode_text(item.name)
        children: list | None
        if typ in COLLAPSED_TYPES:
            children = None
        elif typ in CONTAINER_TYPES and item.ref_id and self._should_descend(level + 1, depth):
            children = self._folder_children(fetcher, item, level=level, depth=depth, visited=visited)
        else:
            children = []
        url = self._resolve_url(item.href) or None
        if typ == "file":
            suffix, size, size_text, timemodified = props_parser.parse_file_props(item.props)
            return Module(
                id=item.ref_id or 0,
                name=name,
                modname=typ,
                url=url,
                visible=not item.offline,
                children=children,
                ref_id=item.ref_id,
                size=size,
                size_text=size_text,
                suffix=suffix,
                timemodified=timemodified,
                fileurl=url,
            )
        return Module(
            id=item.ref_id or 0,
            name=name,
            modname=typ,
            url=url,
            visible=not item.offline,
            children=children,
            ref_id=item.ref_id,
        )

    def _folder_children(
        self, fetcher: Fetcher, item, *, level: int, depth: int | None, visited: set[int]
    ) -> list:
        name = props_parser.decode_text(item.name)
        base_path = f"/{name}/"
        if item.ref_id in visited:
            return []
        visited.add(item.ref_id)
        blocks = self._load_blocks(fetcher, item.ref_id, label="Ordnerseite")
        return self._nodes_from_blocks(
            fetcher, blocks, base_path=base_path, level=level + 1, depth=depth, visited=visited
        )

    def _nodes_from_blocks(
        self, fetcher: Fetcher, blocks, *, base_path: str, level: int, depth: int | None, visited: set[int]
    ) -> list:
        nodes: list = []
        for block in blocks:
            for item in block.items:
                nodes.append(
                    self._child_node(
                        fetcher, item, base_path=base_path, level=level, depth=depth, visited=visited
                    )
                )
        return nodes

    def _child_node(
        self, fetcher: Fetcher, item, *, base_path: str, level: int, depth: int | None, visited: set[int]
    ):
        typ = item.type
        name = props_parser.decode_text(item.name)
        visible = not item.offline
        if typ in CONTAINER_TYPES:
            path = f"{base_path}{name}/"
            children: list = []
            if item.ref_id and self._should_descend(level + 1, depth) and item.ref_id not in visited:
                visited.add(item.ref_id)
                blocks = self._load_blocks(fetcher, item.ref_id, label="Unterordnerseite")
                children = self._nodes_from_blocks(
                    fetcher, blocks, base_path=path, level=level + 1, depth=depth, visited=visited
                )
            return FolderNode(name=name, path=path, children=children, ref_id=item.ref_id, visible=visible)
        if typ == "file":
            suffix, size, size_text, timemodified = props_parser.parse_file_props(item.props)
            return FileNode(
                name=name,
                path=base_path,
                fileurl=self._resolve_url(item.href),
                size=size,
                suffix=suffix,
                size_text=size_text,
                timemodified=timemodified,
                ref_id=item.ref_id,
                visible=visible,
            )
        return ItemNode(
            name=name,
            modname=typ,
            ref_id=item.ref_id,
            url=self._resolve_url(item.href) or None,
            visible=visible,
            children=None,
        )

    @staticmethod
    def _should_descend(child_level: int, depth: int | None) -> bool:
        return depth is None or child_level <= depth
