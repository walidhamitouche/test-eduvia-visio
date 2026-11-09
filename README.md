# Eduvia Visio

> **Projet réalisé à partir d'une expression de besoin** (visioconférence auto-hébergée pilotée par API). Ce dépôt est une démonstration de ma méthode de travail : plan par étapes, décisions tracées, tests automatisés, documentation d'exploitation. Ce n'est pas une livraison contractuelle.
>
> **État d'avancement**
> - Réalisé et testé en local : réunions sur créneau strict, liens organisateur/participant, salle d'attente, modération, chat, présence, webhooks signés, enregistrement (Egress → S3), transcription locale (faster-whisper), URL de téléchargement de 15 min, suppression, logs et `/health`. Plus de 950 tests automatisés (unitaires, intégration, E2E).
> - **Non validé sur un serveur réel** : TURN sur le port 443 derrière un pare-feu d'entreprise ; charge de 10 réunions × 30 participants (scénario 9, mesures provisoires prises sur un PC de développement) ; mise en production.
> - Voir `docs/DECISIONS.md` (décisions et mesures) et `docs/PLAN-PROJET.md` (plan en 22 étapes).

Plateforme de visioconférence auto-hébergée, pilotée par API (réunions sur créneau strict, enregistrement, transcription, chat, présence, webhooks).

