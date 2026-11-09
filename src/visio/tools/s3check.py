"""Vérifie le S3 avec la vraie configuration (étape 13b).

    uv run task s3-init     # développement : crée le bucket s'il manque
    uv run task s3-check    # écriture, lecture, suppression d'un objet, bucket privé

Lancés par `dotenvx run -f .env.development`, donc avec le S3 que l'API utilise.
`init` refuse de tourner en production : le bucket y est fourni par Eduvia.
"""

from __future__ import annotations

import sys
import time
import uuid
from collections.abc import Callable

import httpx

from visio.config import ConfigurationError, get_settings
from visio.services.storage import ObjectNotFoundError, Storage, StorageError

Fetch = Callable[[str], int]


def _anonymous_status(url: str) -> int:
    return httpx.get(url, timeout=10, follow_redirects=False).status_code


def run_check(storage: Storage, *, fetch: Fetch = _anonymous_status) -> list[str]:
    """Retourne la liste des problèmes (vide = tout est bon). N'écrit qu'un objet de test, qu'elle
    supprime ensuite quoi qu'il arrive."""
    problems: list[str] = []
    key = f"healthcheck/{uuid.uuid4().hex}.txt"
    payload = b"eduvia-visio s3 check " + uuid.uuid4().bytes
    try:
        storage.put(key, payload, content_type="text/plain")
        if storage.get(key) != payload:
            problems.append("lecture : le contenu relu est différent du contenu écrit")
        if not storage.exists(key):
            problems.append("l'objet écrit n'est pas visible (exists)")

        # Bucket privé : sans signature, le S3 doit refuser (403), jamais servir l'objet.
        status = fetch(f"{storage.endpoint.rstrip('/')}/{storage.bucket}/{key}")
        if status == 200:
            problems.append("le bucket est PUBLIC : l'objet est lisible sans signature")
        elif status not in (401, 403):
            problems.append(f"accès sans signature : réponse inattendue {status}")
    except (StorageError, httpx.HTTPError) as error:
        problems.append(f"échec : {error}")
    finally:
        try:
            storage.delete(key)
            if storage.exists(key):
                problems.append("suppression : l'objet existe encore")
            try:
                storage.get(key)
                problems.append("suppression : l'objet est encore lisible")
            except ObjectNotFoundError:
                pass
        except StorageError as error:
            problems.append(f"suppression impossible : {error}")
    return problems


def _with_retries[T](action: Callable[[], T], *, attempts: int = 20, pause: float = 1.5) -> T:
    """Un S3 qui vient de démarrer met quelques secondes à répondre : on réessaie d'abord."""
    for attempt in range(attempts):
        try:
            return action()
        except StorageError:
            if attempt == attempts - 1:
                raise
            time.sleep(pause)
    raise AssertionError("inatteignable")  # pragma: no cover


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else "check"
    if command not in ("init", "check"):
        print("usage : s3check [init|check]")
        return 2
    try:
        settings = get_settings()
    except ConfigurationError as error:
        print(f"Configuration invalide : {error}")
        return 1
    storage = Storage.from_settings(settings)
    print(f"S3 : {settings.s3_endpoint}  bucket : {settings.s3_bucket}")

    try:
        if command == "init":
            if settings.app_env == "production":
                print(
                    "Refusé : en production le bucket est fourni par Eduvia, il n'est pas créé ici."
                )
                return 1
            created = _with_retries(storage.ensure_bucket)
            print("Bucket créé." if created else "Bucket déjà présent.")
            return 0

        if not storage.bucket_exists():
            print("Le bucket n'existe pas : lance d'abord  uv run task s3-init")
            return 1
    except StorageError as error:
        print(f"S3 injoignable ou refusé : {error}")
        return 1

    problems = run_check(storage)
    if problems:
        for problem in problems:
            print(f"  ECHEC : {problem}")
        return 1
    print("  OK : écriture, lecture, suppression")
    print("  OK : objet supprimé : introuvable ensuite")
    print("  OK : bucket privé : accès sans signature refusé")
    print("S3 OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
