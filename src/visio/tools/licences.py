"""Inventaire des licences des dépendances (étape 21a) : `uv run task licences`.

Lit les métadonnées **installées** (Python : dépendances du projet, extra `transcription` compris,
et leurs dépendances transitives ; hors outils de développement) et `web/package-lock.json`
(npm : paquets de production, hors `dev`). Écrit les deux tableaux dans `docs/LICENCES-AUTO.md` et
signale à l'écran toute licence à copyleft ou inconnue, à trancher dans `docs/LICENCES.md`.

    uv sync --extra transcription      # pour que le moteur de transcription soit inventorié
    uv run task licences
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

COPYLEFT = re.compile(r"\b(A?GPL|LGPL|SSPL|EUPL|MPL|CPAL|OSL|CC-BY-SA)", re.IGNORECASE)
UNKNOWN = "INCONNUE"
ROOT_EXTRAS = ("transcription",)


def license_of(meta: metadata.PackageMetadata) -> str:
    """Licence d'une distribution : expression SPDX, sinon champ `License`, sinon classifiers."""
    expression = meta.get("License-Expression")
    if expression:
        return str(expression).strip()
    text = (meta.get("License") or "").strip()
    if text and len(text) <= 60 and "\n" not in text:
        return text
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::") and not c.endswith("OSI Approved")
    ]
    return " / ".join(classifiers) if classifiers else UNKNOWN


def is_copyleft(license_text: str) -> bool:
    return bool(COPYLEFT.search(license_text))


def python_runtime(root: str = "eduvia-visio") -> dict[str, tuple[str, str]]:
    """{nom: (version, licence)} des dépendances d'exécution, transitives comprises."""
    found: dict[str, tuple[str, str]] = {}
    todo: list[tuple[str, tuple[str, ...]]] = [(root, ROOT_EXTRAS)]
    seen: set[str] = set()
    while todo:
        name, extras = todo.pop()
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue  # extra ou plateforme non installés ici : signalé par l'absence
        if key != re.sub(r"[-_.]+", "-", root).lower():
            found[dist.metadata["Name"]] = (dist.version, license_of(dist.metadata))
        for raw in dist.requires or []:
            requirement = Requirement(raw)
            wanted = {"": True, **{e: True for e in extras}}
            if requirement.marker is not None and not any(
                requirement.marker.evaluate({"extra": extra}) for extra in wanted
            ):
                continue
            todo.append((requirement.name, tuple(requirement.extras)))
    return dict(sorted(found.items(), key=lambda item: item[0].lower()))


def npm_runtime(lock: Mapping[str, object]) -> dict[str, tuple[str, str]]:
    """{nom: (version, licence)} des paquets npm de production d'un `package-lock.json` v2/v3."""
    packages = lock.get("packages", {})
    out: dict[str, tuple[str, str]] = {}
    if not isinstance(packages, dict):
        return out
    for path, info in packages.items():
        if not path or not isinstance(info, dict) or info.get("dev") or info.get("devOptional"):
            continue
        name = path.split("node_modules/")[-1]
        out[name] = (str(info.get("version", "?")), str(info.get("license") or UNKNOWN))
    return dict(sorted(out.items()))


def table(title: str, rows: dict[str, tuple[str, str]]) -> list[str]:
    lines = [
        f"## {title} ({len(rows)})",
        "",
        "| Paquet | Version | Licence | Alerte |",
        "|---|---|---|---|",
    ]
    for name, (version, licence) in rows.items():
        alert = "copyleft" if is_copyleft(licence) else ("à vérifier" if licence == UNKNOWN else "")
        lines.append(f"| {name} | {version} | {licence} | {alert} |")
    return [*lines, ""]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inventaire des licences (étape 21a).")
    parser.add_argument("--lock", type=Path, default=Path("web/package-lock.json"))
    parser.add_argument("--out", type=Path, default=Path("docs/LICENCES-AUTO.md"))
    args = parser.parse_args(argv)
    python = python_runtime()
    if not python:
        print("Aucune dépendance Python lue : lance `uv sync --extra transcription` d'abord.")
        return 1
    npm = (
        npm_runtime(json.loads(args.lock.read_text(encoding="utf-8")))
        if args.lock.is_file()
        else {}
    )
    body = [
        "# Licences des dépendances (généré par `uv run task licences`)",
        "",
        "> Fichier **généré** : ne pas le modifier. Les décisions sont dans `LICENCES.md`.",
        "",
        *table("Python (exécution)", python),
        *table("npm (production)", npm),
    ]
    args.out.write_text("\n".join(body), encoding="utf-8")
    flagged = {**python, **npm}
    alerts = [(n, lic) for n, (_, lic) in flagged.items() if is_copyleft(lic) or lic == UNKNOWN]
    print(f"{len(python)} paquets Python, {len(npm)} paquets npm -> {args.out}")
    for name, licence in alerts:
        print(f"  A TRANCHER : {name} : {licence}")
    if not alerts:
        print("  Aucune licence à copyleft ni inconnue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
