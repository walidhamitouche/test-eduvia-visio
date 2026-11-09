# Installation et déploiement (étape 19)

Déploiement sur un serveur Linux avec **Coolify**, domaine `visio.eduvia.app`. Rien n'a encore
été construit ni lancé sur un vrai serveur : ce guide est à suivre **tel quel** pour la recette
« déploiement à blanc » (test M de l'étape 19). Tout écart rencontré est une erreur du guide à corriger.

## 1. Ce qu'il faut avant de commencer

| Besoin | Détail |
|---|---|
| Serveur | Linux, Docker, Coolify. Recommandation de taille : voir étape 20. |
| 2 adresses IP publiques | une pour Coolify (443 HTTPS), **une seconde pour le TURN** (`docs/RESEAU.md`, option A) |
| 3 noms DNS | `visio.eduvia.app` (API + interface), `rtc.eduvia.app` (signalisation LiveKit) → IP n°1 ; `turn.eduvia.app` → IP n°2 |
| S3 d'Eduvia | endpoint HTTPS, bucket **privé**, clés ; déjà chiffrés dans `.env.production` |
| La clé maître | `DOTENV_PRIVATE_KEY_PRODUCTION` (dans `.env.keys` sur le poste de dev, **jamais** dans le dépôt) |
| (option) GPU NVIDIA | pour la transcription rapide ; sinon CPU (plus lent, acceptable : « quelques heures ») |

Ports entrants à ouvrir : `80/tcp`, `443/tcp` (IP n°1) ; sur l'IP n°2 : `443/tcp` et `443/udp` ;
`7881/tcp`, `7882/udp`. Détail et alternatives : `docs/RESEAU.md`.

## 2. Valeurs de production à vérifier dans `.env.production`

Les valeurs ont été créées provisoires (étape 2). Avant le déploiement, les ouvrir avec
`dotenvx get -f .env.production` (poste de dev) et corriger au besoin avec
`dotenvx set NOM "valeur" -f .env.production` :

| Variable | Valeur attendue en production |
|---|---|
| `APP_ENV` | `production` |
| `PUBLIC_BASE_URL` | `https://visio.eduvia.app` |
| `LIVEKIT_URL` | `wss://rtc.eduvia.app` (**publique** : le navigateur s'y connecte, et l'API aussi) |
| `RECORDING_TEMPLATE_URL` | `http://api:8000/recording-template` (vue **depuis le conteneur Egress**) |
| `WEBHOOK_URL` | l'URL du backend Eduvia (https) |
| `S3_*` | le S3 réel d'Eduvia |
| `BRAND_LOGO_URL` | https |
| `TRANSCRIPTION_DEVICE` / `_COMPUTE_TYPE` | `cuda` / `float16` avec GPU ; `cpu` / `int8` sinon |

Puis **commiter** `.env.production` (il est chiffré ; `.env.keys` ne l'est jamais).

## 3. Certificat du TURN (`turn.eduvia.app`)

Le TURN termine lui-même le TLS : il lui faut son propre certificat, dans `/data/eduvia/certs`.
Exemple avec certbot, sur l'IP n°2 (`IP2`) :

```bash
sudo mkdir -p /data/eduvia/certs
sudo certbot certonly --standalone --preferred-challenges http \
     --http01-address IP2 -d turn.eduvia.app
sudo cp /etc/letsencrypt/live/turn.eduvia.app/fullchain.pem /data/eduvia/certs/turn.crt
sudo cp /etc/letsencrypt/live/turn.eduvia.app/privkey.pem   /data/eduvia/certs/turn.key
```

Renouvellement : un `--deploy-hook` qui recopie les deux fichiers puis relance `livekit`.

## 4. Créer l'application dans Coolify

1. **Projet → New resource → Docker Compose (dépôt Git)**, branche principale, fichier
   `deploy/docker-compose.yml`, *base directory* = racine du dépôt.
2. **Variables d'environnement** (les seules) :
   - `DOTENV_PRIVATE_KEY_PRODUCTION` : la clé maître → cocher « secret » (masquée).
   - `TURN_DOMAIN` = `turn.eduvia.app`
   - `TURN_BIND_IP` = `IP2` (adresse n°2)
   - `WITH_CUDA` = `1` seulement avec GPU (voir §7).
3. **Domaines** (champ de chaque service) : `api` → `https://visio.eduvia.app:8000` ;
   `livekit` → `https://rtc.eduvia.app:7880`. Coolify obtient les certificats HTTPS.
4. **Deploy**, puis `model-init` (§6). Ordre automatique : `config-init` (écrit les configs LiveKit et Egress) → `redis`,
   `livekit`, `api` → `egress`, `transcriber`.

> Si Coolify n'affiche pas le port à côté du domaine, saisir le domaine seul et indiquer le port
> du service dans ses réglages : la valeur utile est `8000` pour `api` et `7880` pour `livekit`.

## 5. Vérifier

```bash
curl -s https://visio.eduvia.app/health          # {"status":"ok"...}
uv run task smoke-prod                           # depuis le poste de dev (nécessite la clé maître)
```

`smoke-prod` joue le parcours automatisable (santé, clé, création, aperçu, lien inventé ou modifié,
jeton LiveKit, annulation). Il ne remplace pas la **visio réelle** : ouvrir un lien organisateur
et un lien participant dans deux navigateurs, vérifier micro / caméra / partage d'écran, un
enregistrement (`recording.ready`), une transcription (`transcript.ready`), puis les scénarios
de recette 1 à 8 (`docs/PLAN-PROJET.md`).

## 6. Première transcription : le modèle Whisper

Le worker est **hors ligne** et ne télécharge rien. Avant la première réunion, installer le modèle
(≈ 3 Go) **une seule fois** dans le volume `models` :

```bash
docker compose run --rm transcriber model-init
```

Aucun appel à un service d'IA externe : seul le dépôt du modèle est contacté, ce jour-là. Pour un serveur sans accès sortant, copier le dossier du modèle dans
le volume (`data/models/whisper/large-v3`).

## 7. GPU

```bash
WITH_CUDA=1 docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.gpu.yml up -d --build
```

Nécessite les pilotes NVIDIA et `nvidia-container-toolkit` sur l'hôte. Avec Coolify, ajouter le
fichier de surcharge dans les réglages du compose.

## 8. Sauvegarde et restauration de la base

La base (SQLite) contient réunions, liens, présences, historique du chat et files de tâches. Les
enregistrements et transcriptions sont sur le S3 d'Eduvia (à sauvegarder côté S3).

```bash
# Sauvegarde (cron quotidien conseillé) : garde les 14 dernières dans /data/eduvia/backups
DOTENV_PRIVATE_KEY_PRODUCTION=... deploy/backup.sh

# Restauration : arrêter API et worker, restaurer, redémarrer
docker compose stop api transcriber
docker compose run --rm --no-deps -v /data/eduvia/backups:/backups api restore /backups/visio-AAAAMMJJ-HHMMSS.db
docker compose start api transcriber
```

La sauvegarde est cohérente même pendant une réunion. La restauration vérifie le fichier avant
de remplacer la base. Les fichiers sont à copier hors du serveur (la sauvegarde seule ne protège
pas d'une panne du disque).

## 9. Contrôle de sécurité des images

```bash
docker build --target api -t eduvia-visio-api . && docker build --target transcriber -t eduvia-visio-transcriber .
sh deploy/check-images.sh     # historique, variables, fichiers : aucun secret en clair
```

## 10. Mise à jour et dépannage

- **Mise à jour** : `git push` puis *Redeploy* dans Coolify. Les migrations s'appliquent au démarrage de l'API.
- **Rotation de la clé maître** (`dotenvx rotate` n'existe pas dans toutes les versions) :
  `dotenvx decrypt -f .env.production` ; supprimer la ligne `DOTENV_PUBLIC_KEY_PRODUCTION` du fichier et
  la clé correspondante de `.env.keys` ; poser de nouveaux secrets (`dotenvx set ...`, ≥ 32 caractères) ;
  `dotenvx encrypt -f .env.production` ; `uv run task env-check` et `repo-check` **avant** de commiter ;
  mettre la nouvelle clé dans Coolify.
- **`config-init` en échec** : variable manquante ou mauvaise clé maître (le message nomme la variable).
- **LiveKit ne démarre pas** : certificat absent dans `/data/eduvia/certs` (§3).
- **Enregistrement qui échoue (`recording.failed`)** : `docker compose logs egress` ; vérifier `RECORDING_TEMPLATE_URL`
  et que le S3 est joignable depuis le serveur.
- **Visio muette alors que la page s'ouvre** : ports `7881/tcp` et `7882/udp` fermés, ou TURN mal joignable
  (`.\deploy\dev\test-reseau.ps1 -Domaine turn.eduvia.app`).
