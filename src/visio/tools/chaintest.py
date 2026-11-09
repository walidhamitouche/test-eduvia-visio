"""Test automatique de la chaîne de transcription, étape par étape (étape 16d, diagnostic).

    uv run task chain-test C:\\Temp\\test.m4a C:\\Temp\\reference.txt
        [--terms "RAG, NLP"] [--sans-mp4]

Pour trouver **où** la qualité se perd, la même voix (ton fichier) est transcrite à chaque étape :

  0. fichier direct                : le moteur seul, sans plateforme (le meilleur score possible) ;
  1. plateforme, filtres actifs    : vrai navigateur (faux micro = ton fichier), vraie réunion,
                                     pistes Egress, worker, API (réglages actuels de l'interface) ;
  2. plateforme, filtres coupés    : pareil, mais echoCancellation / noiseSuppression /
                                     autoGainControl forcés à `false` dans le navigateur ;
  1b / 2b. MP4 enregistré          : le MP4 composite de la réunion, transcrit avec le même moteur.

Chaque étape affiche **la transcription complète** (avec horodatage), le texte de référence et la
liste des mots différents. Tout est aussi écrit en UTF-8 dans `data/chain-test/<date>/` :
`rapport.md` + un `.txt` par étape. **Ne redirige pas la sortie avec `|` ou `>`** (PowerShell
abîme les accents) : ouvre plutôt le rapport.

Prérequis (comme pour une vraie réunion) : `docker-dev.ps1 up`, `uv run task dev-lan`,
`uv run task transcriber`, interface construite (`uv run task web-build`) et Edge, Chrome ou
Chromium pour Playwright. Compte ~15 minutes pour tout (``--sans-mp4`` en retire ~3).
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import socket
import sys
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from visio.config import ConfigurationError, get_settings
from visio.services.transcription import TranscriptionEngineError
from visio.services.whisper_engine import WhisperTranscriber
from visio.tools import wer as werlib

DEFAULT_TERMS = (
    "Machine Learning, Deep Learning, Natural Language Processing, NLP, "
    "Retrieval-Augmented Generation, RAG, Python, FastAPI, Vector Databases, Embeddings, "
    "Large Language Models, LLMs, React, JavaScript, REST APIs, Software Engineering"
)
PARIS = ZoneInfo("Europe/Paris")
BASE_URL = os.environ.get("CHAIN_TEST_BASE_URL", "http://localhost:8000")
HEAD_SILENCE = 8.0  # avant la parole : le temps d'entrer en salle et que la piste soit prête
TAIL_SILENCE = 90.0  # après : le faux micro tourne en boucle, il ne doit pas rejouer le texte
AFTER_SPEECH = 10.0  # on reste un peu après la fin de la parole
TRANSCRIPT_TIMEOUT = 900.0
RECORDING_TIMEOUT = 180.0
ARGS = [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
]

# Force les trois filtres du navigateur à `false` et note ce que le navigateur applique vraiment.
RAW_AUDIO_SCRIPT = """
(() => {
  const media = navigator.mediaDevices;
  const original = media.getUserMedia.bind(media);
  window.__micSettings = [];
  media.getUserMedia = async (constraints) => {
    let wanted = constraints;
    if (__FORCE__ && constraints && constraints.audio) {
      const audio = constraints.audio === true ? {} : { ...constraints.audio };
      audio.echoCancellation = false;
      audio.noiseSuppression = false;
      audio.autoGainControl = false;
      wanted = { ...constraints, audio };
    }
    const stream = await original(wanted);
    for (const track of stream.getAudioTracks()) {
      window.__micSettings.push(track.getSettings());
    }
    return stream;
  };
})();
"""


# --- audio -------------------------------------------------------------------------------------


def to_fake_mic_wav(source: Path, target: Path) -> float:
    """Convertit n'importe quel audio en WAV 48 kHz mono, avec du silence avant et après.

    Renvoie la durée de la parole (sans les silences)."""
    import av  # installé avec `uv sync --extra transcription`

    resampler = av.AudioResampler(format="s16", layout="mono", rate=48_000)
    chunks: list[bytes] = []
    with av.open(str(source)) as container:
        for frame in container.decode(audio=0):
            chunks.extend(piece.to_ndarray().tobytes() for piece in resampler.resample(frame))
        chunks.extend(piece.to_ndarray().tobytes() for piece in resampler.resample(None))
    speech = b"".join(chunks)
    rate = 48_000

    def silence(seconds: float) -> bytes:
        return b"\x00\x00" * int(rate * seconds)

    with wave.open(str(target), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(silence(HEAD_SILENCE) + speech + silence(TAIL_SILENCE))
    return len(speech) / 2 / rate


def launch_browser(playwright: Any, fake_wav: Path) -> Any:
    """Chromium de Playwright, sinon Edge, sinon Chrome (`E2E_BROWSER` impose un choix)."""
    args = [*ARGS, f"--use-file-for-fake-audio-capture={fake_wav}"]
    imposed = os.environ.get("E2E_BROWSER")
    problems: list[str] = []
    for name in [imposed] if imposed else ["chromium", "msedge", "chrome"]:
        try:
            if name == "chromium":
                return playwright.chromium.launch(args=args)
            return playwright.chromium.launch(channel=name, args=args)
        except Exception as error:  # noqa: BLE001 - on essaie le suivant
            problems.append(f"{name} : {str(error).splitlines()[0]}")
    msg = "aucun navigateur utilisable (" + " ; ".join(problems) + ")"
    raise RuntimeError(msg)


# --- mesure et affichage -----------------------------------------------------------------------


@dataclass
class Result:
    name: str
    text: str = ""
    segments: list[str] = field(default_factory=list)
    note: str = ""
    sub: int = 0
    dele: int = 0
    ins: int = 0
    wer: float = 100.0
    terms: tuple[int, int] = (0, 0)


def word_diff(reference: str, hypothesis: str) -> list[str]:
    """Les seuls endroits où les deux textes diffèrent, en mots normalisés (comme le WER)."""
    ref, hyp = werlib.words(reference), werlib.words(hypothesis)
    lines = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=ref, b=hyp, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        before = " ".join(ref[max(0, i1 - 3) : i1])
        said = " ".join(ref[i1:i2]) or "(rien)"
        got = " ".join(hyp[j1:j2]) or "(rien)"
        lines.append(f"  ...{before}  [attendu : {said}]  [obtenu : {got}]")
    return lines


def score(result: Result, reference: str, terms: list[str]) -> None:
    ref, hyp = werlib.words(reference), werlib.words(result.text)
    result.sub, result.dele, result.ins = werlib.align(ref, hyp)
    result.wer = 100 * (result.sub + result.dele + result.ins) / max(1, len(ref))
    said = sum(werlib.count_term(t, ref) for t in terms)
    found = sum(min(werlib.count_term(t, ref), werlib.count_term(t, hyp)) for t in terms)
    result.terms = (found, said)


class Report:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.lines: list[str] = []

    def say(self, text: str = "") -> None:
        print(text, flush=True)
        self.lines.append(text)

    def show(self, result: Result, reference: str) -> None:
        self.say()
        self.say("=" * 100)
        self.say(f"ÉTAPE {result.name}")
        self.say("=" * 100)
        if result.note:
            self.say(result.note)
        self.say("--- transcription complète ---")
        for line in result.segments or [result.text]:
            self.say(line)
        self.say(
            f"--- WER {result.wer:.1f} %  ({result.sub} substitués, {result.dele} oubliés, "
            f"{result.ins} ajoutés)   termes : {result.terms[0]}/{result.terms[1]}"
        )
        self.say("--- mots différents de la référence ---")
        differences = word_diff(reference, result.text)
        for line in differences or ["  (aucun : texte identique à la référence)"]:
            self.say(line)
        slug = re.sub(r"[^a-z0-9]+", "_", result.name.lower()).strip("_")
        (self.folder / f"{slug}.txt").write_text(result.text + "\n", encoding="utf-8")

    def summary(self, results: list[Result]) -> None:
        self.say()
        self.say("=" * 100)
        self.say("RÉSUMÉ")
        self.say("=" * 100)
        for r in results:
            self.say(
                f"  {r.name:<44} WER {r.wer:5.1f} %   sub {r.sub:3d}  oubliés {r.dele:3d}  "
                f"ajoutés {r.ins:3d}   termes {r.terms[0]}/{r.terms[1]}"
            )
        self.say()
        self.say("Lecture : comparer 0 → 1 (perte due à la plateforme) puis 1 → 2 (perte due aux")
        self.say(
            "filtres du navigateur), et 1 → 1b (perte due au MP4). Un écart de quelques points"
        )
        self.say("peut n'être que du bruit : un seul essai par étape.")
        (self.folder / "rapport.md").write_text("\n".join(self.lines) + "\n", encoding="utf-8-sig")


# --- les étapes --------------------------------------------------------------------------------


def transcribe_local(engine: WhisperTranscriber, audio: Path) -> tuple[str, list[str]]:
    pieces = engine.transcribe(audio, language="fr")
    lines = [f"[{p.start:7.1f} -> {p.end:7.1f}] {p.text.strip()}" for p in pieces]
    return " ".join(p.text.strip() for p in pieces), lines


class Platform:
    """Appels à l'API de la plateforme (celle qui tourne déjà : `uv run task dev-lan`)."""

    def __init__(self, key: str) -> None:
        self.headers = {"Authorization": f"Bearer {key}"}

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = httpx.request(
            method, f"{BASE_URL}{path}", headers=self.headers, timeout=30, **kwargs
        )
        if response.status_code >= 400:
            msg = f"{method} {path} -> {response.status_code} {response.text[:300]}"
            raise RuntimeError(msg)
        return response.json() if response.content else None

    def create_meeting(self) -> tuple[str, str]:
        start = datetime.now(PARIS) - timedelta(seconds=60)
        meeting = self.request(
            "POST",
            "/v1/meetings",
            json={
                "title": "Test chaîne transcription",
                "starts_at": start.isoformat(timespec="seconds"),
                "ends_at": (start + timedelta(hours=1)).isoformat(timespec="seconds"),
                "recording": True,
                "transcription": True,
                "organizers": [{"display_name": "Marie Durand", "external_id": "staff_42"}],
            },
        )
        link = self.request(
            "POST",
            f"/v1/meetings/{meeting['id']}/links",
            json={"role": "participant", "display_name": "Lina Benali", "external_id": "stu_1"},
        )
        return str(meeting["id"]), str(link["url"])

    def wait_transcript(self, meeting_id: str, say: Callable[[str], None]) -> dict[str, Any]:
        """Attend `ready` ou `failed`. Juste après la fin de la réunion, la transcription n'existe
        pas encore : l'API répond 404 `artifact_not_found` (le worker la crée en la prenant en
        charge), ce qui veut dire « pas encore », pas « erreur »."""
        started = time.monotonic()
        warned = False
        while time.monotonic() - started < TRANSCRIPT_TIMEOUT:
            response = httpx.get(
                f"{BASE_URL}/v1/meetings/{meeting_id}/transcript",
                headers=self.headers,
                timeout=30,
            )
            if response.status_code == 200:
                data: dict[str, Any] = response.json()
                if data["status"] in ("ready", "failed"):
                    return data
            elif response.status_code != 404:
                msg = f"GET transcript -> {response.status_code} {response.text[:300]}"
                raise RuntimeError(msg)
            if not warned and time.monotonic() - started > 120:
                say(
                    "  ... toujours rien après 2 min : `uv run task transcriber` tourne-t-il ? "
                    "Et la piste audio du faux micro existe-t-elle (`uv run task tracks-list`) ?"
                )
                warned = True
            time.sleep(5)
        msg = f"transcription absente après {TRANSCRIPT_TIMEOUT:.0f} s"
        raise RuntimeError(msg)

    def download_recording(self, meeting_id: str, target: Path) -> bool:
        deadline = time.monotonic() + RECORDING_TIMEOUT
        while time.monotonic() < deadline:
            data = self.request("GET", f"/v1/meetings/{meeting_id}/recordings")
            ready = [r for r in data["recordings"] if r["status"] == "ready" and r["download_url"]]
            if ready:
                url = str(ready[0]["download_url"])
                if url.startswith("/"):
                    url = f"{BASE_URL}{url}"
                target.write_bytes(httpx.get(url, timeout=120, follow_redirects=True).content)
                return True
            if any(r["status"] == "failed" for r in data["recordings"]):
                return False
            time.sleep(5)
        return False