Documents de référence :
- `docs/PLAN-PROJET.md` : plan en 22 étapes avec tests et validations
- `docs/DECISIONS.md` : décisions, matrice de conformité au besoin, licences, mesures de charge
- `docs/INSTALL.md` : installation et déploiement (Coolify) ; `docs/CONFIGURATION.md` : variables ; `docs/RESEAU.md` : TURN, pare-feu, TLS
- `docs/EXPLOITATION.md` : guide d'exploitation (supervision, pannes, sauvegarde, mise à jour)
- `docs/WEBHOOKS.md` : webhooks sortants (aussi dans l'OpenAPI, `/docs` et `/openapi.json`)
- `docs/LICENCES.md` et `docs/LICENCES-AUTO.md` : licences des briques et des dépendances

## Prérequis (Windows)

```powershell
winget install --id Git.Git -e
winget install --id astral-sh.uv -e
uv python install 3.12
```

Ferme et rouvre le terminal après l'installation.

## Démarrage

```powershell
uv sync                  # crée .venv et installe les dépendances
uv run task dev          # lance l'API sur http://localhost:8000 (config chiffrée requise, voir ci-dessous)
```

- Santé : http://localhost:8000/health (vivant ou non) ; détail des dépendances et de la file de
  tâches : `GET /v1/health` avec la clé d'API (200 si tout va bien, 503 sinon)
- Documentation OpenAPI : http://localhost:8000/docs
- Exemples d'appels : `docs/exemples.http` (extension VS Code « REST Client »)

## Configuration chiffrée (dotenvx)

Voir `docs/CONFIGURATION.md`. En résumé :

```powershell
winget install OpenJS.NodeJS.LTS      # puis fermer / rouvrir le terminal
npm install -g @dotenvx/dotenvx
uv run task env-init development      # idem : test, production
uv run task env-check                 # aucun secret en clair ?
```

`.env.keys` contient les clés privées : **jamais dans Git**, à sauvegarder ailleurs.

## LiveKit en local (étape 6)

```powershell
# 1. télécharger livekit-server.exe (https://github.com/livekit/livekit/releases, windows_amd64)
#    et le placer dans deploy\dev\ (ignoré par Git) ou dans le PATH
.\deploy\dev\livekit-dev.ps1          # terminal 1 : LiveKit, avec la clé de .env.development
uv run task dev                         # terminal 2 : l'API
.\deploy\dev\nouvelle-reunion.ps1     # terminal 3 : crée une réunion et affiche les liens
```

Puis ouvre `http://localhost:8000/dev/livekit-test` (développement uniquement) dans deux onglets et colle un lien dans chacun.

## Interface de visio (étape 7)

L'interface (React, dossier `web/`) est construite puis servie par l'API sur la même adresse. Il faut Node.js (déjà installé pour dotenvx).

```powershell
uv run task web-install     # une fois : npm ci dans web/
uv run task web-build       # (re)construit web/dist ; à refaire après un changement dans web/
uv run task dev             # puis ouvre un lien http://localhost:8000/j/...
```

- Les textes sont dans `web/src/fr.ts` ; le nom, le logo et les couleurs viennent de `BRAND_*` (redémarrer le serveur suffit).
- Interface seule, avec rechargement à chaud : `uv run task web-dev` (port 5173, appels relayés vers l'API sur 8000).
- Téléphone sur le même Wi-Fi (pages seulement, micro et caméra exigent HTTPS) : `uv run task dev-lan`.

## Docker de dev : LiveKit + Valkey + S3 + Egress (étape 13)

```powershell
.\deploy\dev\docker-dev.ps1 up        # génère les configs, démarre tout, crée le bucket, vérifie Egress
.\deploy\dev\docker-dev.ps1 status    # état des services (logs [livekit|redis|s3|egress], down, reset)
uv run task dev-lan                     # le backend doit écouter sur toutes les interfaces (webhooks)
uv run task s3-check                    # S3 : écriture, lecture, suppression, bucket privé
uv run task egress-check                # Egress : enregistre 5 s, le MP4 doit arriver sur le S3
uv run task e2e-docker                  # non-régression : les E2E contre ce LiveKit (port 8000 libre !)
```

Arrêter avant tout le LiveKit « natif » (`livekit-dev.ps1`) : les deux utilisent le port 7880.

## Artefacts : récupération et suppression (étape 15)

```powershell
uv run task artifacts-check             # liste, URL de 15 min, téléchargement, Range, bucket privé (lecture seule)
uv run task artifacts-check --delete    # + suppression : DÉTRUIT l'enregistrement testé (scénario 8)
```

Les URL de téléchargement pointent sur l'API (jamais sur le S3) et durent 15 minutes au plus. Exemples : `docs/exemples.http`, section 11.

## Qualité


```powershell
uv run task check        # lint + types + tests
uv run task test         # tests seuls (sans les tests E2E)
uv run task web-check    # types de l'interface
uv run task e2e          # tests de bout en bout (navigateur + LiveKit) : voir tests/e2e/conftest.py
uv run task format       # formate le code
uv run task repo-check   # aucun secret versionné, `.env.*` chiffrés (après `git add .`)
uv run task licences     # inventaire des licences -> docs/LICENCES-AUTO.md
```

## Transcription (étape 16a)

- `GET /v1/meetings/{id}/transcript` : JSON par segments (début, fin, locuteur, `external_id`, texte) ; `?format=txt|srt|vtt` pour les exports.
- `DELETE /v1/meetings/{id}/transcript` : suppression définitive (S3 et base).
- Moteur de reconnaissance vocale (16c) : faster-whisper **local**, sans réseau. `uv sync --extra transcription`, modèle copié dans `data/models/whisper`, `uv run task transcriber-check` pour le vérifier hors ligne, puis `uv run task transcriber` (processus à part) pour traiter les réunions terminées.
- Pistes audio par participant (16b) : `.\deploy\dev\nouvelle-reunion.ps1 -Transcription`, puis `uv run task tracks-list`.
- Échecs (17c) : `uv run task jobs-list` liste ce qui est en échec (tâches, webhooks, enregistrements, transcriptions) ; `uv run task jobs-retry <id>` ou `--all` le rejoue (sans doublon). Recette automatique : `.\deploy\dev\test-rejeu.ps1`.

## Déploiement : installer le modèle de transcription (une fois)

```powershell
docker compose -f deploy/docker-compose.yml run --rm transcriber model-init
```

Le worker de transcription est hors ligne : sans cette commande, aucune transcription en production
(`docs/INSTALL.md` §6).
