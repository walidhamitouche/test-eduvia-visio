"""Interface de visio (étape 7) : le backend sert l'application web construite (`web/dist`).

- `GET /j/<jeton>` : la même page pour **tout** jeton, valide ou non. C'est l'application qui
  interroge ensuite `POST /v1/join/preview` et affiche l'erreur (« lien invalide », jamais une
  salle vide). La page ne dit donc rien sur la validité d'un lien.
- Marque blanche : nom, logo et couleurs viennent de la configuration (`BRAND_*`) et sont
  injectés dans la page à chaque requête : un redémarrage suffit, pas de reconstruction.
- `GET /assets/...` : fichiers construits (noms à empreinte, donc cache longue durée).

Le jeton est dans l'URL : la page n'est jamais mise en cache et n'envoie aucun `Referer`
(le logo peut venir d'un autre domaine).
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse

from visio.api.auth import require_https
from visio.api.errors import ApiError
from visio.config import Settings

PLACEHOLDER = "<!--brand-head-->"

router = APIRouter(include_in_schema=False)

# Types explicites : sous Windows, le registre peut déclarer `.js` en `text/plain`, ce que les
# navigateurs refusent pour un module.
_MEDIA_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".json": "application/json",
    ".map": "application/json",
    ".wasm": "application/wasm",
}


def _luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def readable_on(hex_color: str) -> str:
    """Blanc ou noir, selon le meilleur contraste (WCAG) sur `hex_color`."""
    luminance = _luminance(hex_color)
    contrast_white = 1.05 / (luminance + 0.05)
    contrast_black = (luminance + 0.05) / 0.05
    return "#FFFFFF" if contrast_white >= contrast_black else "#000000"


def _json_for_script(value: object) -> str:
    """JSON sûr dans une balise <script> : `<`, `>` et `&` ne peuvent pas fermer la balise."""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def brand_head(settings: Settings) -> str:
    primary, secondary = settings.brand_primary_color, settings.brand_secondary_color
    config = {"name": settings.brand_name, "logo_url": settings.brand_logo_url}
    return (
        f"<title>{html.escape(settings.brand_name)}</title>\n"
        f"<style>:root{{--brand-primary:{primary};"
        f"--brand-on-primary:{readable_on(primary)};"
        f"--brand-secondary:{secondary};"
        f"--brand-on-secondary:{readable_on(secondary)}}}</style>\n"
        f'<script id="brand-config" type="application/json">{_json_for_script(config)}</script>'
    )


def _dist(request: Request) -> Path:
    settings: Settings = request.app.state.settings
    return Path(settings.web_dist_dir)


@router.get("/j/{token}", response_class=HTMLResponse, dependencies=[Depends(require_https)])
def meeting_page(token: str, request: Request) -> HTMLResponse:
    index = _dist(request) / "index.html"
    if not index.is_file():
        raise ApiError(
            503,
            "web_not_built",
            "interface non construite : lance « npm --prefix web run build »",
        )
    page = index.read_text(encoding="utf-8").replace(
        PLACEHOLDER, brand_head(request.app.state.settings)
    )
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/recording-template", response_class=HTMLResponse)
def recording_template(request: Request) -> HTMLResponse:
    """Page filmée par Egress (mise en page de l'enregistrement). Aucun jeton dans l'URL du
    serveur : Egress ajoute lui-même `?url=...&token=...` (jeton court, propre à son robot)."""
    page_file = _dist(request) / "recording.html"
    if not page_file.is_file():
        raise ApiError(
            503, "web_not_built", "interface non construite : « npm --prefix web run build »"
        )
    page = page_file.read_text(encoding="utf-8").replace(
        PLACEHOLDER, brand_head(request.app.state.settings)
    )
    return HTMLResponse(
        page, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    )


@router.get("/assets/{filepath:path}")
def asset(filepath: str, request: Request) -> FileResponse:
    base = (_dist(request) / "assets").resolve()
    candidate = (base / filepath).resolve()
    if not candidate.is_file() or not candidate.is_relative_to(base):
        raise ApiError(404, "not_found", "ressource introuvable")
    return FileResponse(
        candidate,
        media_type=_MEDIA_TYPES.get(candidate.suffix.lower()),
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
