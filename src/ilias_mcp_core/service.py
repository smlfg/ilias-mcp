"""Fassade der Kernbibliothek: Instanz auflösen, Backend wählen, Operationen ausführen.

Die CLI ruft nur diese Funktionen auf und formatiert die Rückgabewerte. Auch die
Kurs-Auflösung für `ls` (ID/Teilstring/Mehrdeutigkeit) und das Beschneiden des
Baums nach `--depth` gehören hierher, nicht in die CLI (ANFORDERUNGEN.md §1).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from . import download as dl
from .backends import get_backend
from .config import Instance, load_instance
from .errors import (
    CourseAmbiguousError,
    CourseNotFoundError,
    NotSupportedError,
    ParserError,
    PathNotFoundError,
    PermissionDeniedError,
)
from .models import (
    ContentNode,
    Course,
    CourseContentsResult,
    CoursesResult,
    Credentials,
    DownloadItem,
    DownloadResult,
    FolderNode,
    LogoutResult,
    Module,
    Section,
)

if TYPE_CHECKING:  # vermeidet einen Import-Zyklus (backends -> models -> ...)
    from .backends.base import Backend


@dataclass(frozen=True)
class Service:
    instance: Instance
    backend: Backend

    @property
    def key(self) -> str:
        return self.instance.key

    def login(self, credentials: Credentials, otp_callback=None):
        """Anmelden (die CLI fragt Benutzername/Passwort ab, der Core fragt nie)."""
        return self.backend.login(credentials, otp_callback)

    def status(self):
        return self.backend.status()

    def logout(self) -> LogoutResult:
        return self.backend.logout()

    # -- F2/F3 ----------------------------------------------------------
    def courses(self) -> CoursesResult:
        """Kurse des angemeldeten Nutzers, sortiert (F2)."""
        return CoursesResult(
            instance=self.instance.key,
            lms=self.instance.lms,
            courses=self.backend.courses(),
        )

    def ls(self, query: str, depth: int | None = None) -> CourseContentsResult:
        """Einen Kurs auflösen und seinen Inhalt als Baum liefern (F3)."""
        if not self.backend.supports_courses:
            # sofort die passende "nicht unterstützt"-Meldung für ls (nicht die von courses)
            self.backend.course_contents(0)
        course = resolve_course(self.backend.courses(), query, instance=self.instance.key)
        sections = trim_sections(self.backend.course_contents(course.id, depth), depth)
        return CourseContentsResult(
            instance=self.instance.key,
            lms=self.instance.lms,
            course=course.to_ref_dict(),
            sections=sections,
            depth=depth,
        )


    # -- F5 --------------------------------------------------------------
    def download(
        self,
        query: str,
        selector: str | None = None,
        *,
        out_dir: str | Path = ".",
        dry_run: bool = False,
        force: bool = False,
        progress=None,
    ) -> DownloadResult:
        """Dateien eines Kurses (oder eines Ordners/einer Datei darin) lokal speichern (F5).

        Lädt nur, was fehlt oder sich laut ILIAS geändert hat (Manifest im Kursordner);
        ``force`` lädt alles neu, ``dry_run`` plant nur. Einzelne verweigerte Dateien
        werden als ``failed`` gemeldet, der Rest läuft weiter; Session-/Netzfehler brechen ab.
        """
        if not self.backend.supports_download:
            raise NotSupportedError(
                f"`download` ist für {self.instance.lms} ({self.key}) noch nicht implementiert.",
                hint="Derzeit nur ILIAS-Instanzen (z. B. --instance hhn).",
            )
        course = resolve_course(self.backend.courses(), query, instance=self.instance.key)
        files = dl.plan_files(self.backend.course_contents(course.id, None))
        chosen = dl.select(files, selector)
        if selector and not chosen:
            raise PathNotFoundError(
                f"Keine Datei im Kurs {course.fullname!r} passt auf {selector!r}.",
                hint=f"`ilias ls {course.id} --instance {self.key}` zeigt Pfade und ref_ids.",
            )
        course_dir = Path(out_dir).expanduser().resolve() / dl.safe_name(course.fullname)
        manifest = dl.load_manifest(course_dir)
        items: list[DownloadItem] = []
        try:
            for planned in chosen:
                item = self._download_one(planned, course_dir, manifest, dry_run=dry_run, force=force)
                items.append(item)
                if progress:
                    progress(item)
        finally:
            if not dry_run and any(item.status == "downloaded" for item in items):
                dl.save_manifest(course_dir, manifest)
        return DownloadResult(
            instance=self.instance.key,
            lms=self.instance.lms,
            course=course.to_ref_dict(),
            target_dir=str(course_dir),
            selector=selector,
            dry_run=dry_run,
            items=items,
        )

    def _download_one(self, planned, course_dir: Path, manifest: dict, *, dry_run: bool, force: bool) -> DownloadItem:
        rel = f"{course_dir.name}/{planned.relpath}"
        base = {"ref_id": planned.ref_id, "name": planned.name, "path": rel, "timemodified": planned.timemodified}
        if not planned.visible:
            return DownloadItem(**base, status="skipped", size=planned.size, error_code="offline",
                                message="In ILIAS offline (nicht freigegeben).")
        if not force and dl.is_current(planned, course_dir, manifest):
            local_size = (course_dir / planned.relpath).stat().st_size
            return DownloadItem(**base, status="skipped", size=local_size)
        if dry_run:
            return DownloadItem(**base, status="planned", size=planned.size)
        try:
            got = self.backend.download_file(planned.fileurl, course_dir / planned.relpath, label=f"Datei {planned.name!r}")
        except (PermissionDeniedError, ParserError) as exc:
            return DownloadItem(**base, status="failed", error_code=exc.code, message=exc.message or str(exc))
        manifest[dl.manifest_key(planned)] = {
            "relpath": planned.relpath,
            "timemodified": planned.timemodified,
            "size": got.size,
        }
        return DownloadItem(**base, status="downloaded", size=got.size)


def _courses_hint(instance: str | None) -> str:
    if instance:
        return f"`ilias courses --instance {instance}` zeigt die verfügbaren Kurse (id, Kurzname, Name)."
    return "`ilias courses` zeigt die verfügbaren Kurse (id, Kurzname, Name)."


def resolve_course(courses: list[Course], query: str, *, instance: str | None = None) -> Course:
    """Kurs per ID oder (case-insensitivem) Teilstring von fullname/shortname finden.

    Eine Zahl, die keine ref_id einer Mitgliedschaft ist, wird als **Kursnummer**
    gesucht: erst im Titel, dann in der Beschreibung (ILIAS, Spec §13.1). Ein
    exakter Kurzname/Name schlägt einen bloßen Teilstring. Kein Treffer ->
    `CourseNotFoundError`, mehrere -> `CourseAmbiguousError` (Exit 1).
    """
    text = unicodedata.normalize("NFC", (query or "").strip())
    if not text:
        raise CourseNotFoundError("Kein Kurs angegeben.", hint=_courses_hint(instance))
    if text.isdigit():
        target = int(text)
        for course in courses:
            if course.id == target:
                return course
        matches = _course_number_matches(courses, text)
        if len(matches) == 1:
            return matches[0]
        if matches:
            return _ambiguous(text, matches, kind="Kursnummern", instance=instance)
    needle = text.lower()
    matches = [
        course
        for course in courses
        if needle in unicodedata.normalize("NFC", course.fullname).lower()
        or needle in unicodedata.normalize("NFC", course.shortname).lower()
    ]
    if not matches:
        raise CourseNotFoundError(f"Kein Kurs passt auf {text!r}.", hint=_courses_hint(instance))
    exact = [
        course
        for course in matches
        if unicodedata.normalize("NFC", course.shortname).lower() == needle
        or unicodedata.normalize("NFC", course.fullname).lower() == needle
    ]
    pool = exact or matches
    if len(pool) == 1:
        return pool[0]
    return _ambiguous(text, pool, instance=instance)


def _course_number_matches(courses: list[Course], number: str) -> list[Course]:
    """Kursnummer-Suche: erst im Titel, dann in der Beschreibung (Spec §13.1)."""

    title_matches = [course for course in courses if number in unicodedata.normalize("NFC", course.fullname)]
    if title_matches:
        return title_matches
    return [course for course in courses if number in unicodedata.normalize("NFC", course.description)]


def _ambiguous(
    text: str, pool: list[Course], *, kind: str = "Kurse", instance: str | None = None
) -> Course:
    listing = ", ".join(f"{c.id} ({c.shortname}: {c.fullname})" for c in pool)
    hint = "Eindeutige Kurs-ID oder einen exakten Kurznamen angeben."
    if instance:
        hint = f"Eindeutige Kurs-ID angeben (s. `ilias courses --instance {instance}`)."
    raise CourseAmbiguousError(
        f"Mehrere {kind} passen auf {text!r}: {listing}.",
        hint=hint,
        candidates=[course.to_ref_dict() for course in pool],
    )


def trim_sections(sections: list[Section], depth: int | None) -> list[Section]:
    """Baumtiefe anwenden: 1 = Abschnitte, 2 = +Module, 3 = +Dateien/erste Ordnerebene, ..."""
    if depth is None:
        return sections
    if depth < 1:  # pragma: no cover - die CLI lehnt N < 1 als Usage-Fehler ab
        return []
    result: list[Section] = []
    for section in sections:
        if depth < 2:
            result.append(replace(section, modules=[]))
            continue
        modules: list[Module] = []
        for module in section.modules:
            if depth < 3:
                # None = "nicht aufgeklappt" (Sitzung, Kurslink) bleibt None (Spec §6.2).
                modules.append(module if module.children is None else replace(module, children=[]))
            elif module.children is None:
                modules.append(module)
            else:
                modules.append(replace(module, children=_trim_children(module.children, 3, depth)))
        result.append(replace(section, modules=modules))
    return result


def _trim_children(nodes: list[ContentNode], level: int, depth: int) -> list[ContentNode]:
    if level > depth:
        return []
    result: list[ContentNode] = []
    for node in nodes:
        if isinstance(node, FolderNode):
            result.append(replace(node, children=_trim_children(node.children, level + 1, depth)))
        else:
            result.append(node)
    return result


def open_service(instance_key: str | None = None, config: dict | None = None) -> Service:
    instance = load_instance(instance_key, config)
    return Service(instance=instance, backend=get_backend(instance))
