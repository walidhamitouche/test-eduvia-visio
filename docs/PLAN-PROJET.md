# Eduvia Visio — Plan de réalisation de bout en bout

> Plateforme de visioconférence auto-hébergée, pilotée par API.
> Ce document est la **feuille de route unique** : chaque étape a un objectif, des tâches, des tests et une **porte de validation**.

---

## 0. Règles de travail

1. **Une étape à la fois.** On ne commence l'étape N+1 que lorsque tu écris explicitement : `GO étape N+1`.
2. **Chaque étape se termine par ses tests.** Si un test échoue, on corrige avant de te demander la validation.
3. **La validation est la tienne.** À la fin de chaque étape, je te donne un récap (ce qui est fait, résultats des tests, ce que tu dois vérifier à la main). Tu réponds `VALIDÉ` ou tu listes les corrections.
4. **Docker est repoussé à la Phase B** (étape 13). Toute la Phase A tourne nativement sous Windows.
5. **Aucun secret en clair** dans le dépôt, dès l'étape 2.
6. **Commits** : un commit (ou plus) par étape, tag Git `etape-NN-validee` posé après ta validation.

### Types de tests utilisés

| Sigle | Type | Outil |
|---|---|---|
| **U** | Test unitaire (logique pure) | pytest |
| **I** | Test d'intégration (API + base) | pytest + httpx (TestClient FastAPI) |
| **E2E** | Test navigateur (parcours réel) | Playwright |
| **M** | Vérification manuelle guidée (je te donne la checklist) | Toi, dans le navigateur |
| **C** | Test de charge | k6 / LiveKit load-tester |

### Tableau de suivi

| # | Étape | Phase | Statut | Validée |
|---|---|---|---|---|
| 0 | Cadrage et décisions | A | ✅ | ✅ 2026-11-02 |
| 1 | Environnement Windows et squelette du dépôt | A | ✅ | ✅ 2026-11-02 |
| 2 | Configuration chiffrée (dotenvx) | A | ✅ | ✅ 2026-11-02 |
| 3 | Domaine « réunion » et règle du créneau | A | ✅ | ✅ 2026-11-02 |
| 4 | API réunions (CRUD, auth par clé, OpenAPI) | A | ✅ | ✅ 2026-11-02 |
| 5 | Liens et jetons (organisateur, participant, nominatif) | A | ✅ | ✅ 2026-11-02 |
| 6 | LiveKit local et endpoint « rejoindre » | A | ✅ | ✅ 2026-11-03 |
| 7 | Interface de visio (FR, marque blanche) | A | ✅ | ✅ 2026-11-03 |
| 8 | Rôles et modération | A | ✅ | ✅ 2026-11-04 |
| 9 | Salle d'attente | A | ✅ | ✅ 2026-11-04 |
| 10 | Fin de créneau, clôture, annulation, modification | A | ✅ | ✅ 2026-11-04 |
| 11 | Présence et chat | A | ✅ | ✅ 2026-11-04 |
| 12 | Webhooks sortants signés | A | ✅ | ✅ 2026-11-04 |
| 13 | Docker Desktop : LiveKit + Redis + S3 local + Egress | B | ✅ | ✅ 2026-11-04 |
| 14 | Enregistrement vers S3 (14a à 14d) | B | ✅ | ✅ 2026-11-05 |
| 15 | API artefacts : URL temporaires et suppression | B | ✅ | ✅ 2026-11-05 |
| 16 | Transcription auto-hébergée (16a à 16d) | B | ✅ 16a à 16d ; qualité jugée suffisante (2026-11-08, `DECISIONS.md` 245-246) | ✅ 2026-11-06 |
| 17 | Robustesse et observabilité (17a à 17d) | B | 17a ✅ · 17b ✅ · 17c ✅ · 17d ✅ | ✅ 2026-11-07 |
| 18 | Réseau : TURN, pare-feu, TLS 443 (18a à 18c) | B | 18a ✅ · 18b ✅ · 18c ✅ ; relais de bout en bout et 443 seul reportés au serveur (étape 19) | ✅ 2026-11-07 |
| 19 | Docker Compose complet et déploiement Coolify (19a à 19c) | B | 19a ✅ code · 19b ✅ images construites et contrôlées en local ; déploiement réel et smoke test **reportés à la recette finale** (pas de serveur) | ✅ avec réserves 2026-11-07 |
| 20 | Tests de charge et dimensionnement (20a à 20e) | B | 20a ✅ · 20b ✅ avec réserve · 20c ✅ outillage (mesures ×1,5 et 8 h sur serveur) · 20d ✅ recommandation provisoire · 20e 🟡 diagnostic fait, scénario 9 **sur serveur** | ✅ avec réserves 2026-11-07 |
| 21 | Recette finale, licences, livrables | B | 21a ✅ · 21b ✅ · 21c ✅ · 21d 🟡 (preuves automatiques des scénarios 1 à 8 vertes ; vérifications à la main et scénario 9 sur serveur) | ☐ |

---

## Architecture cible (proposition)

```
Backend Eduvia ──(clé API, HTTPS)──► [ API Visio ]  ──► SQLite (état, liens, artefacts)
                                          │  ▲
                 jetons signés            │  │ webhooks LiveKit (présence, fin d'enregistrement)
                                          ▼  │
Navigateur ◄──── Front visio (React) ──► [ LiveKit Server ] ◄──► [ LiveKit Egress ] ──► S3
   (liens)                                  │  (TURN inclus)         │ (enregistrement)
                                            │                        ▼
                                            └──────► [ Worker transcription (faster-whisper) ] ──► S3
                                                                      │
                                    Webhooks sortants signés HMAC ◄───┘ (vers Eduvia)
```

### Choix proposés (à confirmer à l'étape 0)

