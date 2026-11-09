# Recette finale : les 9 scénarios (étape 21d)

Pour chaque scénario : une **preuve automatique** (`.\deploy\dev\recette.ps1`, tests rejoués et
compte rendu daté dans `data/recette/`) et une **vérification à la main**, qui seule prouve ce que
voient de vrais navigateurs et un vrai LiveKit. Un scénario est **vert** quand les deux sont verts.

## Avant de commencer

```powershell
.\deploy\dev\docker-dev.ps1 up       # LiveKit, Valkey, S3, Egress
uv run task dev-lan                  # l'API, dans un 2e terminal
.\deploy\dev\recette.ps1             # preuves automatiques (scénarios 1 à 8), ~4 min
```

Les vérifications à la main se font avec **deux navigateurs** (ou une fenêtre privée) : un lien
**organisateur**, un lien **participant**. `.\deploy\dev\nouvelle-reunion.ps1` crée la réunion et
affiche les liens (options : `-DansMinutes`, `-DureeMinutes`, `-SalleAttente`, `-Nominatifs`,
`-DeuxOrganisateurs`, `-Enregistrement`, `-Transcription`).

Clé d'API pour les appels ci-dessous (PowerShell) :

```powershell
$h = @{ Authorization = "Bearer " + (dotenvx get API_KEY -f .env.development).Trim() }
```

## Scénarios

### 1. Créneau strict

*15 h 55 refusé, 16 h accepté, coupure à 18 h, 18 h 01 refusé.*

- **Automatique** : `test_slot`, `test_join` (dont `scenario_1_slot_without_margin`), `test_lifecycle`, `test_preview`, `test_restart`, avec horloge simulée.
- **À la main** (créneau court, en vrai) : `.\deploy\dev\nouvelle-reunion.ps1 -DansMinutes 3 -DureeMinutes 3`. (a) Ouvrir le lien participant **avant** l'heure : page d'attente, **aucune entrée**. (b) À l'heure : l'entrée est possible. (c) À la fin du créneau : la salle est coupée pour tout le monde. (d) Rouvrir le lien : refusé.

### 2. URL inventée ou modifiée

- **Automatique** : `test_links`, `test_join`, `test_preview`, `test_web`.
- **À la main** : ouvrir `http://localhost:8000/j/inventé`, puis un vrai lien dont on change **un caractère**. Attendu : une page d'erreur **identique** dans les deux cas, **jamais une salle vide**.

### 3. Micro coupé et exclusion définitive

- **Automatique** : `test_moderation`, `test_presence_chat`.
- **À la main** : participant dans la salle, micro ouvert. L'organisateur **coupe son micro** (il est coupé), puis l'**exclut**. Le participant est déconnecté ; son lien **ne marche plus** (essayer de revenir). Un participant ne peut pas modérer : `.\deploy\dev\test-securite.ps1 -Jeton "<jeton du lien participant>"` doit tout refuser (403).

### 4. Salle d'attente

- **Automatique** : `test_lobby`.
- **À la main** : `nouvelle-reunion.ps1 -SalleAttente`. Le participant voit l'attente et **n'entre pas** ; l'organisateur le voit dans la liste, l'**admet** (il entre) ; refuser une autre personne (elle ne peut pas redemander) ; « tout admettre ».

### 5. Nom nominatif : visio, présence, transcription

- **Automatique** : `test_join`, `test_presence_chat`, `test_tracks`, `test_transcription`, `test_transcripts`.
- **À la main** : `nouvelle-reunion.ps1 -Nominatifs "Lina Benali","Karim Zidane" -Transcription`. Le nom du lien apparaît dans la salle **sans qu'on le saisisse** et ne peut pas être modifié ; la présence (scénario 7) porte ce nom et son identifiant Eduvia ; après la réunion, la transcription (`GET /v1/meetings/{id}/transcript`) attribue chaque phrase au **bon nom**.

### 6. Enregistrement sur S3 et webhooks signés

- **Automatique** : `test_recording`, `test_egress`, `test_webhooks_out`, `test_webhook_signature`, `test_webhook_docs`.
- **À la main** : `nouvelle-reunion.ps1 -Enregistrement`, parler, partager l'écran, terminer. Dans le S3, un **MP4** (audio de chacun, vidéo, **partage d'écran visible**) ; `GET /v1/meetings/{id}/recordings` donne un lien de téléchargement. Webhooks : `uv run python deploy/dev/webhook-receiver.py` (voir `WEBHOOKS.md`, « Tester en local ») affiche `meeting.started`, `participant.joined/left`, `meeting.ended`, `recording.ready` **avec une signature valide**. Une réunion avec `recording: false` n'enregistre rien et n'affiche aucun indicateur.

### 7. Chat et présence exportables