def run_platform(
    platform: Platform,
    browser: Any,
    *,
    force_raw: bool,
    speech_seconds: float,
    say: Callable[[str], None],
    mp4_target: Path | None,
) -> tuple[Result, bool]:
    label = "filtres coupés" if force_raw else "filtres actifs"
    meeting_id, url = platform.create_meeting()
    say(f"  réunion {meeting_id} ({label}) : entrée en salle avec le faux micro...")
    context = browser.new_context(permissions=["microphone", "camera"], locale="fr-FR")
    context.add_init_script(RAW_AUDIO_SCRIPT.replace("__FORCE__", "true" if force_raw else "false"))
    page = context.new_page()
    page.set_default_timeout(30_000)
    try:
        page.goto(f"{BASE_URL}/j/{url.rsplit('/j/', 1)[1]}")
        page.get_by_role("button", name=re.compile("Rejoindre la réunion")).click()
        page.get_by_test_id("room").wait_for()
        stay = HEAD_SILENCE + speech_seconds + AFTER_SPEECH
        say(f"  en salle ; on reste {stay:.0f} s (le faux micro lit ton fichier)...")
        time.sleep(stay)
        settings = page.evaluate("window.__micSettings || []")
    finally:
        platform.request("POST", f"/v1/meetings/{meeting_id}/end")
        context.close()
    note = f"réglages audio réellement appliqués par le navigateur : {settings}"
    say("  réunion terminée ; attente de la transcription du worker...")
    data = platform.wait_transcript(meeting_id, say)
    result = Result(f"plateforme, {label}", note=note)
    if data["status"] != "ready":
        result.note += f"\nÉCHEC de la transcription : {data.get('error')}"
        return result, False
    result.segments = [
        f"[{s['start']:7.1f} -> {s['end']:7.1f}] {s['speaker']} ({s['external_id']}) : {s['text']}"
        for s in data["segments"]
    ]
    result.text = " ".join(s["text"] for s in data["segments"])
    got_mp4 = False
    if mp4_target is not None:
        say("  téléchargement du MP4 enregistré...")
        got_mp4 = platform.download_recording(meeting_id, mp4_target)
    return result, got_mp4


