// Page filmée par LiveKit Egress (RoomComposite, `custom_base_url`). Egress l'ouvre avec
// `?url=<serveur>&token=<jeton>&layout=<nom>`, puis attend le message console START_RECORDING
// pour commencer à filmer, et END_RECORDING pour terminer. Aucune interaction : rendu seul.
import {
  Participant,
  RemoteParticipant,
  Room,
  RoomEvent,
  Track,
  type TrackPublication,
} from "livekit-client";

import "./recording.css";

const MIC_OFF =
  '<svg viewBox="0 0 24 24"><path d="M19 11h-1.7c0 .74-.16 1.43-.43 2.05l1.23 1.23c.56-.98.9-2.09.9-3.28zm-4.02.17c0-.06.02-.11.02-.17V5c0-1.66-1.34-3-3-3S9 3.34 9 5v.18l5.98 5.99zM4.27 3 3 4.27l6.01 6.01V11c0 1.66 1.33 3 2.99 3 .22 0 .44-.03.65-.08l1.66 1.66c-.71.33-1.5.52-2.31.52-2.76 0-5.3-2.1-5.3-5.1H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c.91-.13 1.77-.45 2.54-.9L19.73 21 21 19.73 4.27 3z"/></svg>';

const params = new URLSearchParams(location.search);
const stage = document.getElementById("stage") as HTMLElement;
const grid = document.createElement("div");
grid.id = "grid";
const share = document.createElement("div");
share.id = "share";
stage.append(grid);

const tiles = new Map<string, HTMLElement>();
const room = new Room({ adaptiveStream: false, dynacast: false });

// Les participants invisibles (dont le robot d'enregistrement) ne sont pas filmés.
const isVisible = (p: Participant): boolean => !p.identity.startsWith("EG_");
const nameOf = (p: Participant): string => p.name?.trim() || p.identity;
const initials = (name: string): string =>
  name.split(/\s+/).filter(Boolean).slice(0, 2).map((s) => s[0]!.toUpperCase()).join("") || "?";

function pub(p: Participant, source: Track.Source): TrackPublication | undefined {
  return p.getTrackPublication(source);
}

function makeTile(p: Participant): HTMLElement {
  const tile = document.createElement("div");
  tile.className = "tile no-video";
  tile.innerHTML =
    `<video autoplay playsinline muted></video><div class="avatar"><span></span></div>` +
    `<div class="label"></div><div class="muted">${MIC_OFF}</div>`;
  tile.dataset.id = p.identity;
  return tile;
}

function attach(el: HTMLVideoElement, publication?: TrackPublication): boolean {
  const track = publication?.track;
  if (!track || publication?.isMuted) {
    track?.detach(el);
    el.srcObject = null;
    return false;
  }
  track.attach(el);
  return true;
}

function render(): void {
  const people = [room.localParticipant, ...room.remoteParticipants.values()].filter(isVisible);
  const sharer = people.find((p) => pub(p, Track.Source.ScreenShare)?.track);
  const ids = new Set(people.map((p) => p.identity));
  for (const [id, tile] of tiles) {
    if (!ids.has(id)) {
      tile.remove();
      tiles.delete(id);
    }
  }
  for (const p of people) {
    let tile = tiles.get(p.identity);
    if (!tile) {
      tile = makeTile(p);
      tiles.set(p.identity, tile);
    }
    const name = nameOf(p);
    (tile.querySelector(".label") as HTMLElement).textContent = name;
    (tile.querySelector(".avatar span") as HTMLElement).textContent = initials(name);
    const has = attach(tile.querySelector("video") as HTMLVideoElement, pub(p, Track.Source.Camera));
    tile.classList.toggle("no-video", !has);
    tile.classList.toggle("is-muted", !p.isMicrophoneEnabled);
    tile.classList.toggle("speaking", p.isSpeaking);
    grid.append(tile); // ré-ordonne selon `people`
  }
  // Partage d'écran : scène principale à gauche, participants en bande à droite.
  grid.classList.toggle("with-share", Boolean(sharer));
  if (sharer) {
    if (!share.isConnected) stage.prepend(share);
    if (!share.firstChild) share.innerHTML = `<video autoplay playsinline muted></video><div class="label"></div>`;
    attach(share.querySelector("video") as HTMLVideoElement, pub(sharer, Track.Source.ScreenShare));
    (share.querySelector(".label") as HTMLElement).textContent = `${nameOf(sharer)} présente`;
  } else {
    share.remove();
    share.innerHTML = "";
  }
  // Grille adaptative (1, 2, 4, 6, 9... tuiles), hors partage.
  const n = Math.max(people.length, 1);
  const cols = n === 1 ? 1 : n <= 4 ? 2 : n <= 9 ? 3 : 4;
  const rows = Math.ceil(n / cols);
  if (!sharer) {
    grid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
    grid.style.gridTemplateRows = `repeat(${rows}, 1fr)`;
  } else {
    grid.style.gridTemplateRows = "";
  }
}

// Audio : chaque piste distante est jouée dans la page pour que Chrome la capte.
function audioIn(track: Track): void {
  if (track.kind === Track.Kind.Audio) document.body.append(track.attach());
}

async function main(): Promise<void> {
  const url = params.get("url");
  const token = params.get("token");
  if (!url || !token) throw new Error("url et token requis");
  const redraw = () => render();
  room
    .on(RoomEvent.TrackSubscribed, (t) => {
      audioIn(t);
      redraw();
    })
    .on(RoomEvent.TrackUnsubscribed, (t) => {
      t.detach().forEach((e) => e.remove());
      redraw();
    })
    .on(RoomEvent.ParticipantConnected, redraw)
    .on(RoomEvent.ParticipantDisconnected, redraw)
    .on(RoomEvent.TrackMuted, redraw)
    .on(RoomEvent.TrackUnmuted, redraw)
    .on(RoomEvent.LocalTrackPublished, redraw)
    .on(RoomEvent.ActiveSpeakersChanged, redraw)
    .on(RoomEvent.ParticipantNameChanged, redraw)
    .on(RoomEvent.Disconnected, () => console.log("END_RECORDING"));
  await room.connect(url, token, { autoSubscribe: true });
  room.remoteParticipants.forEach((p: RemoteParticipant) => p.trackPublications.forEach((x) => x.track && audioIn(x.track)));
  render();
  console.log("START_RECORDING");
}

main().catch((error) => {
  console.error("recording template", error);
});
