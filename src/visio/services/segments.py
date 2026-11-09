"""Enregistrement en segments, puis fusion en un seul MP4 (étape 14d).

Pourquoi : un seul fichier de plusieurs heures est perdu en entier si Egress tombe avant la fin.
En segments (HLS, `.ts` de quelques minutes), chaque segment est déjà sur le S3 dès qu'il est fini :
un crash ne coûte que le segment en cours. À la fin de la réunion, les segments sont recollés
**sans réencodage** (`ffmpeg -c copy`, rapide) dans le MP4 unique promis à Eduvia, puis supprimés.

Chemins : l'artefact garde sa clé `meetings/<id>/recordings/<uuid>.mp4` ; les segments sont dans
`meetings/<id>/recordings/<uuid>/seg_00000.ts`, etc. (`segment_prefix`).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from visio.services.storage import Storage

logger = logging.getLogger("visio.segments")

FFMPEG_TIMEOUT_SECONDS = 3600


class MergeError(RuntimeError):
    """La fusion a échoué (ffmpeg absent, segments illisibles...) : la tâche sera relancée."""


def segment_prefix(key: str) -> str:
    """Dossier des segments d'un enregistrement : la clé du MP4 sans `.mp4`, plus `/`."""
    return key.removesuffix(".mp4") + "/"


def segment_keys(storage: Storage, key: str) -> list[str]:
    """Clés des segments `.ts` de l'enregistrement, dans l'ordre de lecture."""
    found = storage.list_objects(segment_prefix(key))
    return sorted(name for name, _size in found if name.endswith(".ts"))


def has_segments(storage: Storage, key: str) -> bool:
    return bool(segment_keys(storage, key))


def merge(storage: Storage, key: str, *, ffmpeg: str | None = None) -> int:
    """Recolle les segments dans `key` (MP4) et supprime les segments. Renvoie la taille du MP4.

    Idempotent : si le MP4 existe déjà (reprise après un arrêt en cours de nettoyage), on ne
    refait que le nettoyage.
    """
    prefix = segment_prefix(key)
    existing = storage.size(key)
    if existing:
        _cleanup(storage, prefix)
        return existing
    binary = ffmpeg or shutil.which("ffmpeg")
    if binary is None:
        msg = "ffmpeg est introuvable : installe-le (winget install Gyan.FFmpeg) pour fusionner"
        raise MergeError(msg)
    keys = segment_keys(storage, key)
    if not keys:
        msg = "aucun segment à fusionner"
        raise MergeError(msg)
    with tempfile.TemporaryDirectory(prefix="visio-merge-") as tmp:
        folder = Path(tmp)
        lines = []
        for index, name in enumerate(keys):
            local = folder / f"{index:05d}.ts"
            storage.download_to(name, local)
            lines.append(f"file '{local.as_posix()}'")
        playlist = folder / "list.txt"
        playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")
        output = folder / "out.mp4"
        command = [
            binary, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(playlist),
            "-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", str(output),
        ]  # fmt: skip
        try:
            done = subprocess.run(  # noqa: S603 - commande construite ici, sans saisie externe
                command, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SECONDS, check=False
            )
        except subprocess.TimeoutExpired as error:
            msg = "ffmpeg a dépassé le délai de fusion"
            raise MergeError(msg) from error
        if done.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
            msg = f"ffmpeg a échoué : {done.stderr.strip()[-300:]}"
            raise MergeError(msg)
        storage.upload_from(key, output, content_type="video/mp4")
    size = storage.size(key)
    if not size:
        msg = "le MP4 fusionné est absent du stockage après envoi"
        raise MergeError(msg)
    _cleanup(storage, prefix)
    logger.info("segments fusionnés", extra={"key": key, "segments": len(keys), "size": size})
    return size


def _cleanup(storage: Storage, prefix: str) -> None:
    for name, _size in storage.list_objects(prefix):
        storage.delete(name)
