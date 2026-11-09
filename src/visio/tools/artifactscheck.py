"""Vérifie de bout en bout l'API des artefacts (étape 15, scénario 8), contre l'API qui tourne.

    uv run task artifacts-check                   # dernière réunion avec un enregistrement prêt
    uv run task artifacts-check mtg_777f5e44...   # une réunion précise
    uv run task artifacts-check --delete          # + teste la suppression (DÉTRUIT le fichier)

Sans `--delete`, lecture seule. Avec `--delete`, l'enregistrement est supprimé pour de bon : le
test se fait sur un enregistrement de développement dont on n'a plus besoin.

Contrôles : liste avec URL de 15 min sur le domaine de l'API (jamais sur le S3) ; clé d'API
exigée ; téléchargement complet dont la taille égale celle du S3 et qui commence comme un MP4 ;
reprise par `Range` ; URL modifiée refusée ; objet inaccessible directement sur le S3 sans
signature ; puis, avec `--delete` : `204`, objet absent du S3, URL déjà émise en `410`, second
`DELETE` en `404`, liste vidée.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from visio.config import ConfigurationError, Settings, get_settings
from visio.db import create_db_engine, create_session_factory
from visio.db.models import ArtifactKind, ArtifactRow, ArtifactStatus
from visio.services.storage import ObjectStore, Storage, StorageError

OK, FAIL, INFO = "OK", "ECHEC", "INFO"
Line = tuple[str, str]
MAX_LIFETIME_SECONDS = 15 * 60


def latest_meeting_with_recording(db_factory: Callable[[], object]) -> str | None:
    with db_factory() as db:  # type: ignore[attr-defined]
        found: str | None = db.scalar(
            select(ArtifactRow.meeting_id)
            .where(
                ArtifactRow.kind == ArtifactKind.RECORDING,
                ArtifactRow.status == ArtifactStatus.READY,
            )
            .order_by(ArtifactRow.created_at.desc())
            .limit(1)
        )
    return found


def _error_code(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["code"])
    except (ValueError, KeyError, TypeError):
        return f"(corps non JSON, statut {response.status_code})"


def _expect(lines: list[Line], condition: bool, ok: str, fail: str) -> bool:
    lines.append((OK, ok) if condition else (FAIL, fail))
    return condition


def run_check(
    http: httpx.Client,
    storage: ObjectStore,
    settings: Settings,
    meeting_id: str,
    *,
    delete: bool,
    raw_status: Callable[[str], int] | None = None,
) -> list[Line]:
    """Les contrôles. `http` parle à l'API (`base_url` = son adresse) ; `raw_status(url)` donne le
    statut d'un accès anonyme direct au S3 (par défaut : une vraie requête)."""
    lines: list[Line] = []
    auth = {"Authorization": f"Bearer {settings.api_key.get_secret_value()}"}

    listed = http.get(f"/v1/meetings/{meeting_id}/recordings", headers=auth)
    if not _expect(
        lines,
        listed.status_code == 200,
        "liste des enregistrements : 200",
        f"liste : {listed.status_code}",
    ):
        return lines
    ready = [r for r in listed.json()["recordings"] if r["status"] == "ready"]
    if not ready:
        lines.append((FAIL, "aucun enregistrement prêt pour cette réunion"))
        return lines
    item = ready[0]
    lines.append((INFO, f"enregistrement {item['id']} · {item['size_bytes']} o"))

    url = str(item["download_url"])
    path = urlsplit(url).path
    _expect(
        lines,
        url.startswith(settings.public_base_url.rstrip("/") + "/v1/downloads/")
        and settings.s3_endpoint not in url
        and settings.s3_bucket not in url,
        "l'URL est sur le domaine de l'API, jamais sur le S3",
        f"URL suspecte : {url[:80]}…",
    )
    _expect(
        lines,
        http.get(f"/v1/meetings/{meeting_id}/recordings").status_code == 401,
        "sans clé d'API : 401",
        "la liste est accessible sans clé d'API !",
    )

    recording = http.get(f"/v1/recordings/{item['id']}", headers=auth).json()
    key = _key_of(item["id"], settings)
    on_s3 = storage.size(key) if key else None

    # Téléchargement complet, compté au fil de l'eau (jamais gardé en mémoire).
    received, head = 0, b""
    with http.stream("GET", path) as response:
        status = response.status_code
        for chunk in response.iter_bytes():
            if len(head) < 12:
                head = (head + chunk)[:12]
            received += len(chunk)
    _expect(lines, status == 200, "téléchargement : 200", f"téléchargement : {status}")
    _expect(
        lines,
        on_s3 is not None and received == on_s3,
        f"taille reçue = taille sur le S3 ({received} o)",
        f"taille reçue {received} o, S3 : {on_s3}",
    )
    _expect(
        lines, head[4:8] == b"ftyp", "le fichier commence comme un MP4 (ftyp)", f"début : {head!r}"
    )

    first = http.get(path, headers={"Range": "bytes=0-99"})
    _expect(
        lines,
        first.status_code == 206 and len(first.content) == 100,
        "Range bytes=0-99 : 206, 100 octets",
        f"Range : {first.status_code}, {len(first.content)} o",
    )
    beyond = http.get(path, headers={"Range": f"bytes={(on_s3 or 0) + 10}-"})
    _expect(
        lines, beyond.status_code == 416, "Range hors du fichier : 416", f"{beyond.status_code}"
    )

    token = path.rsplit("/", 1)[1]
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    bad = http.get(f"/v1/downloads/{tampered}")
    _expect(
        lines,
        bad.status_code == 404 and _error_code(bad) == "invalid_download_link",
        "URL modifiée : 404 invalid_download_link",
        f"URL modifiée : {bad.status_code}",
    )

    if key:
        probe = raw_status or _anonymous_status
        raw_url = f"{settings.s3_endpoint.rstrip('/')}/{settings.s3_bucket}/{key}"
        code = probe(raw_url)
        _expect(
            lines,
            code in (401, 403),
            f"accès direct au S3 sans signature : {code} (bucket privé)",
            f"accès direct au S3 sans signature : {code} — le bucket est-il public ?",
        )
    expires = recording.get("download_url_expires_at")
    _expect(lines, bool(expires), f"URL valable jusqu'à {expires}", "pas de date d'expiration")

    if not delete:
        lines.append((INFO, "suppression non testée (ajoute --delete : détruit l'enregistrement)"))
        return lines

    deleted = http.delete(f"/v1/recordings/{item['id']}", headers=auth)
    _expect(lines, deleted.status_code == 204, "DELETE : 204", f"DELETE : {deleted.status_code}")
    try:
        gone = key is not None and storage.size(key) is None
    except StorageError as error:
        gone = False
        lines.append((FAIL, f"S3 illisible : {error}"))
    _expect(lines, gone, "l'objet n'existe plus sur le S3", "l'objet est ENCORE sur le S3")
    reused = http.get(path)
    _expect(
        lines,
        reused.status_code == 410 and _error_code(reused) == "artifact_deleted",
        "l'URL déjà émise (encore dans ses 15 min) : 410 artifact_deleted",
        f"l'URL déjà émise répond {reused.status_code} {_error_code(reused)}",
    )
    again = http.delete(f"/v1/recordings/{item['id']}", headers=auth)
    _expect(
        lines,
        again.status_code == 404,
        "second DELETE : 404",
        f"second DELETE : {again.status_code}",
    )
    after = http.get(f"/v1/meetings/{meeting_id}/recordings", headers=auth).json()["recordings"]
    _expect(
        lines,
        item["id"] not in [r["id"] for r in after],
        "l'enregistrement a disparu de la liste",
        "l'enregistrement est encore dans la liste",
    )
    return lines


