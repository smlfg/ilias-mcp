"""F5 `ilias download`: Dateien eines Kurses (oder eines Teilbaums) lokal speichern.

Reine Planung ohne I/O (``plan_files``) plus ein kleines Manifest im Zielordner, damit
ein zweiter Lauf nur neue oder geänderte Dateien lädt (Vorstufe zu F6 `sync`).

Lokale Struktur: ``<ziel>/<Kursname>/<Ordner>/…/<Datei>.<endung>``. Abschnitte/Objektblöcke
der Kursseite ("Inhalt", "Sitzungen" …) sind Anzeige-Gruppen und werden **nicht** zu
Ordnern. Namen werden für Dateisysteme bereinigt (kein ``/``, keine Steuerzeichen, NFC).
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .models import FileNode, FolderNode, Module, Section

MANIFEST_NAME = ".ilias-download.json"
_BAD_CHARS = re.compile(r'[\x00-\x1f\x7f\\:*?"<>|]')
_MAX_NAME = 180


@dataclass(frozen=True)
class PlannedFile:
    ref_id: int | None
    name: str
    #: Pfad relativ zum Kursordner, mit ``/`` getrennt (z. B. ``Übungsblätter/Blatt 2.pdf``)
    relpath: str
    #: Anzeigepfad in ILIAS, z. B. ``/Übungsblätter/Lösungen/``
    ilias_path: str
    fileurl: str
    size: int | None
    timemodified: str | None
    visible: bool


def safe_name(name: str) -> str:
    """Datei-/Ordnername für macOS, Linux und Windows (Inhalt bleibt erkennbar)."""

    text = unicodedata.normalize("NFC", name or "").strip()
    text = text.replace("/", "-")
    text = _BAD_CHARS.sub("_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        text = "_"
    return text[:_MAX_NAME]


def file_name(name: str, suffix: str | None) -> str:
    base = safe_name(name)
    if suffix:
        ext = "." + safe_name(suffix).lower()
        if not base.lower().endswith(ext):
            base = f"{base}{ext}"
    return base


def _file_from_module(module: Module, folder: tuple[str, ...]) -> PlannedFile | None:
    if module.modname != "file" or not (module.fileurl or module.url):
        return None
    return PlannedFile(
        ref_id=module.ref_id,
        name=unicodedata.normalize("NFC", module.name),
        relpath="/".join((*folder, file_name(module.name, module.suffix))),
        ilias_path="/",
        fileurl=module.fileurl or module.url or "",
        size=module.size,
        timemodified=module.timemodified,
        visible=module.visible,
    )


def _walk_children(nodes, folder: tuple[str, ...], ilias_path: str, out: list[PlannedFile]) -> None:
    for node in nodes or []:
        if isinstance(node, FolderNode):
            _walk_children(
                node.children, (*folder, safe_name(node.name)), f"{ilias_path}{node.name}/", out
            )
        elif isinstance(node, FileNode):
            out.append(
                PlannedFile(
                    ref_id=node.ref_id,
                    name=unicodedata.normalize("NFC", node.name),
                    relpath="/".join((*folder, file_name(node.name, node.suffix))),
                    ilias_path=ilias_path,
                    fileurl=node.fileurl,
                    size=node.size,
                    timemodified=node.timemodified,
                    visible=node.visible,
                )
            )


def plan_files(sections: list[Section]) -> list[PlannedFile]:
    """Alle Dateien des Baums in Ausgabe-Reihenfolge; doppelte Zielpfade werden eindeutig."""

    out: list[PlannedFile] = []
    for section in sections:
        for module in section.modules:
            planned = _file_from_module(module, ())
            if planned:
                out.append(planned)
            elif module.modname in ("fold", "grp") and module.children:
                _walk_children(module.children, (safe_name(module.name),), f"/{module.name}/", out)
    return _dedupe(out)


def _dedupe(files: list[PlannedFile]) -> list[PlannedFile]:
    seen: set[str] = set()
    result: list[PlannedFile] = []
    for planned in files:
        key = planned.relpath.lower()
        if key in seen:
            path = PurePosixPath(planned.relpath)
            tag = planned.ref_id if planned.ref_id is not None else len(seen)
            relpath = str(path.with_name(f"{path.stem} ({tag}){path.suffix}"))
            planned = PlannedFile(**{**planned.__dict__, "relpath": relpath})
            key = relpath.lower()
        seen.add(key)
        result.append(planned)
    return result


def select(files: list[PlannedFile], selector: str | None) -> list[PlannedFile]:
    """Teilmenge wählen: ref_id (Datei oder Ordner per Pfadpräfix) oder Pfadpräfix.

    Pfad ist case-insensitiv und relativ zum Kurs, z. B. ``Übungsblätter`` oder
    ``Übungsblätter/Blatt 2``. Leerer Selektor -> alle Dateien.
    """

    if not selector or not selector.strip():
        return files
    text = unicodedata.normalize("NFC", selector.strip()).strip("/")
    if text.isdigit():
        ref = int(text)
        return [f for f in files if f.ref_id == ref]
    needle = text.lower()
    hits = []
    for planned in files:
        rel = planned.relpath.lower()
        stem = rel.rsplit(".", 1)[0] if "." in rel.rsplit("/", 1)[-1] else rel
        ilias_full = f"{planned.ilias_path}{planned.name}".strip("/").lower()
        if (
            rel == needle
            or stem == needle
            or ilias_full == needle
            or rel.startswith(needle + "/")
            or ilias_full.startswith(needle + "/")
        ):
            hits.append(planned)
    return hits


# ---------------------------------------------------------------- Manifest
def load_manifest(course_dir: Path) -> dict[str, dict]:
    try:
        data = json.loads((course_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    files = data.get("files") if isinstance(data, dict) else None
    return files if isinstance(files, dict) else {}


def save_manifest(course_dir: Path, files: dict[str, dict]) -> None:
    course_dir.mkdir(parents=True, exist_ok=True)
    target = course_dir / MANIFEST_NAME
    tmp = target.with_name(target.name + ".part")
    tmp.write_text(json.dumps({"version": 1, "files": files}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, target)


def manifest_key(planned: PlannedFile) -> str:
    return str(planned.ref_id) if planned.ref_id is not None else planned.relpath


def is_current(planned: PlannedFile, course_dir: Path, manifest: dict[str, dict]) -> bool:
    """True, wenn die Datei schon da ist und sich laut ILIAS nicht geändert hat."""

    if not (course_dir / planned.relpath).is_file():
        return False
    entry = manifest.get(manifest_key(planned))
    if entry is None:
        # Datei liegt schon da (z. B. von Hand geladen): nicht überschreiben
        return True
    if entry.get("relpath") != planned.relpath:
        return False
    return entry.get("timemodified") == planned.timemodified
