"""Rapport d'un test de charge (étape 20a) : lit le CSV de `deploy/dev/test-charge.ps1`.

    uv run task load-report -- data/charge/20261107-1200/stats.csv --participants 300

Le CSV a une ligne par conteneur et par relevé : `epoch;nom;cpu%;ram;net;disque`, valeurs telles que
les écrit `docker stats --format` (ex. `153.2%`, `812MiB / 7.6GiB`, `1.2GB / 3.4GB`, `0B / 0B`).
Le rapport donne, par conteneur : CPU moyen / max, RAM max, dérive de la RAM (fuite possible), débit
réseau moyen. C'est la matière de la recommandation de dimensionnement (DECISIONS, étape 20d).
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

_UNITS = {
    "b": 1,
    "kb": 10**3,
    "mb": 10**6,
    "gb": 10**9,
    "kib": 2**10,
    "mib": 2**20,
    "gib": 2**30,
}
_SIZE = re.compile(r"^\s*([\d.]+)\s*([a-zA-Z]+)\s*$")


def parse_size(text: str) -> float:
    """`812MiB` → octets. Unité inconnue → ValueError."""
    found = _SIZE.match(text)
    if found is None or found.group(2).lower() not in _UNITS:
        msg = f"taille illisible : {text!r}"
        raise ValueError(msg)
    return float(found.group(1)) * _UNITS[found.group(2).lower()]


def parse_pair(text: str) -> tuple[float, float]:
    """`1.2GB / 3.4GB` → (1.2e9, 3.4e9)."""
    left, _, right = text.partition("/")
    return parse_size(left), parse_size(right)


class GapError(ValueError):
    """Relevé sans valeurs (`--`) : `docker stats` n'a pas répondu pour ce conteneur (machine
    saturée, conteneur à l'arrêt). Compté dans le rapport, jamais ignoré en silence."""


@dataclass(frozen=True)
class Sample:
    at: float
    cpu: float  # % d'un cœur (peut dépasser 100)
    ram: float  # octets
    net_rx: float  # octets cumulés
    net_tx: float
    disk_read: float
    disk_write: float


def parse_line(line: str) -> tuple[str, Sample] | None:
    """Une ligne du CSV ; `None` si elle est vide ou en-tête. Ligne mal formée → ValueError."""
    parts = [p.strip() for p in line.split(";")]
    if not line.strip() or parts[0].lstrip("\ufeff") == "epoch":
        return None
    if len(parts) != 6:
        msg = f"ligne attendue à 6 champs : {line!r}"
        raise ValueError(msg)
    if any(part.startswith("--") for part in parts[2:]):
        raise GapError(parts[1])
    rx, tx = parse_pair(parts[4])
    dr, dw = parse_pair(parts[5])
    sample = Sample(
        at=float(parts[0]),
        cpu=float(parts[2].rstrip("%")),
        ram=parse_pair(parts[3])[0],
        net_rx=rx,
        net_tx=tx,
        disk_read=dr,
        disk_write=dw,
    )
    return parts[1], sample


def load_with_gaps(path: Path) -> tuple[dict[str, list[Sample]], dict[str, int]]:
    """Relevés par conteneur et nombre de relevés manquants par conteneur."""
    out: dict[str, list[Sample]] = defaultdict(list)
    gaps: dict[str, int] = defaultdict(int)
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        try:
            parsed = parse_line(raw)
        except GapError as gap:
            gaps[str(gap)] += 1
            continue
        if parsed is not None:
            out[parsed[0]].append(parsed[1])
    for samples in out.values():
        samples.sort(key=lambda s: s.at)
    return dict(out), dict(gaps)


def load(path: Path) -> dict[str, list[Sample]]:
    return load_with_gaps(path)[0]


def ram_drift_percent(samples: list[Sample]) -> float:
    """Écart entre la RAM moyenne du dernier quart et celle du deuxième quart, en %.

    Le premier quart est ignoré (montée en charge). Une dérive > 10 % sur une longue durée est un
    indice de fuite mémoire (à confirmer avec le test de 8 h).
    """
    n = len(samples)
    if n < 8:
        return 0.0
    q = n // 4
    early = statistics.fmean(s.ram for s in samples[q : 2 * q])
    late = statistics.fmean(s.ram for s in samples[-q:])
    return 0.0 if early == 0 else (late - early) / early * 100


LEAK_WATCHED = ("livekit", "redis", "api")  # egress et S3 varient normalement (fichiers, caches)


def leak_suspects(
    data: dict[str, list[Sample]], *, min_minutes: float = 60.0, threshold: float = 10.0
) -> list[str]:
    """Conteneurs surveillés dont la RAM dérive de plus de `threshold` % sur un test d'au moins
    `min_minutes` (8 h : test de fuite de l'étape 20c). Un test court ne conclut rien."""
    suspects: list[str] = []
    for name, samples in sorted(data.items()):
        if not any(word in name for word in LEAK_WATCHED):
            continue
        minutes = (samples[-1].at - samples[0].at) / 60
        if minutes >= min_minutes and ram_drift_percent(samples) > threshold:
            suspects.append(name)
    return suspects


def summarize(name: str, samples: list[Sample]) -> str:
    seconds = max(samples[-1].at - samples[0].at, 1.0)
    cpu = [s.cpu for s in samples]
    rx = (samples[-1].net_rx - samples[0].net_rx) * 8 / seconds / 1e6
    tx = (samples[-1].net_tx - samples[0].net_tx) * 8 / seconds / 1e6
    disk = (samples[-1].disk_write - samples[0].disk_write) / 1e6
    return (
        f"{name:<28} CPU moy {statistics.fmean(cpu):7.1f} % · max {max(cpu):7.1f} %"
        f" · RAM max {max(s.ram for s in samples) / 2**20:8.0f} Mio"
        f" · dérive RAM {ram_drift_percent(samples):+5.1f} %"
        f" · réseau ↓ {rx:6.1f} / ↑ {tx:6.1f} Mbit/s · disque écrit {disk:8.0f} Mo"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rapport d'un test de charge (étape 20).")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--participants", type=int, default=0, help="connexions simultanées")
    args = parser.parse_args(argv)
    data, gaps = load_with_gaps(args.csv)
    if not data:
        print("CSV vide : aucun relevé.", file=sys.stderr)
        return 1
    first = min(s[0].at for s in data.values())
    last = max(s[-1].at for s in data.values())
    print(f"Durée des relevés : {(last - first) / 60:.1f} min · {len(data)} conteneur(s)")
    for name in sorted(data):
        print(summarize(name, data[name]))
    for name in leak_suspects(data):
        print(f"FUITE POSSIBLE : la RAM de {name} dérive de plus de 10 % sur plus d'1 h.")
    for name in sorted(gaps):
        print(
            f"ATTENTION {name} : {gaps[name]} relevé(s) sans réponse de docker stats"
            " (machine saturée ou conteneur arrêté) : ce test ne tient pas la charge."
        )
    lk = next((n for n in sorted(data) if "livekit" in n), None)
    if args.participants > 0 and lk is not None:
        mean = statistics.fmean(s.cpu for s in data[lk])
        print(
            f"\nCoût moyen de {lk} : {mean / args.participants:.2f} % de cœur par connexion"
            f" ({args.participants} connexions) ; à multiplier par 1,5 pour la marge."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