# --- programme ---------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("audio", type=Path, help="ta lecture du texte (m4a, mp3, wav...)")
    parser.add_argument("reference", type=Path, help="le texte de référence (.txt)")
    parser.add_argument(
        "--terms",
        nargs="?",
        const=DEFAULT_TERMS,
        default=DEFAULT_TERMS,
        help="termes à compter, séparés par des virgules (défaut : ceux du texte de référence)",
    )
    parser.add_argument("--sans-mp4", action="store_true", help="ne transcrit pas les MP4")
    parser.add_argument("--direct-seulement", action="store_true", help="étape 0 uniquement")
    args = parser.parse_args(argv)
    if not args.audio.is_file() or not args.reference.is_file():
        print("fichier introuvable : vérifie les deux chemins")
        return 1
    try:
        settings = get_settings()
    except ConfigurationError as error:
        print(error)
        return 1
    key = os.environ.get("API_KEY", "")
    if not key and not args.direct_seulement:
        print("API_KEY absente : lance  uv run task chain-test ...  (dotenvx l'injecte)")
        return 1

    if not args.direct_seulement:
        try:
            httpx.get(f"{BASE_URL}/health", timeout=5)
        except httpx.HTTPError:
            print(
                f"L'API ne répond pas sur {BASE_URL} : lance  uv run task dev-lan  (autre terminal)"
            )
            print("avec  docker-dev.ps1 up  et  uv run task transcriber  déjà démarrés.")
            return 1

    if not args.direct_seulement:
        endpoint = urlparse(str(settings.s3_endpoint))
        try:
            socket.create_connection(
                (endpoint.hostname or "localhost", endpoint.port or 9000), 3
            ).close()
        except OSError:
            print(
                f"Le S3 ne répond pas sur {settings.s3_endpoint} : sans lui, aucune transcription."
            )
            print("Lance  docker compose -f deploy\\docker-compose.dev.yml up -d --wait s3")
            return 1

    folder = Path("data") / "chain-test" / datetime.now().strftime("%Y%m%d-%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    report = Report(folder)
    reference = werlib.read_text(args.reference)
    terms = [t.strip() for t in args.terms.split(",") if t.strip()]
    engine = WhisperTranscriber(
        settings.transcription_model,
        model_dir=settings.transcription_model_dir,
        device=settings.transcription_device,
        compute_type=settings.transcription_compute_type,
        vocabulary="",  # règle de mesure (DECISIONS 160) : jamais de vocabulaire dans un score
    )
    report.say(f"Dossier du rapport : {folder.resolve()}")
    report.say(f"Texte de référence ({len(werlib.words(reference))} mots) :")
    report.say(reference.strip())
    results: list[Result] = []

    try:
        report.say("\n[étape 0] fichier direct...")
        direct = Result("0. fichier direct (moteur seul)")
        direct.text, direct.segments = transcribe_local(engine, args.audio)
        score(direct, reference, terms)
        report.show(direct, reference)
        results.append(direct)
        if args.direct_seulement:
            report.summary(results)
            return 0

        wav = folder / "faux-micro.wav"
        speech_seconds = to_fake_mic_wav(args.audio, wav)
        report.say(f"\nFaux micro prêt : {speech_seconds:.0f} s de parole ({wav.name}).")
        from playwright.sync_api import sync_playwright

        platform = Platform(key)
        with sync_playwright() as playwright:
            browser = launch_browser(playwright, wav.resolve())
            try:
                for raw, number in ((False, "1"), (True, "2")):
                    mp4 = None if args.sans_mp4 else folder / f"reunion-{number}.mp4"
                    label = "filtres coupés" if raw else "filtres actifs"
                    report.say(f"\n[étape {number}] plateforme, {label}")
                    result, got_mp4 = run_platform(
                        platform,
                        browser,
                        force_raw=raw,
                        speech_seconds=speech_seconds,
                        say=report.say,
                        mp4_target=mp4,
                    )
                    result.name = f"{number}. {result.name}"
                    score(result, reference, terms)
                    report.show(result, reference)
                    results.append(result)
                    if mp4 is not None and got_mp4:
                        report.say(f"  transcription du MP4 ({mp4.name})...")
                        mp4_result = Result(f"{number}b. MP4 enregistré ({label})")
                        mp4_result.text, mp4_result.segments = transcribe_local(engine, mp4)
                        score(mp4_result, reference, terms)
                        report.show(mp4_result, reference)
                        results.append(mp4_result)
                    elif mp4 is not None:
                        report.say("  (MP4 non disponible : Egress a-t-il tourné ?)")
            finally:
                browser.close()
    except (TranscriptionEngineError, RuntimeError, httpx.HTTPError) as error:
        report.say(f"\nÉCHEC : {error}")
        report.summary(results)
        return 1
    report.summary(results)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
