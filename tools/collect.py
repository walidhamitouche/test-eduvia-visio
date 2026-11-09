"""Génère THIRD-PARTY-LICENSES.txt : une entrée par dépendance de production (Python et npm),
avec nom, version, licence déclarée et texte intégral du fichier de licence du paquet.

Usage : python -I collect.py <site-packages> <node_modules> <sortie.txt>
"""

from __future__ import annotations

import json
import re
import sys
from email.parser import Parser
from pathlib import Path

LICENSE_NAMES = re.compile(r"^(licen[cs]e|copying|notice|copyright|authors)", re.IGNORECASE)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip()


def python_entries(site: Path) -> list[dict]:
    out = []
    for dist in sorted(site.glob("*.dist-info")):
        meta_file = dist / "METADATA"
        if not meta_file.exists():
            continue
        meta = Parser().parsestr(meta_file.read_text(encoding="utf-8", errors="replace"))
        name, version = meta.get("Name", dist.name), meta.get("Version", "?")
        lic = meta.get("License-Expression") or meta.get("License") or ""
        if not lic or len(lic) > 80:
            lic = "; ".join(
                c.split("::")[-1].strip()
                for c in meta.get_all("Classifier", [])
                if c.startswith("License ::")
            ) or (lic[:80] if lic else "non déclarée")
        files = []
        for base in (dist, dist / "licenses", dist / "licences"):
            if base.is_dir():
                files += [
                    p
                    for p in sorted(base.rglob("*"))
                    if p.is_file() and LICENSE_NAMES.match(p.name)
                ]
        out.append(
            {"eco": "python", "name": name, "version": version, "license": lic, "files": files}
        )
    return out


def npm_entries(nm: Path) -> list[dict]:
    out = []
    pkgs = []
    for child in sorted(nm.iterdir()):
        if child.name.startswith("."):
            continue
        if child.name.startswith("@"):
            pkgs += sorted(p for p in child.iterdir() if p.is_dir())
        elif child.is_dir():
            pkgs.append(child)
    for pkg in pkgs:
        pj = pkg / "package.json"
        if not pj.exists():
            continue
        data = json.loads(pj.read_text(encoding="utf-8"))
        lic = data.get("license") or data.get("licenses") or "non déclarée"
        if not isinstance(lic, str):
            lic = json.dumps(lic)
        files = [p for p in sorted(pkg.iterdir()) if p.is_file() and LICENSE_NAMES.match(p.name)]
        out.append(
            {
                "eco": "npm",
                "name": data["name"],
                "version": data.get("version", "?"),
                "license": lic,
                "files": files,
            }
        )
    return out


def main() -> int:
    site, nm, dest = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    entries = python_entries(site) + npm_entries(nm)
    lines = [
        "THIRD-PARTY-LICENSES",
        "=" * 20,
        "",
        "Ce fichier reproduit les licences des bibliothèques tierces incluses dans l'application",
        "(dépendances de production Python et npm). Généré par un script à partir des verrous",
        "uv.lock et package-lock.json ; à régénérer à chaque changement de dépendance.",
        "",
        f"Nombre de paquets : {len(entries)}",
        "",
        "SOMMAIRE",
        "--------",
    ]
    for e in entries:
        lines.append(f"- [{e['eco']}] {e['name']} {e['version']} : {e['license']}")
    missing = []
    for e in entries:
        lines += [
            "",
            "=" * 78,
            f"[{e['eco']}] {e['name']} {e['version']}  -  licence : {e['license']}",
            "=" * 78,
            "",
        ]
        if not e["files"]:
            missing.append(e["name"])
            lines.append(
                "(aucun fichier de licence distribué avec ce paquet ; licence déclarée ci-dessus)"
            )
        for f in e["files"]:
            lines += [f"--- {f.name} ---", read(f), ""]
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(entries)} paquets ; sans fichier de licence : {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