| Sujet | Choix | Pourquoi |
|---|---|---|
| Brique WebRTC | **LiveKit** (Apache 2.0) | TURN intégré, Egress (enregistrement) intégré, webhooks, SDK navigateur, une seule image Docker, licence permissive (pas d'AGPL) |
| API métier | **Python 3.12 + FastAPI + Pydantic v2** | OpenAPI généré nativement, validation stricte, SDK serveur LiveKit officiel (`livekit-api`), même langage que la transcription |
| Base de données | **SQLite** (mode WAL) + SQLAlchemy 2 + Alembic | Volume faible (10 réunions), zéro service à déployer, simple sur Coolify. Migration Postgres possible plus tard |
| File de tâches et planificateur | Table `jobs` en base + worker Python (asyncio) | Pas de Redis supplémentaire côté API, reprise après redémarrage facile |
| Front | **React + Vite + `@livekit/components-react`** | Composants prêts, personnalisables (marque blanche) |
| Transcription | **faster-whisper** (MIT), dans le même code Python | Auto-hébergé, bon en français. Pistes audio **par participant** pour attribuer le locuteur sans diarisation. Modèle (`large-v3`, `medium`…) tranché à l'étape 16 selon les mesures |
| Stockage | S3 compatible (MinIO en dev local, S3 d'Eduvia en prod) | Conforme au besoin |
| Config | **dotenvx** (CLI, indépendant du langage) | Imposé par le besoin |
| Qualité et tests | uv, ruff, mypy, pytest, Playwright, k6 | Standard |

> **Note Windows** : le serveur LiveKit existe en binaire Windows (mode `--dev`), donc on avance sans Docker jusqu'à l'étape 12. L'**Egress** (enregistrement) exige Linux/Docker, d'où la Phase B.

### Traçabilité avec les scénarios de recette (section 8 du besoin)

| Scénario | Description courte | Étapes concernées |
|---|---|---|
| 1 | Créneau strict (15 h 55 refusé, 16 h OK, coupure 18 h, 18 h 01 refusé) | 3, 6, 10, 21 |
| 2 | URL inventée ou modifiée n'ouvre rien | 5, 6, 21 |
| 3 | Mute + exclusion définitive | 8, 21 |
| 4 | Salle d'attente | 9, 21 |
| 5 | Nom du lien nominatif dans visio, présence, transcription | 5, 7, 11, 16, 21 |
| 6 | Enregistrement sur S3 + webhooks signés | 12, 14, 16, 21 |
| 7 | Chat et présence exportables | 11, 21 |
| 8 | Suppression par API (S3 + URL déjà émise) | 15, 21 |
| 9 | 10 réunions × 30 participants | 20, 21 |

---

# PHASE A — Fondations (sans Docker)

---

## Étape 0 — Cadrage et décisions

**Objectif** : figer les choix avant d'écrire du code.

**Tâches**
- Confirmer la stack (tableau ci-dessus) ou la modifier.
- Décider du nom du dépôt et de l'arborescence (projet Python unique (`src/visio/`) + `web/` (React) + `tests/` + `deploy/` + `docs/`).
- Lister les licences des briques (LiveKit Apache 2.0, faster-whisper MIT, FastAPI MIT, React MIT, MinIO AGPL **en dev uniquement**, jamais livré).
- Décider des conventions de l'API (noms de champs en `snake_case`, erreurs `{ "error": { "code", "message" } }`).
- Écrire `docs/DECISIONS.md` (journal des décisions).

**Tests / vérifications**
- M : tu relis `docs/DECISIONS.md` et tu confirmes chaque décision.

**Porte de validation** : ☑ stack confirmée · ☑ arborescence confirmée · ☑ conventions API confirmées

> **Validée par Walid le 2026-11-02.** Les 8 points de la section 6 de `DECISIONS.md` sont acceptés tels que proposés (« on essaie, puis on verra après »).

---

## Étape 1 — Environnement Windows et squelette du dépôt

**Objectif** : un projet Python qui démarre, se teste et se lint sur ta machine.

**Tâches**
- Installer / vérifier : Git, **Python 3.12**, **uv** (gestionnaire de dépendances), Node.js LTS (pour le front et pour le CLI dotenvx), VS Code (extensions : Python, Pylance, Ruff, Playwright).
- Initialiser le projet `uv` (`pyproject.toml`), arborescence `src/visio/{api,domain,services,workers,db}`, `tests/{unit,integration,e2e}`.
- `ruff` (lint + format), `mypy` (types), `pytest`.
- Serveur FastAPI minimal avec `GET /health`.
- Commandes : `uv run task dev`, `test`, `lint`, `typecheck`, `check` (= lint + typecheck + tests).
- `.gitignore`, `.editorconfig`, `README.md` (démarrage rapide).

**Tests**
- I : `GET /health` renvoie `200 { "status": "ok" }`.
- U : un test trivial prouve que pytest fonctionne.
- M : `uv run task dev` démarre, `http://localhost:8000/health` répond dans le navigateur, `http://localhost:8000/docs` s'affiche.

**Porte de validation** : ☑ `uv run task check` vert chez toi · ☑ `/health` répond

> **Validée par Walid le 2026-11-02.**
>
> **Résultats constatés sur la machine de Walid (Windows, VS Code) :**
> - Outils : Git 2.56.0, uv 0.13.6, Python 3.12.16.
> - `uv sync` : venv `.venv` créé, 39 paquets installés.
> - `uv run task check` : lint OK, 16 fichiers formatés, mypy strict « no issues found in 13 source files », **4 tests passés**.
> - Serveur (`uv run task dev`) : `http://localhost:8000/health` renvoie `{"status":"ok"}` ; `http://localhost:8000/docs` affiche la documentation OpenAPI.
> - Git : premier commit propre (26 fichiers), `.venv` non versionné, arbre de travail propre.
>
> **Remarques :**
> - Un ancien contenu (`backend/`, `infra/`, `docker-compose.dev.yml`) datant d'un essai précédent a été supprimé du dossier et du commit.
> - Avertissement connu et sans effet : Starlette recommande `httpx2` pour son client de test. À revoir seulement s'il devient bloquant.
> - Le dossier `%USERPROFILE%\.local\bin` n'est pas dans le PATH (facultatif : `uv python update-shell`).
> - Tag Git à poser : `git tag etape-01-validee`.

---

## Étape 2 — Configuration chiffrée (dotenvx)

**Objectif** : aucun secret en clair, modèle « credentials Rails ».

**Tâches**
- Installer `@dotenvx/dotenvx`.
- Créer `.env.development`, `.env.test`, `.env.production` **chiffrés**, versionnés. Les clés privées (`.env.keys`) restent **hors dépôt** (dans `.gitignore`).
- Schéma de configuration validé au démarrage (Pydantic Settings) : clé API, secret des webhooks, URL LiveKit, endpoint S3, domaine, branding.
- Le démarrage échoue avec un message clair si une variable manque.
- Script `uv run task env-check` qui vérifie qu'aucun secret n'est en clair dans le dépôt.

**Tests**
- U : le schéma Pydantic refuse une config incomplète, accepte une config valide.
- I : l'application démarre via `dotenvx run -- uvicorn ...` avec `DOTENV_PRIVATE_KEY_DEVELOPMENT` seul.
- M : tu ouvres `.env.development` : les valeurs sont chiffrées (`encrypted:...`).
- Sécurité : `git grep` d'un faux secret de test ne trouve rien en clair.

**Porte de validation** : ☑ fichiers chiffrés versionnés · ☑ démarrage échoue proprement sans clé · ☑ procédure de sauvegarde des clés comprise

> **Validée par Walid le 2026-11-02.**
>
> **Résultats constatés sur la machine de Walid :**
> - `uv run task check` : lint OK, 25 fichiers formatés, mypy strict « no issues found in 21 source files », **32 tests passés**.
> - dotenvx 2.38.2 installé ; 19 valeurs chiffrées (`encrypted:`) dans `.env.development`, seule la clé publique est en clair.
> - `env-check` : OK ; un faux secret (`FAKE_TOKEN`) ajouté à `.env.test` est détecté, puis retiré.
> - `git ls-files` ne contient aucun fichier de clés ; `.env.keys` et `.env.keys.bak` sont ignorés.
> - Démarrage avec la clé : `/health` renvoie `{"status":"ok"}`. Sans `.env.keys` : démarrage refusé avec le message « Configuration chiffrée non déchiffrée ».
> - Clé de production régénérée (`env-init production --force`) car `.env.keys` avait été transmis hors de la machine ; commit `9d215d4`.
>
> **Remarques :**
> - Le démarrage sans clé affiche en plus une trace Python (cosmétique, à nettoyer plus tard si besoin).
> - Les valeurs de production (S3, LiveKit, domaine) restent provisoires : à renseigner avant l'étape 19.
> - Tag Git à poser : `git tag etape-02-validee`.
>
> **Livré** : `src/visio/config.py` (schéma Pydantic, durcissement production), outils `env-init` et `env-check` (`src/visio/tools/`), `.env.example`, `docs/CONFIGURATION.md`. Tâche `dev` lancée via `dotenvx run`.
> **Tests automatiques** : 31 passés dans mon environnement (config valide/invalide, secrets jamais affichés, production stricte, détection de secrets en clair). Vérifié aussi avec le vrai dotenvx : chiffrement des 3 environnements, démarrage avec la seule variable `DOTENV_PRIVATE_KEY_DEVELOPMENT`, refus de la production tant que des `CHANGE_ME` subsistent, détection d'un fichier en clair.
> **Second durcissement (constaté chez Walid)** : une copie `.env.keys.bak` de la clé privée est apparue dans `git status` (le `.gitignore` n'ignorait que `.env.keys`). Corrigé : `.env.keys.*` et `*.keys.bak` sont ignorés, et `env-check` signale toute copie de clés non ignorée. Rien n'avait été commité.
> **Défaut trouvé et corrigé pendant mes essais** : dotenvx prenait `#` des couleurs pour un commentaire et laissait ces valeurs en clair ; elles sont maintenant entre guillemets, et `env-check` détecte ce cas.

---

## Étape 3 — Domaine « réunion » et règle du créneau

**Objectif** : coder la règle centrale en **logique pure**, testée à fond, avant toute API.

**Tâches**
- Modèle de données SQLite + migrations : `meetings`, `links`, `participants_sessions` (préparée), `jobs`, `artifacts` (préparée).
- États : `scheduled`, `live`, `ended`, `cancelled`.
- Module `slot` (fonctions pures, horloge injectable) :
  - `canJoin(meeting, now)` : tient compte de `starts_at`, `ends_at`, `early_join_minutes`, état.
  - `statusAt(meeting, now)` : `not_open_yet`, `open`, `ended`, `cancelled`.
  - `secondsUntilEnd(meeting, now)` : pour l'avertissement de fin.
- Validation : `ends_at > starts_at`, durée max (ex. 12 h), dates ISO 8601 avec décalage.

**Tests (U, horloge simulée)**
- 15:55 sans marge → refusé · 16:00:00 → accepté · 17:59:59 → accepté · 18:00:00 → refusé · 18:01 → refusé.
- Avec `early_join_minutes = 10` : 15:49 refusé, 15:50 accepté.
- Réunion annulée : toujours refusée.
- Réunion terminée par l'organisateur à 17:00 : refusée à 17:01 même dans le créneau.
- Décalages horaires : `+01:00` et `+02:00` (changement d'heure) donnent la même instant UTC attendu.
- Créneau incohérent (`ends_at <= starts_at`) rejeté.

**Porte de validation** : ☑ tous les cas ci-dessus verts · ☑ tu valides la règle d'ouverture anticipée (0 par défaut)

> **Validée par Walid le 2026-11-02.** Les points 9, 10 et 11 de `DECISIONS.md` §6 ter sont acceptés tels que proposés.
>
> **Résultats constatés sur la machine de Walid (Windows, Python 3.12.16) :**
> - `uv sync` : alembic 1.20.1, sqlalchemy 2.1.6, tzdata 2026.6, mako et markupsafe installés.
> - `uv run task check` : lint OK, 39 fichiers formatés, mypy strict « no issues found in 35 source files », **117 tests passés**.
> - `uv run task db-upgrade` : « Base de données à jour. » ; `data/visio.db` créé, ignoré par Git (règle `data/`).
> - Tables constatées : `alembic_version`, `artifacts`, `jobs`, `links`, `meetings`, `participant_sessions` ; version de migration `0001`.
> - `env-check` : OK. Commit `ed3a89a` (21 fichiers), aucun fichier de clés ni de données.
> - Tag Git à poser : `git tag etape-03-validee`.
>
> **Remarque :** l'avertissement Starlette sur `httpx2` est toujours présent, sans effet.
>
> **Livré** : domaine pur `src/visio/domain/` (`slot.py` : `parse_iso8601`, `validate_slot`, `status_at`, `can_join`, `seconds_until_end`, `to_paris`, `has_started`, `can_modify` ; `clock.py` : `SystemClock` et `FixedClock` ; `meeting.py`, `roles.py`, `errors.py`). Base SQLite `src/visio/db/` : tables `meetings`, `links`, `participant_sessions`, `jobs`, `artifacts`, mode WAL, clés étrangères actives, dates stockées en UTC (une date sans décalage est refusée à l'écriture), migration Alembic `0001`. Nouvelle commande : `uv run task db-upgrade`. Nouvelles dépendances : `sqlalchemy`, `alembic`, `tzdata` (nécessaire aux fuseaux sous Windows).
> **Tests automatiques** : 117 passés (85 de plus qu'à l'étape 2) sous Python 3.12 et 3.13 : tous les cas du créneau ci-dessus, changement d'heure du 28 mars 2027, contraintes de la base, cohérence modèles / migration, retour arrière de la migration.
> **À confirmer** : points 9, 10 et 11 de `DECISIONS.md` §6 ter.

---

## Étape 4 — API réunions (CRUD, authentification par clé, OpenAPI)

**Objectif** : le backend Eduvia peut créer et piloter des réunions.

**Tâches**
- Authentification par clé secrète (`Authorization: Bearer ...`), comparaison à temps constant, HTTPS exigé en production.
- Endpoints : `POST /meetings`, `GET /meetings/:id`, `PATCH /meetings/:id`, `POST /meetings/:id/end`, `DELETE /meetings/:id` (les autres plus tard).
- Réponse de création : `id`, `participant_url`, `organizer_urls[]`, `status`.
- `metadata` libre renvoyé tel quel.
- Erreurs JSON à codes explicites : `invalid_slot`, `meeting_already_started`, `meeting_not_found`, `unauthorized`…
- Génération automatique de la spec **OpenAPI** (`/openapi.json`) et page de doc.

**Tests**
- I : création valide → `201` avec les bons champs.
- I : sans clé / mauvaise clé → `401` (y compris sur `POST /meetings`).
- I : créneau incohérent → `422 invalid_slot`.
- I : `PATCH` après début → `409 meeting_already_started`.
- I : `DELETE` d'une réunion non commencée → `cancelled` ; d'une réunion commencée → `409`.
- I : `metadata` identique entre `POST` et `GET`.
- U : la spec OpenAPI générée est valide (validateur officiel).
- M : tu crées une réunion avec un client REST (VS Code REST Client / Postman) en suivant mon fichier `docs/exemples.http`.

**Porte de validation** : ☑ tu as créé une réunion toi-même · ☑ noms de champs et erreurs te conviennent

> **Validée par Walid le 2026-11-02.** Les points 12 à 20 de `DECISIONS.md` §6 quater sont acceptés tels que proposés. Les routes sont sous `/v1` (décision de l'étape 0) : `POST /v1/meetings`, etc.
>
> **Livré** : `src/visio/api/` (`meetings.py` routes, `schemas.py` formats, `auth.py` clé d'API, `errors.py` erreurs JSON, `deps.py`), `src/visio/services/meetings.py` (création, modification, annulation, clôture), `src/visio/services/links.py` (URL **provisoires**, voir ci-dessous), `lifecycle_status` dans le domaine, 4 nouvelles erreurs métier, `docs/exemples.http`. Nouvelle dépendance de développement : `openapi-spec-validator`.
> **Tests automatiques** : 221 passés (104 de plus qu'à l'étape 3) sous Python 3.12 et 3.13 : tous les cas ci-dessus (201, 401 y compris sur `POST`, 422 `invalid_slot`, 409 après début, `DELETE`, `metadata` identique, spec OpenAPI validée), plus clé tronquée ou rallongée, HTTPS en production, créneau de 12 h pile, champs inconnus et types stricts, `/end` dans la marge d'ouverture, rejeu de `DELETE` et `/end`. Essai réel avec dotenvx + curl : 401 sans clé, `201` à la création, `422 invalid_slot` pour une date sans décalage.
> **Résultats constatés sur la machine de Walid (Windows, Python 3.12.16) :**
> - `uv run task check` : lint OK, 49 fichiers formatés, mypy strict « no issues found in 45 source files », **221 tests passés**.
> - `uv run task db-upgrade` : « Base de données à jour. » ; `uv run task dev` démarre, `/health` répond 200.
> - Appels de `docs/exemples.http` (extension REST Client) : 1 → `201` avec `participant_url` et `organizer_urls` ; 2 → `201`, deux liens organisateurs distincts ; 3 → `200`, aucun lien renvoyé ; 4 → `200`, titre et créneau modifiés ; 5 → `409 meeting_not_started` (attendu avant l'ouverture) ; 6 → `200 cancelled`.
> - Erreurs : E2 `401 unauthorized`, E3 et E4 `422 invalid_slot`, E5 `422 validation_error` (« recordng : champ inconnu »), E6 `404 meeting_not_found`. E1 : `401` avec l'en-tête `www-authenticate: Bearer` ; le corps s'est affiché vide dans le copier-coller alors que sa taille (116 octets) est celle d'E2 : très probablement un défaut d'affichage, à rejouer si on veut l'écarter.
> - Tag Git à poser : `git tag etape-04-validee`.
>
> **Remarque :** l'avertissement Starlette sur `httpx2` est toujours présent, sans effet.
> **Limite connue** : `/end` ne ferme pas encore la salle LiveKit ni n'envoie de webhook (étapes 6, 10, 12).
>
> **Ta checklist manuelle (M)** :
> 1. `uv sync`, puis `uv run task check` : vert (221 tests).
> 2. `uv run task db-upgrade` puis `uv run task dev`.
> 3. Ouvre `http://localhost:8000/docs` : les 5 routes `/v1/meetings` y sont, avec le bouton « Authorize ».
> 4. `dotenvx get API_KEY -f .env.development`, colle la clé dans `docs/exemples.http`, ouvre-le dans VS Code (extension REST Client) et envoie les requêtes 1 à 6, puis les erreurs E1 à E6.
> 5. Vérifie : `201` avec `participant_url` et `organizer_urls`, les erreurs ont toutes la forme `{"error": {"code", "message"}}`, `GET` ne renvoie aucun lien.

---

## Étape 5 — Liens et jetons (organisateur, participant, nominatif)

**Objectif** : sécuriser « qui peut entrer, avec quel rôle ».

**Tâches**
- Jeton signé (JWT HS256 ou Ed25519) contenant : `meeting_id`, `role`, `display_name`, `external_id`, `jti`, expiration = fin du créneau.
- Lien participant générique + liens nominatifs (`POST /meetings/:id/links`).
- Un lien organisateur **par organisateur**, sans aucun lien mathématique avec le lien participant.
- Liste de révocation (`jti` exclus) pour l'exclusion définitive.
- Une URL de visio ne contient **que** le jeton opaque, jamais un id devinable.

**Tests**
- U : un jeton modifié d'un seul caractère est rejeté.
- U : un jeton participant dont le `role` est changé en `organizer` est rejeté (signature).
- U : on ne peut pas dériver un lien organisateur à partir d'un lien participant (entropie ≥ 128 bits, test statistique d'unicité sur 100 000 jetons).
- I : lien nominatif → `display_name` et `external_id` présents dans le jeton.
- I : deux organisateurs sur une même réunion → deux liens distincts.
- I : jeton d'une réunion A utilisé sur la réunion B → rejeté.
- **Scénario 2 (partiel)** : URL inventée ou modifiée → erreur, jamais une salle vide.

**Porte de validation** : ☐ tu valides le format de jeton · ☐ tu confirmes la durée de vie des jetons

> **Validée par Walid le 2026-11-02.** Les points 21 à 29 de `DECISIONS.md` §6 quinquies sont acceptés tels que proposés, dont le 22 (pas d'`exp` dans le jeton, écart assumé avec le plan) et le 27 (pas de route de révocation par API).
>
> **Livré** : `src/visio/services/links.py` (émission, vérification, révocation), `src/visio/api/links.py` (`POST /v1/meetings/{id}/links`), schémas `CreateLinkIn` / `LinkOut`, 4 erreurs métier (`invalid_link`, `participant_excluded`, `meeting_ended`, `too_many_organizers`), exemples ajoutés à `docs/exemples.http`. Aucune migration (les colonnes existaient). Nouvelle dépendance : `pyjwt`.
> **Écart avec le plan** : pas d'`exp` dans le jeton (point 22 de `DECISIONS.md` §6 quinquies). La durée de vie est le créneau, décidé côté serveur.
> **Tests automatiques** (dans mon environnement, Python 3.12) : `ruff` OK, `mypy` strict « no issues found in 47 source files », **285 tests passés** (64 de plus qu'à l'étape 4) :
> - U : un caractère modifié, n'importe où dans le jeton → refusé ; `role` changé en `organizer` → refusé par la signature ; `alg: none`, HS512, autre secret, claim manquant ou de mauvais type, texte quelconque → `invalid_link` au message identique ; unicité de 100 000 identifiants et de 100 000 jetons ; noms de 200 caractères (accents, émojis) → jeton sous le plafond.
> - I : lien nominatif → `display_name` et `external_id` dans le jeton et en base ; deux organisateurs → deux liens distincts qui fonctionnent ; jeton de la réunion A annonçant la réunion B → refusé ; jeton signé mais en désaccord avec la base (rôle, nom, lien inconnu) → refusé ; lien révoqué refusé sans toucher aux autres ; limite de 10 organisateurs ; réunion annulée ou terminée → 409 ; clé d'API absente → 401 sans rien créer ; spec OpenAPI toujours valide ; un déplacement de créneau (`PATCH`) ne casse pas les liens.
> - **Scénario 2 (partiel)** : URL inventée ou modifiée → `invalid_link`, au niveau du service. Le passage en HTTP (`POST /join`, « jamais une salle vide ») est à l'étape 6.
>
> **Ta checklist manuelle (M)** :
> 1. `uv sync` (installe `pyjwt`), puis `uv run task check` : vert (285 tests).
> 2. `uv run task dev`, puis dans `docs/exemples.http` (après avoir collé ta clé d'API) : requête 1 → note `participant_url` ; requête **4 bis** → `201` avec `url` ; **4 ter** → `201`.
> 3. Décode un jeton **en local** : `uv run python -c "import jwt,sys;print(jwt.decode(sys.argv[1],options={'verify_signature':False}))" <jeton>` (jamais sur un site en ligne). Vérifie `role`, `name`, `eid`, et qu'il n'y a pas d'`exp`.
> 4. Erreurs : **E7** → `422 validation_error`. Rejoue la requête 6 (annulation) puis **E8** → `409 meeting_cancelled`.
> 5. Relis les points 21 à 29 de `DECISIONS.md` §6 quinquies, surtout le 22 (pas d'expiration) et le 27 (révocation par API : oui ou non).
> 6. Tag Git : `git tag etape-05-validee`.
>
> **Résultats constatés sur la machine de Walid (Windows, Python 3.12.16) :**
> - `uv run task check` : lint OK, 51 fichiers formatés, mypy strict « no issues found in 47 source files », **285 tests passés** ; `env-check` : aucun secret en clair.
> - `POST /v1/meetings/{id}/links` : participant nominatif (Lina Benali, `stu_981`) → `201` ; organisateur sans `external_id` (Paul Martin) → `201`. Chaque lien est un jeton distinct.
> - Erreurs : E7 sans nom → `validation_error` ; sans clé → `unauthorized` ; réunion inconnue → `meeting_not_found` ; E8 réunion annulée → `meeting_cancelled`.
> - Note : `$l.url.Split("/j/")` ne marche pas en PowerShell 5.1 (découpe sur chaque caractère) ; utiliser `($l.url -split '/j/')[1]` pour décoder un jeton.

---

## Étape 6 — LiveKit local et endpoint « rejoindre »

**Objectif** : se connecter réellement à une salle, en respectant le créneau.

**Tâches**
- Télécharger le binaire **LiveKit Server pour Windows**, lancement en mode dev.
- Endpoint `POST /join` (reçoit le jeton du lien) : vérifie signature, réunion, créneau, révocation, puis émet un **jeton LiveKit** de courte durée avec les droits adaptés au rôle.
- Création de la room LiveKit à la volée, nom opaque.
- Les jetons LiveKit expirent à `ends_at`.
- Réponses distinctes : `not_open_yet` (avec heure d'ouverture), `ended`, `invalid_link`.

**Tests**
- I (horloge simulée) : **scénario 1** côté API : 15:55 refusé, 16:00 accepté, 18:01 refusé.
- I : lien invalide ou falsifié → `invalid_link`, aucune room créée.
- I : le jeton LiveKit émis a la bonne expiration et les bons droits (`canPublish`, pas d'admin pour un participant).
- M : deux onglets rejoignent la même salle avec le client de test LiveKit : audio/vidéo circulent.

**Porte de validation** : ☑ tu as vu deux onglets se parler · ☑ refus hors créneau constaté

> **Validée par Walid le 2026-11-03.** Les points 30 à 37 de `DECISIONS.md` §6 sexies sont acceptés tels que proposés, dont le 31 (jeton LiveKit de 10 minutes au plus, précision par rapport au plan).
>
> **Livré** : `src/visio/services/livekit_tokens.py` (droits par rôle, expiration, noms de salle et identités opaques), `src/visio/services/join.py`, `src/visio/api/join.py` (`POST /v1/join`), `src/visio/api/devtools.py` (page d'essai `/dev/livekit-test`, développement uniquement), `deploy/dev/livekit-dev.ps1`, 2 erreurs métier (`not_open_yet` avec `opens_at`, `display_name_required`), `require_https` partagé, exemples ajoutés à `docs/exemples.http`. Aucune migration, **aucune nouvelle dépendance** (pas de `uv sync` nécessaire).
> **Précision avec le plan** : le jeton LiveKit dure 10 minutes au plus (jamais après la fin de la réunion), au lieu de « expire à `ends_at` » : sinon un exclu pourrait revenir (point 31 de `DECISIONS.md` §6 sexies).
> **Tests automatiques** : 16 unitaires (`tests/unit/test_livekit_tokens.py`) et 25 d'intégration (`tests/integration/test_join.py`) : scénario 1 (15:55 refusé, 16:00 accepté, 17:59:59 accepté, 18:00 et 18:01 refusés, avec et sans marge), liens invalides sans aucun jeton émis, lien révoqué, réunion annulée ou close, droits et expiration du jeton LiveKit, salle opaque et commune à la réunion, identités, nom du lien générique, HTTPS en production, page d'essai absente hors développement. Tous verts chez Walid (voir les résultats constatés ci-dessous).
>
> **Ta checklist manuelle (M)** :
> 1. `uv run task format` puis `uv run task check` : vert. Si un test échoue, colle-moi la sortie.
> 2. Télécharge **LiveKit Server pour Windows** (`livekit_..._windows_amd64.zip`, dépôt `livekit/livekit`, page Releases), mets `livekit-server.exe` dans `deploy\dev\`.
> 3. Vérifie la config LiveKit du développement : `dotenvx get LIVEKIT_URL -f .env.development` doit donner `ws://localhost:7880` et `dotenvx get LIVEKIT_API_KEY -f .env.development` doit donner `devkey`. Le script de l'étape 4 lit la même clé et le même secret, donc LiveKit et le backend concordent.
> 4. Terminal 1 : `.\deploy\dev\livekit-dev.ps1` (autorise le pare-feu Windows si demandé). Terminal 2 : `uv run task db-upgrade` puis `uv run task dev`.
> 5. Crée une réunion **qui commence maintenant** avec `.\deploy\dev\nouvelle-reunion.ps1` : il affiche `participant_url` et `organizer_urls[0]`.
> 6. Ouvre `http://localhost:8000/dev/livekit-test` dans **deux onglets** : colle le lien organisateur dans l'un, le lien participant (avec un nom) dans l'autre. Autorise micro et caméra : tu dois voir et entendre l'autre onglet (mets le micro d'un onglet en sourdine ou utilise un casque pour éviter l'écho).
> 7. Refus hors créneau : crée une réunion dans le futur, ouvre son lien dans la page d'essai → `not_open_yet` avec `opens_at`. Une réunion passée (ou annulée) → `meeting_ended` / `meeting_cancelled`. Un lien modifié d'un caractère → `invalid_link`.
> 8. Relis les points 30 à 37 de `DECISIONS.md` §6 sexies, surtout le 31 (jeton de 10 minutes).
> 9. Tag Git après validation : `git tag etape-06-validee`.
>
> **Résultats constatés sur la machine de Walid (Windows, Python 3.12.16) :**
> - `uv run task format` : 1 fichier reformaté, 1 erreur ruff corrigée automatiquement. Puis `uv run task check` : lint OK, 57 fichiers formatés, mypy strict « no issues found in 53 source files », **326 tests passés** (41 de plus qu'à l'étape 5 : 16 unitaires + 25 d'intégration).
> - LiveKit Server **1.13.11** (binaire Windows) placé dans `deploy\dev\` (`livekit-server.exe --version` OK).
> - Premier lancement de `livekit-dev.ps1` refusé par PowerShell (« script non signé »). Réglé avec `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, puis `Unblock-File` sur `livekit-dev.ps1` et `nouvelle-reunion.ps1`.
> - `nouvelle-reunion.ps1` : réunion créée (`scheduled`), lien participant et lien organisateur (Marie Durand, `staff_42`) affichés.
> - Journal du serveur : `POST /v1/meetings` `201` ; `GET /dev/livekit-test` `200` ; `POST /v1/join` : trois `422` aux premiers essais, puis `200` à trois reprises (connexions réussies).
> - `GET /j/<jeton>` ouvert directement dans le navigateur : `404`. **Normal à ce stade** : la route `/j/` et l'interface sont l'objet de l'étape 7 ; pour l'instant seule la page d'essai échange un lien contre un jeton LiveKit.
> - Checklist manuelle (M) passée selon Walid : deux onglets dans la même salle, refus hors créneau.
>
> **Remarques :**
> - Même avertissement Starlette (`httpx2`) que précédemment, sans effet. Un avertissement PyJWT sur la longueur de clé HMAC apparaît dans le test qui essaie volontairement HS512 : sans effet.
> - Limites connues de l'étape (traitées plus tard) : `join` ne passe pas la réunion en `live` et n'enregistre pas de présence (étapes 11 et 12) ; la salle d'attente n'est pas encore appliquée (étape 9) ; un jeton LiveKit déjà émis reste utilisable jusqu'à 10 minutes après une annulation, une clôture ou une exclusion (étapes 8 et 10). L'interface (étape 7) devra rappeler `POST /v1/join` à chaque reconnexion.
> - Le client LiveKit de la page d'essai vient d'un CDN (jsDelivr) : acceptable en local, il sera **embarqué** dans l'interface de l'étape 7.
> - Tag Git à poser : `git tag etape-06-validee`.

---

## Étape 7 — Interface de visio (FR, marque blanche)

**Objectif** : l'application web que les humains utilisent.

**Tâches**
- App React/Vite servie sur le même domaine que l'API.
- Écrans : page d'attente (« pas encore ouverte », heure de Paris + compte à rebours), pré-jointure (test micro/caméra, nom saisi si lien générique, nom verrouillé si lien nominatif), salle, « réunion terminée », erreur « lien invalide ».
- Partage d'écran, chat (affichage), liste des participants, indicateur d'enregistrement (branché plus tard).
- Textes 100 % en français, fichier de traductions.
- Marque blanche : logo, couleurs, nom, depuis la configuration (variables CSS), aucune mention de LiveKit.
- Adapté mobile (navigateur).

**Tests**
- E2E (Playwright) : lien valide → entrée en salle ; lien invalide → page d'erreur.
- E2E : lien nominatif → champ nom verrouillé et pré-rempli ; lien générique → champ nom obligatoire.
- E2E : avant le créneau → page d'attente avec l'heure d'ouverture en heure de Paris.
- E2E : après le créneau → « réunion terminée ».
- M : tu changes logo et couleur dans la config, tu rebuildes, tu vois le résultat ; aucune mention « LiveKit » nulle part (inspection du DOM).
- M : test sur ton téléphone (Chrome Android / Safari iOS) via le réseau local.

**Porte de validation** : ☑ tu valides l'aspect et les textes · ☑ test mobile OK (pages ; voir la limite ci-dessous pour la salle complète)

> **Validée par Walid le 2026-11-03.** Les points 38 à 47 de `DECISIONS.md` §6 septies sont acceptés tels que proposés.
>
> **Livré** : application React/Vite dans `web/` (écrans : attente avec compte à rebours en heure de Paris, pré-jointure avec test micro/caméra, salle avec vidéos, partage d'écran, discussion et participants, « réunion terminée », « réunion annulée », « lien invalide », « connecté ailleurs », « reconnexion impossible »), textes français dans `web/src/fr.ts`, marque blanche par `BRAND_*`, client LiveKit **embarqué** dans le bundle (plus de CDN). Côté serveur : `src/visio/api/web.py` (`GET /j/<jeton>`, `GET /assets/...`) et `POST /v1/join/preview` (aperçu d'un lien sans jeton LiveKit). Nouvelles commandes : `web-install`, `web-build`, `web-check`, `web-dev`, `dev-lan`, `e2e`.
> **Tests automatiques** : 353 passés dans `uv run task check` (27 de plus qu'à l'étape 6 : 8 d'intégration sur l'aperçu, 19 sur la page, la marque et les ressources), plus **14 tests E2E** navigateur passés avec `uv run task e2e` (deux personnes dans la même salle, aucune mention de la brique dans l'interface, liens inventés ou modifiés, lien exclu, nom verrouillé / obligatoire, page d'attente en heure de Paris et ouverture automatique, réunion terminée, réunion annulée, marque venue de la configuration, pages et contrôles adaptés à un écran de téléphone).
>
> **Résultats constatés sur la machine de Walid (Windows, Python 3.12.16) :**
> - `uv sync` : à jour (62 paquets). `uv run task web-install` : 38 paquets, 0 vulnérabilité. `uv run task web-build` : `tsc --noEmit` puis Vite OK (32 modules, `index.js` 782 kB / 217 kB gzip, `index.css` 10 kB).
> - `uv run task check` : lint OK, 63 fichiers formatés, mypy strict « no issues found in 59 source files », **353 tests passés** (14 E2E exclus), 2 avertissements connus et sans effet (Starlette / `httpx2`, PyJWT / clé HMAC courte dans le test qui essaie volontairement HS512).
> - `uv run task e2e` : **14 passés** en 26 s.
> - `uv run playwright install chromium` : échec du téléchargement (délai dépassé, réseau). **Sans conséquence** : les E2E utilisent Microsoft Edge ou Chrome déjà installés (`tests/e2e/conftest.py`), rien à télécharger.
> - Checklist manuelle (M) passée selon Walid : parcours complet, liens invalides, créneau avant / pendant / après, marque blanche et inspection du DOM (aucune mention de LiveKit).
>
> **Remarques et limites connues (traitées plus tard) :**
> - **Mobile** : le navigateur n'autorise micro et caméra que sur HTTPS. Avec `dev-lan` (HTTP), seules les pages se testent sur téléphone ; la **salle complète sur mobile** se vérifie avec de vrais certificats aux étapes 18 et 19 (TLS), où elle est à reprendre.
> - La salle d'attente n'est pas encore appliquée (étape 9) : le texte « L'organisateur devra vous admettre » est prêt mais masqué.
> - Pas encore de boutons de modération (étape 8), d'avertissement avant la fin ni de coupure à `ends_at` (étape 10), de présence ni de chat conservés côté serveur (étape 11).
> - L'indicateur « Enregistrement en cours » et l'annonce de transcription sont **affichés**, mais rien n'est encore enregistré ni transcrit (étapes 14 et 16).
> - Le jeton du lien est dans l'URL : la page n'est jamais mise en cache et n'envoie aucun `Referer`.
> - Tag Git à poser : `git tag etape-07-validee`.

---

## Étape 8 — Rôles et modération

**Objectif** : droits de l'organisateur appliqués **côté serveur**.

**Tâches**
- Actions organisateur : couper le micro, couper la caméra / le partage d'un participant, exclure, terminer la réunion.
- Autorisation vérifiée côté serveur (jamais seulement côté interface) via les permissions LiveKit et un endpoint dédié.
- Exclusion définitive : `jti` ajouté à la liste de révocation + déconnexion immédiate ; toute reconnexion avec le même lien est refusée.
- Gestion de plusieurs organisateurs.

**Tests**
- I : un participant qui appelle l'endpoint de modération → `403`.
- I : un organisateur coupe un micro → l'état de la piste change.
- I : exclusion → `POST /join` avec le même lien renvoie `participant_excluded`.
- E2E : **scénario 3** complet avec 3 navigateurs (organisateur, 2 participants).
- Sécurité : un participant qui modifie son jeton pour devenir organisateur échoue.
- M : tu joues l'organisateur et tu modères pour de vrai.

**Porte de validation** : ☑ scénario 3 validé · ☑ co-animateur testé

> **Réalisé et validé le 2026-11-04 :** `POST /v1/room/mute|remove|end` réservés aux organisateurs (403 `organizer_required` sinon, jeton falsifié → refus), plusieurs organisateurs possibles, exclusion définitive (entrée `excluded` + déconnexion + reconnexion refusée), boutons dans le panneau « Participants ». Les droits `roomAdmin` ont été **retirés** des jetons : toute modération passe par le backend. Voir `docs/DECISIONS.md` §6 octies. Tests : `test_moderation.py`, E2E scénario 3 (3 navigateurs + co-animateur).

---

## Étape 9 — Salle d'attente

**Objectif** : admission contrôlée quand `waiting_room: true`.

**Tâches**
- Participants en attente : connectés à un état « lobby » sans accès aux flux ni au chat.
- L'organisateur voit la liste (nom) avec « admettre », « refuser », « tout admettre ».
- Tant qu'aucun organisateur n'est présent, tout le monde attend.
- Un participant refusé ne peut pas rentrer sans nouvelle décision (règle à confirmer).
- Mode ouvert par défaut : entrée directe.

**Tests**
- I : un participant en attente ne reçoit **aucun** droit de lecture de flux (jeton LiveKit sans `canSubscribe`).
- E2E : **scénario 4** : avec salle d'attente, entrée seulement après admission ; sans, entrée directe.
- E2E : « tout admettre » fait entrer N participants d'un clic.
- E2E : sans organisateur présent, personne n'entre.
- M : tu valides l'ergonomie de la liste d'attente.

**Porte de validation** : ☑ scénario 4 validé · ☑ comportement « refusé » confirmé

> **Réalisé et validé le 2026-11-04 :** table `access_entries` (en attente / admis / refusé / exclu) ; un participant en attente **ne reçoit aucun jeton** (pas seulement un jeton sans droits) et ne rejoint le serveur de médias qu'une fois admis ; liste, admettre, refuser, « tout admettre » ; tant qu'aucun organisateur n'est présent, personne n'entre ; refusé = refusé tant qu'un organisateur ne le réadmet pas ; sans salle d'attente, entrée directe. Tests : `test_lobby.py`, E2E scénario 4.

---

## Étape 10 — Fin de créneau, clôture, annulation, modification

**Objectif** : le créneau est strictement respecté dans le temps.

**Tâches**
- Planificateur (scheduler) : à `ends_at`, ferme la room, déconnecte tout le monde, passe l'état à `ended`, déclenche la génération des artefacts.
- Avertissement visible quelques minutes avant la fin (ex. 5 min et 1 min).
- `POST /meetings/:id/end` (clôture immédiate), `PATCH` (créneau/options avant début), `DELETE` (annulation).
- Résilience : si le service redémarre, les fins planifiées sont rattrapées.

**Tests**
- I (horloge simulée) : à `ends_at`, la room est fermée et l'état est `ended`.
- I : `PATCH` du créneau reprogramme bien la coupure.
- I : redémarrage simulé avant `ends_at` → la coupure a quand même lieu ; redémarrage après → réunion déjà terminée détectée.
- E2E : avertissement affiché à T-5 min (horloge accélérée en test).
- M : tu crées une réunion de 3 minutes et tu vérifies avertissement + coupure réels.
- **Scénario 1 complet** (en réel, avec un créneau court de test).

**Porte de validation** : ☑ scénario 1 validé en réel · ☑ messages d'avertissement validés

> **Réalisé et validé le 2026-11-04 :** planificateur sans état (relit la base chaque seconde) : coupure à `ends_at`, fermeture de la salle avec relances, rattrapage après redémarrage, avertissement visible dans la salle à moins de 5 minutes, annulation et modification (`PATCH`) qui déplacent la coupure. Chemin unique de fin : `terminate_meeting`. Tests : `test_lifecycle.py`, E2E de coupure à l'heure.

---

## Étape 11 — Présence et chat

**Objectif** : données d'émargement et historique fiables.

**Tâches**
- Réception des webhooks LiveKit (signature vérifiée) : `participant_joined/left`.
- Table de présence : nom, `external_id`, rôle, entrée, sortie, avec gestion des reconnexions multiples.
- Temps de présence total calculé (somme des intervalles, sans double comptage).
- Chat : messages via data channel, **persistés côté serveur** (auteur, horodatage, texte).
- Endpoints `GET /meetings/:id/attendance` et `GET /meetings/:id/chat`.
- Clôture des sessions ouvertes à la fin de la réunion.

**Tests**
- U : calcul de durée avec reconnexions, chevauchements, sortie manquante.
- I : un participant qui entre, sort, revient → 2 intervalles, durée = somme.
- I : à `ends_at`, tous les intervalles ouverts sont clos à l'heure de fin.
- I : le chat exporté contient auteur, horodatage, message, dans l'ordre.
- E2E : **scénario 7** : séance scriptée, puis comparaison export / séance.
- M : tu rejoins, quittes, reviens ; tu lis l'export.

**Porte de validation** : ☑ scénario 7 validé · ☑ règle de calcul de présence validée (émargement)

> **Réalisé et validé le 2026-11-04 :** présence alimentée par les webhooks du serveur de médias (`POST /v1/livekit/webhook`, signé), intervalles fusionnés (règle d'émargement : union des intervalles, une personne = une ligne), `GET /v1/meetings/{id}/attendance` ; chat **conservé côté serveur** (envoi par `POST /v1/room/chat`, export `GET /v1/meetings/{id}/chat`). Tests : `test_presence_chat.py`, `test_presence.py`, E2E scénarios 5 et 7.

---

## Étape 12 — Webhooks sortants signés

**Objectif** : Eduvia est prévenu sans interroger l'API.

**Tâches**
- Événements : `meeting.started`, `meeting.ended`, `participant.joined`, `participant.left` (les autres en Phase B).
- Signature HMAC SHA-256 (en-têtes `X-Visio-Signature`, `X-Visio-Timestamp`), `metadata` inclus.
- File d'envoi persistante avec relances à délai croissant (ex. 10 s, 1 min, 5 min, 30 min, 2 h), journal des tentatives.
- Identifiant d'événement unique (idempotence côté Eduvia).
- Petit récepteur de test (script) + doc de vérification de signature.

**Tests**
- U : signature correcte acceptée, corps modifié rejeté, timestamp trop ancien rejeté.
- I : échec du récepteur → relances aux bons délais, puis abandon marqué.
- I : redémarrage pendant les relances → reprise.
- I : chaque appel contient les `metadata`.
- M : tu lances le récepteur de test et tu vois arriver les événements d'une vraie réunion.

**Porte de validation** : ☑ format des payloads validé · ☑ **fin de la Phase A** : démo complète sans Docker

> **Réalisé et validé le 2026-11-04 :** boîte d'envoi persistante, signature `X-Visio-Signature: v1=` (HMAC-SHA256 de `<timestamp>.<corps>`), idempotence par id d'événement, 6 tentatives avec relances jusqu'à 2 h. Format et exemples de vérification : `docs/WEBHOOKS.md`. Récepteur de test : `deploy/dev/webhook-receiver.py`. Tests : `test_webhooks_out.py`, `test_webhook_signature.py`, E2E (événements reçus, signatures valides, ordre cohérent).

---

# PHASE B — Enregistrement, transcription, production (avec Docker)

---

## Étape 13 — Docker Desktop : LiveKit + Redis + S3 local + Egress

**Objectif** : retrouver en local un environnement proche de la production.

**Tâches**
- Vérifier Docker Desktop (WSL2 activé).
- `deploy/docker-compose.dev.yml` : LiveKit server, Redis, MinIO (S3 de dev), LiveKit Egress.
- Basculer l'API sur ce LiveKit (même code, config différente).
- Script de reset de l'environnement de dev.

**Tests**
- M : `docker compose up` démarre tous les services sains.
- I : les étapes 6 à 11 passent toujours contre ce LiveKit (non-régression).
- I : écriture / lecture / suppression d'un objet test dans MinIO depuis l'API.

**Porte de validation** : ☑ environnement Docker stable · ☑ non-régression verte

**Découpage en trois sous-étapes** (validées une à une) :

| Sous-étape | Contenu | Statut |
|---|---|---|
| 13a | LiveKit + Redis dans Docker, script `docker-dev.ps1`, webhook LiveKit → backend | ✅ validée le 2026-11-04 |
| 13b | S3 local (SeaweedFS, à la place de MinIO), couche `services/storage.py`, `s3-init` / `s3-check` | ✅ validée le 2026-11-04 |
| 13c | Egress (enregistrement) dans le compose, configuration générée, `egress-check` | ✅ validée le 2026-11-04 |
| 13 (fin) | Non-régression des étapes 6 à 11 contre LiveKit Docker, `docker compose up` sain | ✅ validée le 2026-11-04 |

> **13b réalisée et validée le 2026-11-04 :** SeaweedFS 4.06 remplace MinIO (retiré de Docker Hub, dépôt archivé ; écart expliqué en `DECISIONS.md` point 68). `services/storage.py` est l'unique point d'accès au S3 (`put`, `get`, `exists`, `delete`, `bucket_exists`, `ensure_bucket`), `uv run task s3-init` crée le bucket de dev, `uv run task s3-check` écrit / relit / supprime un objet et vérifie que le bucket est privé. Tests : `test_storage.py` (19, contre un S3 simulé).

> **13c réalisée et validée le 2026-11-04 :** service `egress` (image `livekit/egress`) dans `deploy/docker-compose.dev.yml`, configuration `egress.yaml` générée par `docker-dev.ps1 up` (mêmes clés que l'API, jamais en clair dans le dépôt), passerelle `services/egress.py`, et `uv run task egress-check` : enregistre 5 s d'une page web, attend la fin, vérifie que le **MP4 est arrivé sur le S3** puis le supprime. Tests : `test_egress.py` (16, contre un faux Egress). Constaté en réel : les 4 services `healthy`, `egress-check` « Egress OK » (MP4 de 21 Ko sur le S3, puis supprimé).

> **Étape 13 validée le 2026-11-04 :** Docker de dev complet (LiveKit 1.13.11, Redis, SeaweedFS 4.06, Egress 1.15.0 figée). Non-régression : `uv run task e2e-docker`, 25 E2E verts en 1 min 59 s contre LiveKit Docker ; 488 tests unitaires et d'intégration verts.

---

## Étape 14 — Enregistrement vers S3

**Objectif** : enregistrer automatiquement chaque réunion avec `recording: true`.

**Tâches**
- Démarrage de l'enregistrement composite (audio + vidéo + partage d'écran, MP4) dès le début de la réunion.
- Arrêt à la fin ; fichier envoyé au S3 (endpoint configurable), chemin opaque `meetings/<id>/recordings/<uuid>.mp4`.
- **Indicateur d'enregistrement** visible dans la visio.
- Réunions de plusieurs heures : découpage éventuel en segments (à décider selon les tests).
- Webhooks `recording.ready` / `recording.failed`.

**Tests**
- E2E : réunion de 2 min avec partage d'écran → MP4 présent sur S3.
- M : tu télécharges le MP4 et tu vérifies : audio de chaque participant, vidéo, **partage d'écran visible**.
- I : `recording: false` → aucun enregistrement, aucun indicateur.
- I : `recording.ready` signé reçu avec les `metadata`.
- I : arrêt forcé de l'egress → `recording.failed` avec motif.
- **Scénario 6 (partie enregistrement)**.
- Test long : réunion de 60 min, mesure de la taille (donnée pour le dimensionnement).

**Porte de validation** : ☐ qualité du MP4 validée · ☐ taille par heure notée

**Découpage en quatre sous-étapes** (validées une à une, comme l'étape 13) :

> Pourquoi découper : l'étape touche quatre couches (passerelle Egress, base et migration, machine à états + webhooks, interface et E2E), sa validation réelle passe par Docker (non rejouable hors d'un poste équipé de Docker) et elle contient un test de 60 min. Chaque sous-étape reste petite, testable et validable seule.

| Sous-étape | Contenu | Tests | Statut |
|---|---|---|---|
| 14a | **Démarrer et arrêter l'enregistrement.** Passerelle `start_room_composite` (MP4 composite : audio, vidéo, partage d'écran). Migration `0003` (`egress_id` sur `artifacts`). Tâche `start_recording` créée à `meeting.started` (relances, reprise après redémarrage). Arrêt dans `terminate_meeting`, **avant** `close_room`. Chemin opaque `meetings/<id>/recordings/<uuid>.mp4`. `recording: false` → rien. | I (faux Egress) : un seul démarrage même si le webhook est rejoué ; aucun démarrage si `recording: false` ; arrêt avant fermeture de salle ; relance si Egress indisponible ; l'enregistreur n'apparaît pas dans la présence. M : une réunion réelle produit un fichier sur le S3 (outil `recordings-list`). | ✅ validée 2026-11-04 |
| 14b | **Fin, état et webhooks.** Réception de `egress_ended` sur le webhook LiveKit existant ; filet de sécurité : le planificateur interroge `ListEgress` pour les artefacts `pending` (webhook perdu, redémarrage). Artefact `ready` (taille lue sur le S3) ou `failed` (motif). Webhooks sortants `recording.ready` / `recording.failed` (+ `docs/WEBHOOKS.md`). | I : `recording.ready` signé reçu avec les `metadata` ; arrêt forcé d'Egress → `recording.failed` avec motif ; webhook LiveKit perdu → rattrapé par le planificateur ; redémarrage de l'API pendant l'enregistrement sans perte. | ✅ validée 2026-11-05 |
| 14c | **Indicateur et recette navigateur.** L'indicateur vient de LiveKit (`room.isRecording`) : vérifier qu'il n'apparaît que si l'enregistrement tourne réellement, jamais avec `recording: false` ; avis préalable déjà présent. E2E avec partage d'écran. | I : `recording: false` → ni enregistrement ni indicateur. E2E : réunion de 2 min avec partage d'écran → MP4 sur le S3. M : tu télécharges le MP4 (audio de chacun, vidéo, **partage d'écran visible**). **Scénario 6 (partie enregistrement)**. | ✅ validée 2026-11-05 |
| 14d | **Réunions longues et mesures.** Test de 60 min : taille par heure, CPU / RAM d'Egress. Décision sur le découpage en segments (réunions jusqu'à 8 h). Reprise après redémarrage d'Egress et de l'API pendant un long enregistrement. Chiffres versés dans `DECISIONS.md` (dimensionnement, étape 20). | Test long (60 min) ; M : relecture du MP4 de 60 min. | ✅ validée 2026-11-05 |

> **14c codée le 2026-11-05, à valider :** indicateur prouvé par E2E (jamais sans enregistrement réel, jamais avec `recording: false`), scénario 6 (partie enregistrement) automatisé avec Egress dans `uv run task e2e-docker`, mode `-Mode Recette` du script de test pour la recette à l'œil, et **défaut corrigé** : le planificateur tenait le verrou d'écriture de la base pendant les appels à Egress (voir `DECISIONS.md` §6 decies, points 105 à 110, checklist 14c).

**Porte de validation de l'étape 14** : ✅ qualité du MP4 validée (14c) · ✅ taille par heure notée (14d) · ✅ choix « un fichier ou des segments » tranché (14d : segments HLS fusionnés sans réencodage, `RECORDING_SEGMENT_SECONDS`)

> **À faire à l'occasion :** les chiffres du test de 60 min (taille par heure, CPU, RAM) sont encore des `____` dans `DECISIONS.md` (section « 14d »). Ils servent au dimensionnement (étape 20) : les y noter.

---

## Étape 15 — API artefacts : URL temporaires et suppression

**Objectif** : récupération et suppression sûres.

**Tâches**
- `GET /meetings/:id/recordings` : liste + URL signées **15 min maximum**.
- `DELETE /recordings/:id` : suppression de l'objet S3 **et** des métadonnées.
- Pour qu'une URL déjà émise ne marche plus après suppression : téléchargement via **proxy de l'API** (URL courte vers l'API qui vérifie l'existence en base puis streame depuis le S3), plutôt que des URL S3 présignées directes.
- Même mécanisme pour la transcription.

**Tests**
- I : URL valide → téléchargement OK ; après 15 min → refus.
- I : **scénario 8** : URL émise, suppression par API, puis ré-utilisation de l'URL → `404/410`.
- I : l'objet n'existe plus dans le S3 (vérifié directement).
- I : bucket privé : l'URL S3 brute est inaccessible sans signature.
- I : suppression d'un artefact inexistant → `404` propre.

**Porte de validation** : ✅ scénario 8 validé · ✅ choix « URL via proxy » validé

> **15 codée et validée le 2026-11-05 :** `GET /v1/meetings/{id}/recordings` (liste + URL de 15 min), `GET` et `DELETE /v1/recordings/{id}`, `GET /v1/downloads/{jeton}` (proxy de l'API : `Range`, `HEAD`, aucune clé d'API, jeton signé), suppression qui purge le fichier, le manifeste et les segments (et les versions d'un bucket versionné) avant la base. Mécanisme générique (`services/artifacts.py`) que l'étape 16 réutilise pour la transcription. Voir `DECISIONS.md` §6 undecies, points 111 à 126, et la checklist 15. Outil de vérification réelle : `uv run task artifacts-check [--delete]`. Tests : `test_artifacts.py`, `test_artifacts_s3.py`, `test_download_tokens.py`, `test_byte_range.py`. Aucune migration.

---

## Étape 16 — Transcription auto-hébergée

**Objectif** : transcription française, attribuée au bon locuteur, sans service externe.

**Tâches**
- Enregistrement **audio par participant** (track egress) pour attribuer le locuteur avec certitude (nom + `external_id`).
- Worker (faster-whisper, même base de code Python) : traitement après la réunion, segments horodatés par participant, fusion chronologique.
- Sorties : JSON par segments (début, fin, locuteur, texte), texte brut, SRT, VTT ; stockage S3.
- Webhooks `transcript.ready` / `transcript.failed`.
- Mesures : temps de traitement pour 1 h, qualité (WER sur un échantillon français de référence), besoins CPU/GPU.
- `DELETE /meetings/:id/transcript`.
- Aucun appel réseau sortant du worker (vérifié).

**Tests**
- U : fusion des segments de plusieurs locuteurs dans l'ordre chronologique.
- U : génération SRT/VTT valide (parseur de référence).
- I : **scénario 5** : le nom du lien nominatif apparaît dans la transcription.
- I : réunion sans parole → transcription vide propre, pas d'erreur.
- I : worker coupé en plein traitement → reprise sans doublon.
- M : tu lis la transcription d'une vraie réunion française et tu juges la qualité.
- Sécurité : blocage réseau du conteneur worker → la transcription fonctionne toujours.

**Découpage en quatre sous-étapes** (validées une à une, comme les étapes 13 et 14) :

> Pourquoi découper : l'étape mêle de la logique pure (formats), du stockage, de l'enregistrement de pistes audio côté Egress (qui exige Docker), un moteur d'IA lourd et des mesures. Chaque sous-étape reste petite, testable et validable seule ; les deux premières se testent sans Docker ni modèle.

| Sous-étape | Contenu | Tests | Statut |
|---|---|---|---|
| 16a | **Format, stockage et API (sans moteur).** Fusion chronologique des segments de plusieurs locuteurs, sorties JSON / texte / SRT / VTT (`domain/transcript.py`). Stockage S3 en quatre fichiers au même nom opaque (`meetings/<id>/transcripts/<uuid>.{json,txt,srt,vtt}`), cycle `begin` → `complete` / `fail` idempotent (`services/transcripts.py`). `GET /v1/meetings/{id}/transcript` (`?format=json\|txt\|srt\|vtt`) et `DELETE`, qui réutilise la purge de l'étape 15. | U : fusion dans l'ordre, indépendante de l'ordre de traitement ; SRT/VTT valides (parseurs stricts) ; échappement. I : réunion sans parole → transcription vide propre ; états `pending`/`failed` ; suppression des quatre fichiers, S3 en panne, `409` si en cours ; relance sans doublon. | ✅ validée 2026-11-05 |
| 16b | **Pistes audio par participant.** Egress « track » : un fichier audio par micro publié (webhook `track_published`) sur le S3 (`meetings/<id>/audio/<uuid>.ogg`, dossier de travail), avec le nom et l'`external_id` **du lien**. Table `audio_tracks` (migration `0004`), tâche `start_track` avec relances, règlement par `egress_ended` + filet du planificateur, arrêt avant la fermeture de la salle, `purge` après transcription. Seuls les micros sont transcrits, jamais le partage d'écran. Outil `uv run task tracks-list`. | I (faux Egress) : une piste par participant, aucune si `transcription: false`, nom et `external_id` du lien, rien pour caméra/partage/personne non admise, webhook rejoué sans doublon, Egress indisponible → relances puis échec de la piste seule, webhook perdu → rattrapé, arrêt avant fermeture de salle, décalages. M : une vraie réunion à deux produit deux pistes (Docker : `nouvelle-reunion.ps1 -Transcription`, puis `uv run task tracks-list`). | ✅ validée 2026-11-05 |
| 16c | **Worker faster-whisper.** Tâche `transcribe` créée à la fin de la réunion, lit les pistes, segments par participant, fusion, écriture via 16a ; webhooks `transcript.ready` / `transcript.failed` ; reprise après coupure ; **aucun appel réseau sortant** (modèle local). Moteur dans `services/whisper_engine.py` (chargé avec `local_files_only`), tâche et fusion dans `services/transcription.py`, processus à part `uv run task transcriber`, contrôle hors ligne `uv run task transcriber-check`. | I (faux moteur) : **scénario 5** (le nom du lien nominatif est dans la transcription), reprise sans doublon, échec → `transcript.failed`, attente des pistes, nettoyage rejouable. Sécurité : aucun appel réseau pendant le traitement (sockets coupés). M : `transcriber-check` hors ligne sur ta machine. | ✅ validée 2026-11-06 (modèle `large-v3` retenu, choix définitif en fin de projet) |
| 16d | **Mesures et qualité.** Temps de traitement pour 1 h, WER sur un échantillon français de référence, CPU/GPU recommandé ; chiffres versés dans `DECISIONS.md` (étape 20). **Scénarios 5 et 6 complets.** | M : tu lis la transcription d'une vraie réunion française et tu juges la qualité. | ✅ validée fonctionnellement 2026-11-06 ; qualité insuffisante, point ouvert (DECISIONS 160-166) |

**Porte de validation de l'étape 16** (posée à la fin de 16d) : ✅ qualité jugée suffisante **(2026-11-08 : lecture du texte d'une réunion réelle, `DECISIONS.md` 245-246 ; contre-essai à deux personnes à faire en recette)** · ✅ matériel recommandé noté (GPU en production ; CPU/RAM de la machine de test à compléter) · ✅ **scénarios 5 et 6** (réserves : lien nominatif et webhook `transcript.ready` non observés en réel, couverts par tests automatiques)

---

## Étape 17 — Robustesse et observabilité

**Tâches**
- Logs JSON structurés, corrélation par `meeting_id`.
- Route `/health` (API) enrichie : base, LiveKit, S3, file de jobs ; `/health` sans détails sensibles.
- Écran / commande CLI pour lister et **rejouer** les jobs en échec (enregistrement, transcription, webhook).
- Reprise après redémarrage de tous les services.

**Tests**
- I : redémarrage de l'API pendant une réunion → réunion maintenue, coupure à l'heure.
- I : redémarrage après fin d'enregistrement → fichier conservé et référencé.
- I : job en échec → visible → rejouable → succès.
- M : tu coupes un conteneur au hasard pendant une démo et tu observes la reprise.

**Découpage en quatre sous-étapes** (validées une à une) :

| Sous-étape | Contenu | Tests | Statut |
|---|---|---|---|
| 17a | **Logs structurés.** `logging_setup.py` (JSON en production, texte en dev, `LOG_LEVEL` / `LOG_FORMAT`), middleware `X-Request-ID` + journal d'accès par route générique (jamais de jeton), `meeting_id` partout, journal d'accès d'uvicorn coupé, `httpx` en `WARNING`. | U : JSON valide, corrélation, secrets masqués, jamais d'exception. I : id de requête repris/remplacé, aucun jeton ni clé d'API dans les logs, URL inventée, `/health` en `DEBUG`, `5xx` en `ERROR`. M : lancer l'API en `LOG_FORMAT=json` et lire les lignes. | ✅ validée 2026-11-06 |
| 17b | **`/health` enrichi.** `GET /health` public minimal (vivant ou non, aucune dépendance sondée) ; `GET /v1/health` derrière la clé d'API : base, LiveKit, S3 (sondes parallèles, 3 s au plus chacune) et file de tâches (tâches et webhooks en attente / en échec / abandonnés, âge de la plus ancienne tâche due, retard par type de tâche), sans valeur sensible. Statut `ok` (200), `degraded` ou `down` (503). | I : chaque dépendance coupée → statut dégradé et code 503, base coupée → `down`, sonde bloquée → coupée à 3 s, rien de sensible dans les réponses ni les logs, file en souffrance. M : couper le S3 / LiveKit pour de vrai et lire `/v1/health`. | ✅ validée 2026-11-06 |
| 17c | **Jobs en échec visibles et rejouables.** Commandes `uv run task jobs-list` (lecture seule) et `jobs-retry <id>` / `--all` (tâches `failed` / `abandoned`, webhooks abandonnés ou en relance, artefacts et pistes audio en échec), remise à zéro des tentatives ; logique dans `services/replay.py`. Artefact → rejoue sa tâche ; échec définitif (le direct ne se rejoue pas) → refusé avec le motif ; `meeting_id` dans les logs d'abandon (limite 174). | I : job en échec → visible → rejoué → succès (démarrage, fusion, webhook), rejeu sans doublon (déjà en attente / déjà terminé / déjà livré), échec définitif refusé, démarrage refusé si la réunion est finie, `--all` limité à l'abandonné, rien de sensible dans l'affichage, commandes et codes de retour. M **automatisée** : `.\deploy\dev\test-rejeu.ps1` (vraie panne LiveKit, liste, rejeu, succès, webhook, échec définitif, fuites ; rien à taper). | ✅ validée 2026-11-07 |
| 17d | **Reprise après redémarrage.** Tests de redémarrage de l'API (réunion en cours, fin d'enregistrement, tâches interrompues) et scénario de démo « coupe un conteneur ». | I : trois scénarios du plan (`test_restart.py`, 5 tests). M **automatisée** : `.\deploy\dev\test-reprise.ps1` (coupe `livekit` / `s3` pour de vrai, lit `/v1/health`). | ✅ validée 2026-11-07 |

**Porte de validation** : ✅ résilience constatée en démo (`test-reprise.ps1` : 11 OK)

---

## Étape 18 — Réseau : TURN, pare-feu, TLS 443

**Tâches**
- TURN intégré de LiveKit en TCP/TLS sur 443 (avec certificat), UDP en plus quand possible.
- Documentation des ports à ouvrir et des alternatives.
- Test derrière un pare-feu restrictif.

**Tests**
- M : connexion depuis un réseau n'autorisant que 443 sortant (partage de connexion filtré / VM avec règles).
- I : le client bascule bien en relais TURN (statistiques de connexion).
- Sécurité : scan de ports, seuls les ports prévus sont ouverts.

**Porte de validation** : ☐ visio fonctionnelle en 443 seul (**reportée à l'étape 19**, serveur réel : le relais ne peut pas être validé de bout en bout sous Docker Desktop, voir `DECISIONS.md` points 217 à 221) · ✅ TURN configuré, relais alloué en local, aucune régression

---

## Étape 19 — Docker Compose complet et déploiement Coolify

**Tâches**
- `deploy/docker-compose.yml` de production : API, front, LiveKit, Egress, Redis, worker de transcription ; volumes persistants (SQLite).
- Une seule variable injectée : `DOTENV_PRIVATE_KEY_PRODUCTION`.
- Images construites, documentées, sans secret.
- Guide d'installation pas à pas (`docs/INSTALL.md`) et déploiement sur Coolify, domaine `visio.eduvia.app`.
- Sauvegarde / restauration de la base.

**Tests**
- M : déploiement à blanc depuis zéro en suivant **uniquement** le guide.
- Sécurité : `docker history` + inspection des images → aucun secret en clair.
- I : rejouer les étapes 6 à 16 en production (smoke test automatisé `uv run task smoke-prod`).
- I : sauvegarde puis restauration → données identiques.

**Porte de validation** : ☐ déploiement reproductible (**reporté à la recette finale** : pas de serveur ; à rejouer avec les réserves des étapes 16 et 18) · ☐ smoke test vert en production (**idem**) · ✅ **faits en local le 2026-11-07** : `uv run task check` vert (921 tests, mypy corrigé), `docker compose config` valide, les deux images construites, `docker history` / variables / fichiers sans aucun secret (`check-images.ps1`), `config-init` exécuté dans l'image avec la seule clé `DOTENV_PRIVATE_KEY_PRODUCTION`, génération des configs LiveKit et Egress en local ; la config de production refuse bien les valeurs `CHANGE_ME`

---

## Étape 20 — Tests de charge et dimensionnement

**Tâches**
- Simuler **10 réunions × 30 participants** (≈ 300 connexions), dont quelques-unes avec enregistrement et partage d'écran.
- Mesurer CPU, RAM, bande passante, I/O disque, latence, perte de paquets.
- Mesurer la charge de l'egress et de la transcription simultanées.
- Recommandation serveurs (CPU, RAM, GPU, bande passante) + stockage par heure enregistrée.

**Tests**
- C : **scénario 9** : charge nominale tenue sur ≥ 30 min, 0 déconnexion anormale.
- C : charge × 1,5 pour mesurer la marge.
- C : une journée de 8 h avec 1 réunion (stabilité longue durée, fuites mémoire).

**Découpage en quatre sous-étapes** (validées une à une, comme les étapes 13, 14 et 18) :

> Pourquoi découper : un vrai test de 300 connexions ne tient pas sur un PC de développement (le générateur de charge et le serveur se disputent le CPU) ; il faut d'abord un outil de mesure fiable et testé, puis les trois essais, puis la recommandation. Le générateur est le `lk perf load-test` officiel de LiveKit.

| Sous-étape | Contenu | Tests | Statut |
|---|---|---|---|
| 20a | **Outillage.** `deploy/dev/test-charge.ps1` (N salles × P participants, relevés `docker stats` toutes les 10 s en CSV) et `uv run task load-report` (CPU moyen/max, RAM max, **dérive de la RAM**, débit réseau, disque, coût par connexion). | U : `test_loadreport.py`. M : essai réduit 2 × 10 pendant 2 min, le rapport s'affiche. | ✅ validée 2026-11-07 |
| 20b | **Scénario 9 : charge nominale.** 10 salles × 30, ≥ 30 min, quelques salles avec enregistrement et partage d'écran, transcription en parallèle. Mesures : CPU, RAM, bande passante, disque, latence, perte de paquets, Egress, worker. | C : 0 déconnexion anormale sur 30 min. `-Recorded N` enregistre N salles via `uv run task load-record` (`loadrecord.py`). Limites : `lk` ne simule pas le partage d'écran (à faire à la main avec un vrai navigateur) ni la transcription (mesurée en 20d avec `transcribe-file` en parallèle). | ✅ avec réserve 2026-11-07 : 300 connexions non tenues sur le PC de dev, à refaire sur serveur |
| 20c | **Marge et longue durée.** Charge × 1,5 ; une journée de 8 h avec 1 réunion (fuites mémoire). | C : marge chiffrée ; dérive RAM < 10 %. Outillage : `-Rooms 15`, `-Minutes 480 -SampleSeconds 60`, signal `FUITE POSSIBLE`. | ✅ outillage validé 2026-11-07 ; mesures sur serveur reportées à la recette (étape 21) |
| 20d | **Recommandation de dimensionnement.** CPU / RAM / GPU / bande passante, stockage par heure enregistrée ; chiffres versés dans `DECISIONS.md` (dont les `____` de 14d et la machine de 16d). | M : Walid valide la recommandation. Voir `DECISIONS.md` §6 quindecies (points 222 à 229) : chiffres **provisoires**, à confirmer sur serveur. | ✅ provisoire, validée 2026-11-07 ; à confirmer sur serveur |
| 20e | **Diagnostic de l'échec du 10 × 30.** `diag-charge.ps1` (paliers, CPU hôte, connexions réelles, perte de paquets), correction du comptage (26 participants par salle, pas 30), décision : scénario 9 sur serveur. | M : palier 2 salles rejoué (52/52 connectés). Reste : test natif (facultatif) et paliers 2 à 10 depuis une autre machine, **sur serveur**. Voir `DECISIONS.md` §6 septdecies (248 à 254). | 🟡 diagnostic fait le 2026-11-08 ; cause « banc d'essai » probable, à confirmer sur serveur |

**Porte de validation** : ☐ scénario 9 validé (**reporté à la recette sur serveur** : 300 connexions non tenues sur le PC de dev, voir `DECISIONS.md` points 222 à 230 et le diagnostic 248 à 254 : le « 10 × 30 » mesuré était un 10 × 26) · ✅ recommandation de dimensionnement validée (provisoire, à confirmer sur serveur ; test de transcription en parallèle non fait)

---

## Étape 21 — Recette finale, licences, livrables

**Tâches**
- Rejouer les 9 scénarios de recette sur l'infrastructure finale (script + checklist signée).
- Documentation OpenAPI complète (API + webhooks).
- Tests automatisés des règles de créneau et de droits dans la CI.
- `docs/LICENCES.md` : briques, licences, contraintes.
- Dépôt Git propre, README, guide d'installation, guide d'exploitation.
- Conditions de maintenance et de garantie (partie contractuelle).

**Découpage en quatre sous-étapes** (validées une à une) :

| Sous-étape | Contenu | Statut |
|---|---|---|
| 21a | **Licences.** `uv run task licences` (inventaire Python et npm généré), `docs/LICENCES.md` (briques tierces, contraintes, décisions). Redis remplacé par Valkey (BSD-3). | ✅ validée 2026-11-07 |
| 21b | **Qualité livrable.** OpenAPI complet : section `webhooks` (8 événements, en-têtes de signature) + test que chaque opération `/v1` a résumé et description (`webhook_docs.py`). CI `.github/workflows/ci.yml` (fichier non inclus dans cette version du dépôt ; qualité, règles de créneau et de droits nommées, suite complète, front). Dépôt propre : `uv run task repo-check` (aucun secret versionné, `.env.*` chiffrés). | ✅ validée 2026-11-07 (948 tests verts, `repo-check` 0 problème) |
| 21c | **Documents.** README (index des documents), **`docs/EXPLOITATION.md`** (supervision, pannes, sauvegarde, mise à jour, capacité, données personnelles), un modèle de maintenance (retiré de cette version du dépôt). | ✅ validée 2026-11-07 |
| 21d | **Recette.** `deploy/dev/recette.ps1` (preuves automatiques des scénarios 1 à 8, compte rendu daté) et `docs/RECETTE.md` (vérifications à la main, registre signable) ; scénario 9 à refaire sur serveur. | 🟡 preuves automatiques des scénarios 1 à 8 : vertes le 2026-11-07 (633 tests) ; vérifications à la main à faire |

**Recette**

| # | Scénario | Résultat | Date | Validé |
|---|---|---|---|---|
| 1 | Créneau strict (15 h 55 / 16 h / 18 h / 18 h 01) | | | ☐ |
| 2 | URL inventée ou modifiée | | | ☐ |
| 3 | Micro coupé + exclusion définitive | | | ☐ |
| 4 | Salle d'attente | | | ☐ |
| 5 | Nom nominatif : visio, présence, transcription | | | ☐ |
| 6 | Enregistrement S3 + webhooks signés | | | ☐ |
| 7 | Chat et présence exportables | | | ☐ |
| 8 | Suppression + URL déjà émise | | | ☐ |
| 9 | 10 réunions × 30 participants | | | ☐ |

**Porte de validation finale** : ☐ 9 scénarios verts · ☐ livrables complets · ☐ **projet livré**

---

## Annexe — Rituel de fin d'étape

À chaque fin d'étape, je te fournis :

1. **Ce qui a été fait** (liste courte).
2. **Résultats des tests** (commande lancée + sortie).
3. **Ta checklist manuelle** (ce que tu dois essayer toi-même).
4. **Points d'attention / décisions à trancher**.

Tu réponds : `VALIDÉ, GO étape N+1` ou `À CORRIGER : ...`.
