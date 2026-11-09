"""Simule une longue réunion en quelques minutes (étape 14d) : segments + fusion à grande échelle.

    uv run task sim-long -- --hours 1            # essai : ~1 h d'enregistrement
    uv run task sim-long -- --hours 8            # la vraie cible (gros : voir « disque » ci-dessous)
    uv run task sim-long -- --hours 8 --drop-last 2   # Egress « tombé » : les 2 derniers segments manquent

Ce que ça prouve : le S3, la fusion `ffmpeg -c copy`, la durée du MP4 obtenu, le nettoyage des
segments, le temps et le disque nécessaires. Ce que ça ne prouve PAS : la charge d'Egress en direct
(CPU/RAM), qui ne se simule pas : elle se mesure avec `test-long.ps1`.

Principe : un segment réaliste (720p, débit constant `--bitrate-kbps`) est encodé une fois, copié
côté S3 autant de fois que nécessaire, puis `segments.merge` fait exactement le travail de la
production. Disque : environ 2 x la taille totale en temporaire (segments + MP4) sur ce poste, et
autant sur le S3 de dev. Le script refuse de démarrer s'il n'y a pas la place.
"""

# ruff: noqa: E501
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from visio.config import ConfigurationError, get_settings
from visio.services import segments
from visio.services.storage import Storage, StorageError


def _run(command: list[str]) -> str:
    done = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    if done.returncode != 0:
        msg = f"{command[0]} a échoué : {done.stderr.strip()[-300:]}"
        raise RuntimeError(msg)
    return done.stdout


def make_sample(path: Path, seconds: int, bitrate_kbps: int) -> None:
    """Un segment `.ts` de `seconds` s, vidéo 720p30 à débit constant (bruit : ne se compresse pas)
    et audio AAC, comme la sortie d'Egress."""
    rate = f"{bitrate_kbps}k"
    _run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=s=1280x720:r=30:d={seconds}",
            "-f", "lavfi", "-i", f"sine=d={seconds}",
            "-vf", "noise=alls=25:allf=t",
            "-c:v", "libx264", "-preset", "ultrafast", "-b:v", rate, "-minrate", rate,
            "-maxrate", rate, "-bufsize", f"{bitrate_kbps * 2}k",
            "-x264-params", "nal-hrd=cbr:force-cfr=1", "-g", "60",
            "-c:a", "aac", "-b:a", "128k", "-shortest", "-f", "mpegts", str(path),
        ]
    )  # fmt: skip


def duration_of(path: Path) -> float:
    out = _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    )
    return float(out.strip())


def simulate(
    storage: Any,
    *,
    hours: float,
    segment_seconds: int,
    bitrate_kbps: int,
    drop_last: int = 0,
    keep: bool = False,
    workdir: Path | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, float]:
    emit = log
    count = max(1, round(hours * 3600 / segment_seconds))
    key = f"meetings/sim_{uuid.uuid4().hex[:12]}/recordings/{uuid.uuid4()}.mp4"
    prefix = segments.segment_prefix(key)
    estimate = count * segment_seconds * bitrate_kbps / 8 / 1024  # Mo
    base = workdir or Path(tempfile.gettempdir())
    free = shutil.disk_usage(base).free / 1024 / 1024
    emit(
        f"Simulation : {count} segments de {segment_seconds} s = {count * segment_seconds / 3600:.2f} h"
    )
    emit(f"Taille estimée : {estimate:,.0f} Mo ; disque libre ici : {free:,.0f} Mo")
    if free < estimate * 2.5:
        msg = "pas assez de place disque pour la simulation (il faut ~2,5 x la taille estimée)"
        raise RuntimeError(msg)
    with tempfile.TemporaryDirectory(prefix="visio-sim-", dir=workdir) as tmp:
        sample = Path(tmp) / "sample.ts"
        t0 = time.monotonic()
        make_sample(sample, segment_seconds, bitrate_kbps)
        sample_mb = sample.stat().st_size / 1024 / 1024
        emit(f"Segment type encodé en {time.monotonic() - t0:.0f} s : {sample_mb:.1f} Mo")
        first = f"{prefix}seg_00000.ts"
        storage.upload_from(first, sample, content_type="video/mp2t")
    for i in range(1, count):
        storage.copy(first, f"{prefix}seg_{i:05d}.ts")
    for i in range(count - drop_last, count):
        if i > 0:
            storage.delete(f"{prefix}seg_{i:05d}.ts")
    kept = count - (min(drop_last, count - 1))
    emit(f"{kept} segments sur le S3 (déposés en {time.monotonic() - t0:.0f} s). Fusion...")
    t1 = time.monotonic()
    size = segments.merge(storage, key)
    merge_s = time.monotonic() - t1
    size_mb = size / 1024 / 1024
    with tempfile.TemporaryDirectory(prefix="visio-sim-", dir=workdir) as tmp:
        local = Path(tmp) / "out.mp4"
        if size_mb < 2048:  # relecture complète seulement si le fichier est raisonnable
            storage.download_to(key, local)
            real = duration_of(local)
        else:
            real = float("nan")
    expected = kept * segment_seconds
    leftovers = len(storage.list_objects(prefix))
    result = {
        "segments": float(kept),
        "merge_seconds": merge_s,
        "size_mb": size_mb,
        "mb_per_hour": size_mb / (expected / 3600),
        "duration_seconds": real,
        "expected_seconds": float(expected),
        "leftover_objects": float(leftovers),
    }
    emit(
        f"Fusion : {merge_s:.0f} s ({size_mb / max(merge_s, 0.01):.0f} Mo/s) -> MP4 de {size_mb:,.0f} Mo"
    )
    emit(
        f"Taille par heure : {result['mb_per_hour']:,.0f} Mo/h ; pour 8 h : {result['mb_per_hour'] * 8 / 1024:.1f} Go"
    )
    emit(
        f"Durée du MP4 : {real:.0f} s (attendu {expected} s) ; objets restants après nettoyage : {leftovers}"
    )
    if not keep:
        storage.delete(key)
        emit("MP4 de simulation supprimé du S3.")
    else:
        emit(f"MP4 conservé sur le S3 : {key}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simule une longue réunion (segments + fusion)")
    parser.add_argument("--hours", type=float, default=1.0)
    parser.add_argument("--segment-seconds", type=int, default=600)
    parser.add_argument("--bitrate-kbps", type=int, default=3000)
    parser.add_argument(
        "--drop-last", type=int, default=0, help="segments manquants (Egress tombé)"
    )
    parser.add_argument("--keep", action="store_true", help="garde le MP4 fusionné sur le S3")
    args = parser.parse_args(argv)
    try:
        storage = Storage.from_settings(get_settings())
    except ConfigurationError as error:
        print(f"configuration invalide : {error}")
        return 2
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ffmpeg/ffprobe introuvables : winget install Gyan.FFmpeg")
        return 2
    try:
        result = simulate(
            storage, hours=args.hours, segment_seconds=args.segment_seconds,
            bitrate_kbps=args.bitrate_kbps, drop_last=args.drop_last, keep=args.keep,
        )  # fmt: skip
    except (StorageError, segments.MergeError, RuntimeError) as error:
        print(f"ECHEC : {error}")
        return 1
    ok = result["leftover_objects"] == 0 and (
        result["duration_seconds"] != result["duration_seconds"]  # NaN : non relu (gros fichier)
        or abs(result["duration_seconds"] - result["expected_seconds"]) < 5 + result["segments"]
    )
    print("RESULTAT : OK" if ok else "RESULTAT : ECHEC (durée ou nettoyage inattendus)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
