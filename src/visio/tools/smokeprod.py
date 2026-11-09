"""Smoke test de production (étape 19) : `uv run task smoke-prod`.

Joue contre le serveur RÉEL (PUBLIC_BASE_URL, API_KEY lus dans .env.production) le parcours
automatisable des règles de réunion : santé, authentification, création, aperçu, lien inventé,
lien nominatif, jeton LiveKit, annulation. Ne crée qu'une réunion de test, annulée à la fin.

Ce que ce test ne remplace pas (à faire à la main, voir docs/INSTALL.md §9) : une vraie
visio à deux navigateurs, l'enregistrement MP4 sur le S3, la transcription, le réseau 443 seul.

    uv run task smoke-prod   (DOTENV_PRIVATE_KEY_PRODUCTION dans l'environnement)
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx

_results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    _results.append((ok, label))
    print(("  OK   " if ok else "  ECHEC"), label, f"({detail})" if detail and not ok else "")


def main() -> int:
    base = os.environ.get("SMOKE_BASE_URL") or os.environ.get("PUBLIC_BASE_URL", "")
    key = os.environ.get("API_KEY", "")
    if not base.startswith("https://") or not key:
        print(
            "PUBLIC_BASE_URL (https) et API_KEY requis : lance via dotenvx run -f .env.production"
        )
        return 2
    base = base.rstrip("/")
    auth = {"Authorization": f"Bearer {key}"}
    print(f"Smoke test contre {base}")
    with httpx.Client(base_url=base, timeout=15) as c:
        r = c.get("/health")
        check(r.status_code == 200, "GET /health public", str(r.status_code))
        r = c.get("/v1/health", headers=auth)
        deps = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        check(
            r.status_code == 200,
            "GET /v1/health : base, LiveKit, S3, file de tâches",
            str(deps)[:300],
        )
        check(c.get("/v1/health").status_code in (401, 403), "santé détaillée refusée sans clé")
        check(
            c.post("/v1/meetings", json={}).status_code in (401, 403), "création refusée sans clé"
        )
        check(c.get("/j/lien-invente").status_code in (200, 404), "page /j/<jeton> servie")
        check(
            c.post("/v1/join/preview", json={"token": "x.y.z"}).status_code == 404,
            "lien inventé : invalid_link (404)",
        )

        now = datetime.now(UTC)
        meeting = {
            "title": "smoke-prod (à supprimer)",
            "starts_at": (now + timedelta(minutes=3)).isoformat(),
            "ends_at": (now + timedelta(minutes=20)).isoformat(),
            "early_join_minutes": 10,
            "waiting_room": False,
            "recording": False,
            "transcription": False,
            "organizers": [{"display_name": "Smoke Test", "external_id": "smoke_org"}],
            "metadata": {"smoke": True},
        }
        r = c.post("/v1/meetings", json=meeting, headers=auth)
        check(r.status_code == 201, "POST /v1/meetings", r.text[:200])
        if r.status_code != 201:
            return 1
        created = r.json()
        mid = created["id"]
        try:
            ptoken = urlparse(created["participant_url"]).path.rsplit("/", 1)[-1]
            otoken = urlparse(created["organizer_urls"][0]).path.rsplit("/", 1)[-1]
            check(
                created["participant_url"].startswith(base), "lien aux couleurs du domaine public"
            )
            check(
                ptoken != otoken and ptoken not in otoken,
                "lien organisateur distinct du lien participant",
            )
            r = c.post("/v1/join/preview", json={"token": ptoken})
            check(r.status_code == 200, "aperçu du lien participant", r.text[:200])
            r = c.post("/v1/join/preview", json={"token": ptoken[:-2] + "xx"})
            check(r.status_code == 404, "lien participant modifié : 404", str(r.status_code))
            r = c.post("/v1/join", json={"token": ptoken, "display_name": "Visiteur Smoke"})
            body = r.json() if r.status_code == 200 else {}
            check(
                r.status_code == 200 and bool(body.get("livekit_token")),
                "rejoindre : jeton LiveKit émis",
                r.text[:200],
            )
            check(
                str(body.get("livekit_url", "")).startswith("wss://"),
                "URL LiveKit publique en wss://",
                str(body.get("livekit_url")),
            )
            r = c.post("/v1/join", json={"token": otoken})
            check(r.status_code == 200, "rejoindre en organisateur", r.text[:200])
        finally:
            r = c.delete(f"/v1/meetings/{mid}", headers=auth)
            check(r.status_code == 200, "DELETE /v1/meetings/:id (nettoyage)", r.text[:200])
    failed = [label for ok, label in _results if not ok]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} vérifications réussies")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
