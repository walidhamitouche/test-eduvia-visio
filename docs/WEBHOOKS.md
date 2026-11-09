# Webhooks sortants (étape 12)

Visio prévient le backend d'Eduvia à chaque changement important. Destination : `WEBHOOK_URL`.
Signature : `WEBHOOK_SECRET` (≥ 32 caractères, partagé avec Eduvia).

## Événements

| `type` | Quand | `data` |
|---|---|---|
| `meeting.started` | première personne admise connectée | `started_at` |
| `participant.joined` | une personne entre en salle | `participant {display_name, external_id, role}`, `joined_at` |
| `participant.left` | une personne sort (ou clôture) | `participant`, `joined_at`, `left_at`, `duration_seconds` |
| `meeting.ended` | fin de créneau, fin par l'organisateur ou par l'API | `reason` (`slot_end`, `organizer`, `api`), `started_at`, `ended_at` |
| `recording.ready` | l'enregistrement est terminé et le fichier est sur le S3 | `recording_id`, `size_bytes`, `duration_seconds` (`null` si inconnue) |
| `recording.failed` | l'enregistrement n'a pas pu être produit | `recording_id`, `reason` (texte lisible) |
| `transcript.ready` | la transcription est écrite sur le S3 | `transcript_id`, `language`, `segments`, `speakers`, `duration_seconds` |
| `transcript.failed` | la transcription n'a pas pu être produite (après relances) | `transcript_id`, `reason` (texte lisible) |

Toutes les dates sont en heure de Paris (offset explicite). À la clôture, les `participant.left` des
présents partent **avant** `meeting.ended`.

### Enregistrement (`recording.ready` / `recording.failed`)

- Un seul des deux événements par enregistrement, **après** `meeting.ended` (le fichier est envoyé
  au S3 une fois la salle fermée : quelques secondes, davantage pour une longue réunion).
- `recording_id` est l'identifiant de l'artefact ; la récupération du fichier passe par l'API :
  `GET /v1/recordings/{recording_id}` renvoie une `download_url` valable 15 minutes (voir `DECISIONS.md`
  §6 undecies)
  (étape 15). Les webhooks ne contiennent **ni clé S3 ni URL**.
- Motifs de `recording.failed` : `démarrage impossible : …` (Egress injoignable après plusieurs
  essais), `la réunion s'est terminée avant le démarrage de l'enregistrement`, `aucune piste audio
  ou vidéo n'a été publiée : rien à enregistrer`, `Egress a échoué : …`, `enregistrement
  interrompu`, `limite de durée ou de taille atteinte`, `Egress annonce la fin de
  l'enregistrement mais le fichier est absent du stockage`, `Egress n'a donné aucune nouvelle de
  l'enregistrement après la fin de la réunion` (au bout de 15 minutes).
- Une réunion avec `recording: false` n'émet aucun de ces événements.

### Transcription (`transcript.ready` / `transcript.failed`) — étape 16c

- Un seul des deux événements par transcription, **après** `meeting.ended` (le calcul démarre quand les pistes audio sont terminées ; compter de quelques minutes à quelques heures selon la durée et le matériel).
- Le contenu se lit par `GET /v1/meetings/{id}/transcript` (`?format=json|txt|srt|vtt`) ; le webhook ne contient aucun texte.
- Une réunion sans parole donne `transcript.ready` avec `segments: 0`.
- Motif de `transcript.failed` : `transcription impossible : …` (modèle absent, fichier illisible, stockage en panne).
- Une réunion avec `transcription: false` n'émet aucun de ces événements.

## Exemple

```http
POST /hooks/visio HTTP/1.1
Content-Type: application/json
X-Visio-Event-Id: evt_3f9c…
X-Visio-Timestamp: 1793855043
X-Visio-Signature: v1=5b1c…
```

```json
{
  "id": "evt_3f9c…",
  "type": "participant.left",
  "created_at": "2027-01-02T17:01:02+01:00",
  "meeting": {
    "id": "mtg_…", "title": "Cours BTS MCO – Groupe A",
    "starts_at": "2027-01-02T16:00:00+01:00", "ends_at": "2027-01-02T18:00:00+01:00",
    "metadata": { "eduvia_session_id": 4242 }
  },
  "data": {
    "participant": { "display_name": "Lina Benali", "external_id": "stu_981", "role": "participant" },
    "joined_at": "2027-01-02T16:02:10+01:00",
    "left_at": "2027-01-02T17:01:00+01:00",
    "duration_seconds": 3530
  }
}
```

`meeting.metadata` est celui fourni à la création : c'est la clé de rapprochement côté Eduvia.

## Vérifier la signature

`signature = "v1=" + HMAC_SHA256(secret, "<X-Visio-Timestamp>." + <corps brut>)` en hexadécimal.
Utiliser le **corps brut**, pas le JSON re-sérialisé. Refuser si le timestamp date de plus de
5 minutes (anti-rejeu) et comparer en temps constant.

```python
import hashlib, hmac, time


def verify(secret: str, body: bytes, timestamp: str, signature: str) -> bool:
    if abs(time.time() - int(timestamp)) > 300:
        return False
    expected = (
        "v1="
        + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, signature)
```

```js
import { createHmac, timingSafeEqual } from "node:crypto";
const expected = "v1=" + createHmac("sha256", secret).update(`${timestamp}.`).update(rawBody).digest("hex");
const ok = Math.abs(Date.now() / 1000 - Number(timestamp)) <= 300 &&
  expected.length === signature.length && timingSafeEqual(Buffer.from(expected), Buffer.from(signature));
```

## Livraison

- Répondre **2xx** = reçu. Tout autre statut ou une erreur réseau = échec.
- Relances après 10 s, 1 min, 5 min, 30 min, 2 h (6 tentatives au total), puis l'événement est marqué
  `abandoned`. Nous pouvons le **rejouer à la main** (`jobs-retry`) : il repart avec 6 nouvelles
  tentatives et **le même `id`**. La signature est recalculée à chaque tentative.
- **Idempotence** : `id` (et `X-Visio-Event-Id`) est identique à chaque tentative, rejeu manuel compris ; dédoublonner dessus.
- **Échec puis succès** : après un `recording.failed` (ou `transcript.failed`), un rejeu réussi envoie
  `recording.ready` (ou `transcript.ready`) pour le **même** `recording_id` (`transcript_id`) : le dernier
  événement reçu fait foi.
- L'ordre d'arrivée n'est garanti qu'en l'absence de relance : se fier aux dates des événements.
- Les événements sont écrits en base dans la même transaction que le changement qu'ils annoncent :
  un redémarrage ne perd rien.

## Tester en local

`uv run python deploy/dev/webhook-receiver.py` écoute sur `http://localhost:9000/hooks/visio`,
vérifie la signature et affiche chaque événement. Mettre `WEBHOOK_URL=http://localhost:9000/hooks/visio`
et, dans la même session, le même `WEBHOOK_SECRET` (`WEBHOOK_SECRET=… uv run python …`).

Côté LiveKit, le serveur doit pointer sur `POST /v1/livekit/webhook` du backend (voir
`deploy/dev/livekit-dev.ps1`) : c'est lui qui alimente présence et émargement.
