"""Outils de développement : **répondent 404 hors `APP_ENV=development`**.

`GET /dev/livekit-test` sert une petite page pour essayer `POST /v1/join` à la main : on y colle
un lien, la page l'échange contre un jeton LiveKit et se connecte à la salle (micro et caméra).
Ce n'est pas l'interface de visio (étape 7). Servie par le backend lui-même : pas de CORS.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from visio.api.errors import ApiError
from visio.config import Settings


def require_development(request: Request) -> None:
    settings: Settings = request.app.state.settings
    if settings.app_env != "development":
        raise ApiError(404, "not_found", "ressource introuvable")


router = APIRouter(
    prefix="/dev", include_in_schema=False, dependencies=[Depends(require_development)]
)

# Le client LiveKit vient d'un CDN : acceptable pour une page de test locale uniquement.
_PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>Test LiveKit (développement)</title>
<style>
  body { font: 15px system-ui, sans-serif; margin: 1.5rem; max-width: 60rem; }
  input { width: 100%; padding: .4rem; margin: .2rem 0 .6rem; box-sizing: border-box; }
  button { padding: .4rem .9rem; margin-right: .4rem; }
  pre { background: #f3f3f3; padding: .6rem; white-space: pre-wrap; word-break: break-all; }
  #videos { display: flex; flex-wrap: wrap; gap: .5rem; }
  video { width: 18rem; background: #222; border-radius: 6px; }
</style>
</head>
<body>
<h1>Test LiveKit <small>(développement)</small></h1>
<label>Lien de la réunion (<code>http://localhost:8000/j/...</code>)
  <input id="link" placeholder="http://localhost:8000/j/eyJ..."></label>
<label>Nom (seulement pour un lien générique)
  <input id="name" placeholder="Ex. Lina"></label>
<button id="go">Rejoindre</button><button id="leave" disabled>Quitter</button>
<pre id="log">Colle un lien puis clique sur « Rejoindre ».</pre>
<div id="videos"></div>
<script src="https://cdn.jsdelivr.net/npm/livekit-client@2/dist/livekit-client.umd.min.js"></script>
<script>
const $ = (id) => document.getElementById(id);
let room = null;
const show = (value) => {
  $("log").textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
};
const clientKey = "dev-client-id";

$("go").onclick = async () => {
  const link = $("link").value.trim();
  const token = link.includes("/j/") ? link.split("/j/")[1] : link;
  const body = { token };
  if ($("name").value.trim()) body.display_name = $("name").value.trim();
  const saved = sessionStorage.getItem(clientKey);
  if (saved) body.client_id = saved;
  const response = await fetch("/v1/join", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) { show({ http: response.status, ...data }); return; }
  if (data.client_id) sessionStorage.setItem(clientKey, data.client_id);
  show({ ...data, livekit_token: data.livekit_token.slice(0, 20) + "..." });
  room = new LivekitClient.Room();
  room.on(LivekitClient.RoomEvent.TrackSubscribed, (track) => {
    const element = track.attach();
    if (track.kind === "video") element.style.width = "18rem";
    $("videos").appendChild(element);
  });
  room.on(LivekitClient.RoomEvent.TrackUnsubscribed, (track) => {
    track.detach().forEach((e) => e.remove());
  });
  room.on(LivekitClient.RoomEvent.Disconnected, () => { $("leave").disabled = true; });
  await room.connect(data.livekit_url, data.livekit_token);
  await room.localParticipant.setMicrophoneEnabled(true);
  await room.localParticipant.setCameraEnabled(true);
  const source = LivekitClient.Track.Source.Camera;
  const mine = room.localParticipant.getTrackPublication(source);
  if (mine && mine.track) {
    const e = mine.track.attach();
    e.muted = true;
    $("videos").appendChild(e);
  }
  $("leave").disabled = false;
};
$("leave").onclick = () => { if (room) room.disconnect(); };
</script>
</body>
</html>
"""


@router.get("/livekit-test", response_class=HTMLResponse)
def livekit_test_page() -> str:
    return _PAGE
