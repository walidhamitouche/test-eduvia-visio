# Configuration chiffrée (dotenvx)

Besoin §7 : toute la configuration est dans un fichier `.env` **chiffré**, versionné dans le dépôt, sur le modèle des credentials Rails. Une seule clé maître par environnement est injectée au déploiement. Aucun secret en clair dans le dépôt, les images ou les variables Coolify.

## Principe

| Élément | Rôle | Versionné ? |
|---|---|---|
| `.env.development`, `.env.test`, `.env.production` | Valeurs chiffrées (`encrypted:...`) + clé publique | **Oui** |
| `.env.keys` | Clés privées (`DOTENV_PRIVATE_KEY_<ENV>`) | **Jamais** (dans `.gitignore`) |
| `.env.example` | Modèle documentaire, aucune valeur secrète | Oui |

Au lancement, `dotenvx run -f .env.<env> -- <commande>` déchiffre les valeurs et les place dans l'environnement du processus. L'application ne lit jamais de fichier `.env` elle-même (`src/visio/config.py`).

En production (Coolify), la **seule** variable à fournir est `DOTENV_PRIVATE_KEY_PRODUCTION`.

## Variables

| Variable | Rôle | Secret |
|---|---|---|
| `APP_ENV` | `development`, `test` ou `production` | non |
| `API_KEY` | Clé de l'API (≥ 32 caractères) | oui |
| `LINK_SIGNING_SECRET` | Signature des jetons de lien (≥ 32 car.) | oui |
| `PUBLIC_BASE_URL` | Domaine public, ex. `https://visio.eduvia.app` | non |
| `WEBHOOK_URL` | Destination des webhooks sortants | non |
| `WEBHOOK_SECRET` | Secret HMAC partagé avec Eduvia (≥ 32 car.) ; format des envois : `docs/WEBHOOKS.md` | oui |
| `SCHEDULER_INTERVAL_SECONDS` | Période du planificateur : fin de créneau, fermeture de salle, livraison des webhooks (défaut 1 s) | non |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | Connexion à LiveKit | secret : `..._SECRET` |
| `S3_ENDPOINT`, `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Stockage S3 compatible | secrets : clés |
| `BRAND_NAME`, `BRAND_LOGO_URL`, `BRAND_PRIMARY_COLOR`, `BRAND_SECONDARY_COLOR` | Marque blanche | non |
| `DATABASE_URL` | Base SQLite (défaut `sqlite:///./data/visio.db`) | non |
| `RECORDING_TEMPLATE_URL` | URL de `/recording-template` vue **depuis Egress** (dev Docker : `http://host.docker.internal:8000/recording-template`). **Obligatoire** : sans elle l'API ne démarre pas et aucun enregistrement n'est lancé (plus de repli sur la mise en page LiveKit) | non |
| `RECORDING_SEGMENT_SECONDS` | Durée d'un segment d'enregistrement ; segments fusionnés en un MP4 à la fin (ffmpeg requis sur le serveur de l'API). `0` = un seul fichier (défaut). Conseillé : `600` | non |
| `LOG_LEVEL` | Niveau des logs : `DEBUG`, `INFO` (défaut), `WARNING`, `ERROR` | non |
| `LOG_FORMAT` | `json` (une ligne JSON par log), `text` (lisible), `auto` (défaut : json en production, texte ailleurs). Champs de corrélation : `request_id`, `meeting_id` | non |
| `TRANSCRIPTION_MODEL` | Modèle faster-whisper (dossier ou nom : `medium`, `large-v3`), déjà présent sur le disque du worker. Défaut `large-v3` (retenu à l'étape 16c, choix définitif en fin de projet) | non |
| `TRANSCRIPTION_MODEL_DIR` | Dossier des modèles (défaut `data/models/whisper`). Jamais de téléchargement automatique | non |
| `TRANSCRIPTION_KEEP_AUDIO` | **Diagnostic** : `true` garde les pistes audio après la transcription (à télécharger avec `uv run task tracks-get`). Données personnelles : `false` (défaut) en production | non |
| `TRANSCRIPTION_DEVICE` | `auto`, `cpu` ou `cuda` (défaut `auto`) | non |
| `TRANSCRIPTION_COMPUTE_TYPE` | `int8` (CPU, défaut) ou `float16` (GPU) | non |
| `TRANSCRIPTION_POLL_SECONDS` | Intervalle de scrutation du worker (défaut `5`) | non |
| `TRANSCRIPTION_VOCABULARY` | Termes techniques et anglicismes à reconnaître, séparés par des virgules (ex. `Airflow, Python, Docker`). Vide par défaut | non |
| `WEB_DIST_DIR` | Dossier de l'interface construite (défaut `web/dist`, étape 7) | non |

## Règles de validation au démarrage

- Variable manquante ou invalide : l'application **refuse de démarrer** et liste les variables en cause, **sans jamais afficher de valeur**.
- Valeurs encore chiffrées (clé privée absente ou fausse) : message dédié.
- Secrets de moins de 32 caractères refusés ; couleurs au format `#RRGGBB`.
- **En production** : `https://` / `wss://` obligatoires, et toute valeur contenant `CHANGE_ME` est refusée.

## Commandes (PowerShell)

```powershell
# Installer dotenvx (une fois). Nécessite Node.js
winget install OpenJS.NodeJS.LTS
# fermer / rouvrir le terminal, puis :
npm install -g @dotenvx/dotenvx
dotenvx --version

# Créer et chiffrer les trois fichiers (génère des secrets aléatoires)
uv run task env-init development
uv run task env-init test
uv run task env-init production

# Vérifier qu'aucun secret n'est en clair
uv run task env-check

# Lancer l'API avec la configuration chiffrée
uv run task dev
```

Modifier une valeur (elle est rechiffrée automatiquement) :

```powershell
dotenvx set S3_BUCKET "eduvia-visio" -f .env.production
```

Lire une valeur déchiffrée (à éviter à l'écran en production) :

```powershell
dotenvx get API_KEY -f .env.development
```

## Gestion des clés

- `.env.keys` contient **toutes** les clés privées de ta machine. **Sauvegarde-le** (gestionnaire de mots de passe) : sans la clé, un fichier chiffré est définitivement illisible.
- Une clé par environnement : celle de production ne doit exister que dans `.env.keys` du poste d'administration et dans Coolify.
- Rotation : `uv run task env-init <env> --force` régénère le fichier et la clé (nouveaux secrets : à répercuter côté Eduvia, notamment `API_KEY` et `WEBHOOK_SECRET`).
- Si un secret a fuité : le changer immédiatement, puis régénérer le fichier concerné.

## Piège connu

Dans un fichier `.env`, `#` démarre un commentaire. Les valeurs contenant `#` (couleurs) sont donc écrites **entre guillemets** (`BRAND_PRIMARY_COLOR="#E8505B"`), sinon dotenvx ne les chiffre pas. `env-init` le fait automatiquement, et `env-check` détecterait l'oubli.

## Protection des routes publiques (revue du 2026-11-08)

| Variable | Défaut | Rôle |
|---|---|---|
| `JOIN_RATE_LIMIT_PER_MINUTE` | `1500` | Requêtes par minute et par IP sur `/v1/join` et `/v1/join/preview` ; au-delà `429 rate_limited`. `0` = désactivé. |
| `MAX_PARTICIPANTS_PER_MEETING` | `40` | Participants connectés par réunion ; au-delà `409 meeting_full` (organisateurs exemptés). `0` = illimité. |