def _key_of(artifact_id: str, settings: Settings) -> str | None:
    engine = create_db_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as db:
            row = db.get(ArtifactRow, artifact_id)
            return None if row is None else row.storage_key
    finally:
        engine.dispose()


def _anonymous_status(url: str) -> int:
    try:
        return httpx.get(url, timeout=10).status_code
    except httpx.HTTPError:
        return 0


def main(argv: list[str] | None = None, *, out: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(description="Vérifie l'API des artefacts (étape 15).")
    parser.add_argument("meeting_id", nargs="?")
    parser.add_argument("--delete", action="store_true", help="teste aussi la suppression")
    parser.add_argument("--base-url", help="adresse de l'API (défaut : PUBLIC_BASE_URL)")
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
    except ConfigurationError as error:
        out(str(error))
        return 1
    engine = create_db_engine(settings.database_url)
    try:
        meeting_id = args.meeting_id or latest_meeting_with_recording(
            create_session_factory(engine)
        )
    finally:
        engine.dispose()
    if not meeting_id:
        out("Aucune réunion avec un enregistrement prêt : lance d'abord un enregistrement (14).")
        return 1
    base = (args.base_url or settings.public_base_url).rstrip("/")
    out(f"API : {base}   réunion : {meeting_id}   suppression : {'oui' if args.delete else 'non'}")
    with httpx.Client(base_url=base, timeout=60) as http:
        lines = run_check(
            http, Storage.from_settings(settings), settings, meeting_id, delete=args.delete
        )
    for level, text in lines:
        out(f"  {level:<5} {text}")
    failed = any(level == FAIL for level, _ in lines)
    out("RÉSULTAT : ECHEC" if failed else "RÉSULTAT : OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