- **Automatique** : `test_presence_chat`, `test_presence`.
- **À la main** : après quelques messages et une sortie / rentrée d'un participant :
  `Invoke-RestMethod -Headers $h "http://localhost:8000/v1/meetings/<id>/attendance"` (présences avec heures d'entrée et de sortie) et `.../chat` (auteur, heure, message, dans l'ordre).

### 8. Suppression par API et URL déjà émise

- **Automatique** : `test_artifacts` (dont `scenario_8_…`), `test_artifacts_s3`, `test_transcripts`, `test_storage`.
- **À la main** : récupérer le lien de téléchargement d'un enregistrement, le **télécharger** (il marche), puis `Invoke-RestMethod -Method Delete -Headers $h "http://localhost:8000/v1/recordings/<id>"`. Le **même lien** doit alors être refusé, et le fichier **absent du S3** (`uv run task s3-check` ou la console du S3).

### 9. 10 réunions × 30 participants

- **Automatique** : non. Outils : `deploy/dev/test-charge.ps1` (LiveKit), `uv run task load-api` (API).
- **État** : **à valider sur le serveur d'Eduvia** (décision du 2026-11-08, `DECISIONS.md` §6 septdecies, points 248 à 254). L'API tient 600 connexions sans erreur ; LiveKit tient 52 connexions (2 salles) et un enregistrement à ≈ 2,3 cœurs, sans fuite. Le test 10 × 30 n'a pas pu être tenu sur le PC de développement (puce portable, Docker Desktop, générateur sur la même machine) : cause côté banc d'essai, à confirmer sur serveur. **Attention : l'ancien « 10 × 30 » était un 10 × 26 (260 connexions)** ; les scripts sont corrigés (30 participants par salle).
- **À refaire sur le serveur** (générateur sur **une autre machine** du même réseau) :
  1. Diagnostic par paliers : `.\deploy\dev\diag-charge.ps1 -Cible Docker -Url ws://<ip-serveur>:7880 -ApiKey <clé> -ApiSecret <secret>` (2, 4, 6, 8, 10 salles de 30). Attendu : toutes les connexions tenues et perte de paquets faible ; le tableau `RESUME` et `data/diag/` sont joints à la recette.
  2. Charge nominale : `.\deploy\dev\test-charge.ps1 -Recorded 3 -Url ws://<ip-serveur>:7880`, au moins 30 minutes, **sans déconnexion anormale**.
  3. Puis le reste de la liste de contrôle (`DECISIONS.md` point 229) : marge × 1,5, 8 h, partage d'écran à la main, transcription en parallèle, MP4 d'une heure.

## Registre de recette

| # | Scénario | Preuve automatique (fichier de `data/recette/`) | Vérification à la main | Résultat | Date | Validé par |
|---|---|---|---|---|---|---|
| 1 | Créneau strict | OK, 123 tests (`recette-20261107-1519.txt`) | | | 2026-11-07 (auto) | ☐ |
| 2 | URL inventée ou modifiée | OK, 91 tests (`recette-20261107-1519.txt`) | | | 2026-11-07 (auto) | ☐ |
| 3 | Micro coupé + exclusion | OK, 35 tests (`recette-20261107-1519.txt`) | | | 2026-11-07 (auto) | ☐ |
| 4 | Salle d'attente | OK, 16 tests (`recette-20261107-1519.txt`) | | | 2026-11-07 (auto) | ☐ |
| 5 | Nom nominatif | OK, 112 tests (`recette-20261107-1519.txt`) | | | 2026-11-07 (auto) | ☐ |
| 6 | Enregistrement S3 + webhooks | OK, 88 tests (`recette-20261107-1519.txt`) | | | 2026-11-07 (auto) | ☐ |
| 7 | Chat et présence | OK, 30 tests (`recette-20261107-1519.txt`) | | | 2026-11-07 (auto) | ☐ |
| 8 | Suppression + URL émise | OK, 138 tests (`recette-20261107-1519.txt`) | | | 2026-11-07 (auto) | ☐ |
| 9 | 10 × 30 participants | partiel (voir §9) ; diagnostic du 2026-11-08 | **à faire sur le serveur d'Eduvia** (décision 2026-11-08) | | | ☐ |


## Vérifications complémentaires (revue du 2026-11-08)

Preuves automatiques : `uv run pytest tests/integration/test_robustness.py tests/unit/test_ratelimit.py -v`.

- **Plafond** : `MAX_PARTICIPANTS_PER_MEETING=2`, 3 navigateurs en participant : le 3e reçoit `meeting_full` ; un organisateur entre toujours.
- **Coupure avec webhooks en panne** : `WEBHOOK_URL` sur une adresse qui ne répond pas, `nouvelle-reunion.ps1 -DansMinutes 1 -DureeMinutes 2` : la salle est coupée à l'heure exacte.
- **Transcription en production** : `model-init` exécuté, puis une réunion avec `-Transcription` donne `transcript.ready`.
