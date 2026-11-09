# Eduvia Visio — Journal des décisions

> Étape 0 — Cadrage. Ce document fige les choix avant d'écrire du code.
> Source de vérité du besoin : `eduvia-visio-besoin.pdf` (novembre 2026).
> Les décisions marquées **À TRANCHER** attendent ta réponse.

---

## 1. Stack retenue

| Sujet | Décision | Statut |
|---|---|---|
| Backend / API | **Python 3.12, FastAPI, Pydantic v2** (choix confirmé : simplifie transcription et modèles IA) | Acté |
| Accès base | SQLAlchemy 2 + Alembic, **SQLite** (WAL) | Proposé |
| Gestion du projet | **uv**, ruff, mypy, pytest | Proposé |
| Brique WebRTC | **LiveKit** (Apache 2.0), SDK serveur `livekit-api` (Python) | Proposé |
| Front visio | React + Vite + `@livekit/components-react` (navigateur uniquement, servi par le backend) | Proposé |
| Transcription | **faster-whisper** (MIT), local, même base de code | Proposé |
| Stockage | S3 compatible, endpoint configurable (SeaweedFS en dev uniquement ; MinIO abandonné, §6 nonies) | Acté (S3 fourni par Eduvia) |
| Configuration | **dotenvx**, fichiers `.env.<env>` chiffrés versionnés, une clé `DOTENV_PRIVATE_KEY_<ENV>` | Acté |
| Déploiement | Docker Compose, Coolify | Acté |

**Pourquoi LiveKit plutôt que Jitsi ou BigBlueButton** : TURN intégré (exigence réseau 443), enregistrement par service dédié (Egress) pilotable par API, webhooks natifs, jetons signés avec droits fins, licence Apache 2.0 (la consigne demande de lister les contraintes de licence : pas d'AGPL à gérer, marque blanche possible sans mention).

**Note dotenvx** : c'est un outil en ligne de commande indépendant du langage. L'app Python est lancée par `dotenvx run -- uvicorn ...`. Il faudra installer son binaire (Node/npm ou binaire autonome) sur Windows et dans l'image Docker. Je vérifie la procédure exacte à l'étape 2.

## 2. Arborescence du dépôt

```
eduvia-visio/
├─ pyproject.toml
├─ src/visio/
│  ├─ api/         routes FastAPI, auth par clé, erreurs
│  ├─ domain/      règle du créneau, rôles, états (logique pure)
│  ├─ services/    liens/jetons, LiveKit, présence, chat, webhooks, S3
│  ├─ workers/     scheduler, jobs, enregistrement, transcription
│  └─ db/          modèles, migrations
├─ web/            front de visio (React)
├─ tests/          unit/ · integration/ · e2e/
├─ deploy/         docker-compose, Coolify
├─ docs/           DECISIONS.md, INSTALL.md, LICENCES.md
└─ .env.development / .env.test / .env.production   (chiffrés)
```

## 3. Conventions d'API

- API REST JSON, authentifiée par clé (`Authorization: Bearer`), documentée en OpenAPI (générée par FastAPI).
- Champs en `snake_case`, dates **ISO 8601 avec décalage** (conforme au besoin), interface affichée en heure de Paris.
- Erreurs : `{ "error": { "code": "invalid_slot", "message": "..." } }` avec codes explicites : `invalid_slot`, `meeting_already_started`, `meeting_not_found`, `unauthorized`, `invalid_link`, `not_open_yet`, `meeting_ended`, `participant_excluded`…
- Les endpoints du besoin (section 4 du PDF) sont repris **tels quels** ; aucun n'est retiré.
- Préfixe de version **`/v1`** (tranché à l'étape 0) : `POST /v1/meetings`, etc. Seule `/health` reste sans préfixe, sans clé.

## 4. Matrice de conformité à la consigne

Chaque exigence du PDF est rattachée à l'étape qui la livre. Rien n'est omis.

| Exigence du besoin | Réf. PDF | Étape |
|---|---|---|
| Réunions créées **uniquement** par API, aucune création depuis l'interface | §2 | 4, 7 |
| Créneau strict, coupure à l'heure de fin, avertissement avant | §2 | 3, 10 |
| URL inconnue : erreur, jamais une salle vide | §2 | 5, 6 |
| Marge d'ouverture anticipée (0 par défaut) | §2 | 3 |
| Modifier / annuler avant début, clore une réunion en cours | §2, §4 | 4, 10 |
| Dates ISO 8601, affichage heure de Paris | §2 | 3, 7 |
| Deux rôles via le lien, pas de compte | §3 | 5, 8 |
| Mute, coupure caméra/partage, exclusion définitive, fin anticipée | §3 | 8 |
| Salle d'attente (option à la création, « tout admettre ») | §3 | 9 |
| Liens nominatifs (`display_name`, `external_id`), lien générique | §3 | 5, 7 |
| Plusieurs organisateurs, lien organisateur indevinable | §3 | 5 |
| Tous les endpoints REST + OpenAPI + erreurs JSON | §4 | 4, 5, 15, 16, 11 |
| `metadata` renvoyé tel quel (API et webhooks) | §4, §6 | 4, 12 |
| Enregistrement audio + vidéo + partage d'écran, MP4, S3, indicateur | §5 | 14 |
| Aucune expiration automatique, suppression par API uniquement | §5 | 15 |
| Transcription auto-hébergée, français, par locuteur, JSON/texte/SRT/VTT | §5 | 16 |
| Chat exportable, présence avec temps total | §5 | 11 |
| Suppression définitive, y compris URL temporaire déjà émise (15 min max) | §5 | 15 |
| Webhooks listés, HMAC, relances, metadata | §6 | 12, 14, 16 |
| Sécurité : clé API, HTTPS, jetons, rôle vérifié côté serveur à chaque connexion | §6 | 4, 5, 6, 8 |
| DTLS-SRTP, S3 privé, URL signées | §6 | 6, 15 |
| RGPD : UE, sans tiers, indicateur d'enregistrement visible | §6 | 14, 16, 19 |
| Docker Compose, Coolify, navigateurs récents desktop + mobile | §7 | 7, 19 |
| TURN inclus (TCP/TLS 443) | §7 | 18 |
| Marque blanche (logo, couleurs, domaine, textes FR, sans mention de la brique) | §7 | 7 |
| Licences des briques listées | §7 | 21 |
| Configuration chiffrée dotenvx, aucun secret en clair | §7 | 2, 19 |
| Logs structurés, `/health`, erreurs rejouables, reprise après redémarrage | §7 | 12, 17 |
| Dimensionnement serveurs + stockage par heure enregistrée | §7 | 14, 20 |
| Livrables : dépôt, Compose, OpenAPI, tests créneau/droits, mise en prod | §8 | 19, 21 |
| Réponse attendue : stack, chiffrage, coût infra, licences, maintenance | §8 | 0, 20, 21 |

**Hors périmètre respecté** (rien ne sera développé) : back-office, comptes et authentification des participants, création de réunion depuis la visio, apps mobiles natives, intégration côté Eduvia.

## 5. Licences des briques (premier état)

| Brique | Licence | Contrainte |
|---|---|---|
| LiveKit server, Egress, SDK | Apache 2.0 | Aucune mention imposée au-delà de la conservation des notices de licence |
| faster-whisper | MIT | Aucune |
| Modèles Whisper (poids) | MIT | Aucune |
| FastAPI, Pydantic, SQLAlchemy | MIT / BSD | Aucune |
| React, Vite | MIT | Aucune |
| SeaweedFS (S3 de dev local uniquement, étape 13b) | Apache 2.0 | Jamais livré ni déployé : le S3 de prod est celui d'Eduvia. Remplace MinIO (AGPL-3.0), voir §6 nonies |
| boto3 / botocore (client S3) | Apache 2.0 | Aucune |
| moto (S3 simulé, tests uniquement) | Apache 2.0 | Non livré |

À finaliser dans `docs/LICENCES.md` (étape 21), avec vérification des dépendances transitives.

## 6. Points du besoin à trancher (ambiguïtés ou silences)

Je ne choisis pas seul sur ces points : la consigne ne les tranche pas.

1. **Salle d'attente, participant refusé.** Le PDF dit « accepter ou refuser » sans préciser la suite.
   *Proposition* : un refus bloque ce lien pour la réunion (comme une exclusion).
2. **Transcription sans enregistrement.** `transcription: true` et `recording: false` est possible. La transcription exige de capter l'audio.
   *Proposition* : l'audio est capté temporairement, transcrit, puis **supprimé** ; seul le texte est conservé. Aucun MP4 n'est stocké.
3. **Audio par participant pour la transcription.** Ces pistes sont des artefacts de plus sur le S3.
   *Proposition* : elles sont conservées jusqu'à la fin de la transcription, puis supprimées, sauf si `recording: true` (suppression via l'API de l'enregistrement).
4. **Durée maximale d'une réunion.** Le besoin dit « jusqu'à environ 8 h ».
   *Proposition* : plafond à 12 h, erreur `invalid_slot` au-delà.
5. **Lien générique, `external_id`.** Le participant saisit son nom et n'a pas d'identifiant.
   *Proposition* : `external_id` vaut `null` dans la présence et la transcription ; le nom est celui saisi.
6. **Cadence des relances de webhooks.** « Plusieurs fois, délai croissant ».
   *Proposition* : 10 s, 1 min, 5 min, 30 min, 2 h, puis abandon marqué et rejouable via l'outil d'exploitation.
7. **Base de données.** Le besoin est silencieux.
   *Proposition* : SQLite (10 réunions simultanées, peu d'écritures, un seul service). Passage à Postgres possible plus tard sans changer l'API.
8. **Préfixe `/v1`** (voir §3).

## 6 bis. Réponses attendues à l'étape 0

Réponds simplement, par exemple : `1 ok, 2 ok, 3 ok, 4 ok, 5 ok, 6 ok, 7 ok, 8 oui` ou indique ce qui change.

## 6 ter. Étape 3 — Règle du créneau : ce qui est codé et ce qui est à confirmer

**Règle codée** (`src/visio/domain/slot.py`, fonctions pures, horloge injectée) :

- Ouverture à `starts_at − early_join_minutes` (inclus). Fin à `ends_at` (**exclue**) : 17:59:59 accepté, 18:00:00 refusé.
- Clôture anticipée (`ended_at`) : si elle est antérieure à `ends_at`, c'est elle qui coupe. Une réunion `ended` ou `cancelled` est refusée à toute heure.
- Statuts : `not_open_yet`, `open`, `ended`, `cancelled`. Les calculs se font sur des instants absolus (UTC) : un créneau qui chevauche le changement d'heure garde sa vraie durée.
- Dates : ISO 8601 **avec décalage obligatoire** (`+01:00`, `Z`). Une date sans décalage est refusée (`invalid_slot`). Affichage : heure de Paris (`to_paris`).
- Validation : `ends_at > starts_at`, durée ≤ 12 h (point 4 acté), `early_join_minutes` entier de 0 à 60 (0 par défaut).

**Points confirmés par Walid le 2026-11-02** (acceptés tels que proposés) :

9. **Plafond de la marge d'ouverture anticipée : 60 min.** Le besoin parle de « par ex. 10 min » sans plafond.
10. **« Réunion commencée »** (pour `PATCH` / `DELETE` → `409 meeting_already_started`) : dès qu'une personne s'est connectée (`live`), ou que `starts_at` est passé. Conséquence : une réunion que personne n'a rejointe, une fois `starts_at` passé, ne peut plus être annulée par `DELETE` (elle peut être close par `POST /end` ou se terminer à `ends_at`).
11. **Date sans décalage refusée** plutôt que supposée en heure de Paris.

**Point d'attention pour les étapes 5 et 8** (rien n'est codé ici) : avec un lien participant **générique** partagé par tout le monde, révoquer le `jti` du lien exclurait tous les participants. L'exclusion définitive devra donc viser l'**identité de la personne** (identifiant propre à chaque connexion, mémorisé dans le navigateur), pas le lien. La table `participant_sessions` porte déjà une `participant_identity` pour cela. Limite à accepter : sur un lien générique, une personne exclue qui vide son navigateur peut revenir ; seul un lien nominatif l'empêche réellement.

## 6 quater. Étape 4 — API réunions : ce qui est codé et ce qui est à confirmer

**Routes** (toutes sous `/v1`, toutes exigent `Authorization: Bearer <clé>`) : `POST /v1/meetings`, `GET /v1/meetings/{id}`, `PATCH /v1/meetings/{id}`, `POST /v1/meetings/{id}/end`, `DELETE /v1/meetings/{id}`. Les autres endpoints du besoin arrivent aux étapes 5, 11, 15 et 16.

**Règles codées**

- Clé comparée à temps constant ; sans clé ou clé fausse : `401 unauthorized` **avant** toute validation du corps (rien n'est créé). En production, HTTP simple refusé : `403 https_required`.
- Erreurs toujours `{"error": {"code", "message"}}` : `unauthorized`, `https_required`, `invalid_slot` (422), `validation_error` (422), `meeting_not_found` (404), `meeting_already_started` (409), `meeting_cancelled` (409), `meeting_not_started` (409), `not_found` / `method_not_allowed` (routes inconnues), `internal_error` (500, sans détail).
- `POST` renvoie `201` avec `id`, `status`, les options, `participant_url`, `organizer_urls[]` (dans l'ordre des `organizers` de la requête) et `organizers[]` (les mêmes liens avec le nom). En-tête `Location`. Réunion et liens créés dans une seule transaction.
- `metadata` : objet JSON libre, renvoyé à l'identique (valeurs, ordre des clés). Limite 16 Ko, `NaN` refusé.
- `GET` : état, options, `metadata`, participants connectés (sessions de présence sans sortie ; rempli aux étapes 6 et 11). **Ne renvoie jamais de lien.**
- `PATCH` : seulement les champs envoyés ; le créneau est revalidé sur les valeurs fusionnées. `409 meeting_already_started` dès qu'une personne s'est connectée ou que `starts_at` est atteint (point 10).
- `DELETE` : annule une réunion non commencée, renvoie son état (`cancelled`).
- `POST /end` : clôt une réunion **ouverte** (en cours, ou ouvrable mais vide). Enregistre `ended_at = maintenant`.

**Pas encore fait (volontairement)** : `/end` ne ferme pas encore la salle LiveKit, n'envoie aucun webhook et ne génère aucun artefact (étapes 6, 10, 12). `status: ended` est déjà calculé à l'heure de fin même si le planificateur (étape 10) n'a rien enregistré.

**Points confirmés par Walid le 2026-11-02** (acceptés tels que proposés ; la consigne ne les tranche pas) :

12. **Au moins 1 organisateur, au plus 10.** Sans organisateur, personne ne pourrait modérer, et en salle d'attente personne n'entrerait.
13. **Création refusée si `ends_at` est déjà passé** (`invalid_slot`). Un `starts_at` passé avec une fin future est accepté (réunion à lancer tout de suite).
14. **Dates renvoyées en heure de Paris** avec décalage (`+01:00` / `+02:00`), comme l'exemple du besoin. Les entrées acceptent n'importe quel décalage ou `Z`.
15. **Entrées strictes** : `"10"` n'est pas un entier, `1` n'est pas un booléen, et **un champ inconnu est refusé** (`recordng: true` ne doit jamais donner une réunion sans enregistrement en silence).
16. **`PATCH`** : `metadata` envoyé = remplacé en entier (pas de fusion) ; `null` interdit (omettre le champ) ; les organisateurs ne se changent pas ici (nouveaux liens : étape 5).
17. **`DELETE` et `/end` sont rejouables** : déjà annulée / déjà terminée → `200` sans rien changer (la première heure de clôture est conservée). Annulée puis `PATCH` ou `/end` → `409 meeting_cancelled`. `/end` avant l'ouverture → `409 meeting_not_started` (utiliser `DELETE`).
18. **Liens renvoyés uniquement à la création** (et plus tard par `POST /links`). Un lien organisateur perdu se remplace, il ne se relit pas.
19. **Liens provisoires** : `<domaine>/j/<id du lien>.<signature HMAC>`. L'id est un UUID4 aléatoire (128 bits), indépendant d'un lien à l'autre et de l'id de la réunion ; la signature empêche d'en inventer un. Le format définitif (JWT) est celui de l'étape 5 : seule la fonction `issue_link_url` changera.
20. **HTTPS en production** : le refus se base sur `X-Forwarded-Proto` s'il est présent. Le proxy de Coolify doit donc le poser (à vérifier à l'étape 19, test `smoke-prod`).


## 6 quinquies. Étape 5 — Liens et jetons : ce qui est codé et ce qui a été confirmé

**Codé** (`src/visio/services/links.py`, `src/visio/api/links.py`)

- Un lien = une ligne `links` + un **jeton JWT HS256** dans l'URL `<domaine>/j/<jeton>`. Contenu : `jti` (id du lien), `mid` (réunion), `role`, `iat`, et `name` / `eid` pour un lien nominatif.
- `POST /v1/meetings/{id}/links` (clé d'API) : crée un lien nominatif (`role`, `display_name`, `external_id` facultatif) → `201` avec `id`, `url`. L'URL n'est renvoyée qu'ici et à la création de la réunion.
- `resolve_link(db, settings, jeton)` : à appeler **à chaque connexion** (étape 6). Vérifie la signature, puis relit le lien en base et compare réunion, rôle, nom et identifiant externe au jeton, puis refuse un lien révoqué. Ne regarde pas l'heure (règle du créneau, étape 6).
- `revoke_link` : liste de révocation (`revoked_at`), prête pour l'exclusion définitive (étape 8). Pas encore exposée en HTTP.
- Durcissement : seul HS256 accepté (`none` et HS384/512 refusés), encodage **canonique** exigé (un caractère modifié est toujours refusé, même sans effet sur les octets décodés), taille plafonnée à 4096 caractères avant décodage, nouvelle dépendance `pyjwt`.

**Points confirmés par Walid le 2026-11-02** (la consigne ne les tranche pas ; acceptés tels que proposés, points 22 et 27 compris)

21. **Format du jeton : JWT HS256**, signé avec `LINK_SIGNING_SECRET`. L'URL ne contient que le jeton.
22. **Pas d'expiration (`exp`) dans le jeton.** *Écart assumé avec le plan* (« expiration = fin du créneau »). Raisons : les liens ne sont jamais relus et `PATCH` peut déplacer le créneau, donc une date figée dans le jeton deviendrait fausse ; et après la réunion, le lien doit afficher « réunion terminée » (besoin §2), pas « lien invalide ». La durée de vie réelle est le créneau, décidé côté serveur à chaque connexion (étape 6). Conséquence : un lien reste reconnu tant que la réunion existe en base. **Confirmé : pas d'expiration dans le jeton.** Une expiration dure (ex. fin du créneau + 30 jours) reste un ajout simple si le besoin change.
23. **La base fait foi.** Un jeton ne donne aucun droit que la base ne confirme pas : rôle, réunion, nom et `external_id` sont relus et comparés à chaque connexion. Un jeton signé avec le bon secret mais qui prétend « organisateur » sur un lien participant est refusé.
24. **Identifiants à 128 bits aléatoires.** Correction : les `jti` étaient des UUID4, qui n'ont que 122 bits aléatoires. Ils viennent maintenant de `secrets.token_hex(16)` (même longueur, aucune migration). La sécurité repose de toute façon sur la signature HMAC.
25. **Une seule erreur pour tout lien invalide** : `404 invalid_link`, message identique (« lien invalide »), qu'il soit inventé, modifié ou inconnu. Un lien révoqué donne `403 participant_excluded`. Autres codes ajoutés : `meeting_ended` (409) et `too_many_organizers` (409).
26. **`POST /links`** : nom obligatoire, y compris pour un organisateur (liens nominatifs) ; refusé sur une réunion annulée (`meeting_cancelled`) ou terminée (`meeting_ended`) ; au plus **10 liens organisateur actifs** par réunion (point 12), un lien révoqué libère une place.
27. **Lien organisateur perdu** : on en génère un nouveau, mais l'ancien reste valable tant qu'il n'est pas révoqué. **Tranché : non**, pas de `DELETE /v1/meetings/{id}/links/{link_id}` pour l'instant (hors besoin). Ajout possible plus tard sans casser l'API : `revoke_link` existe déjà dans le service et sert à l'exclusion (étape 8).
28. **Changer `LINK_SIGNING_SECRET` invalide tous les liens déjà émis** (à noter dans le guide d'exploitation, étape 19).
29. **Pas de route publique d'ouverture à cette étape.** `POST /join` arrive à l'étape 6 ; le scénario 2 (URL inventée ou modifiée) est donc testé ici sur `resolve_link`, et passera en HTTP à l'étape 6.

## 6 sexies. Étape 6 — Rejoindre une réunion : ce qui est codé et ce qui a été confirmé

**Codé** (`services/join.py`, `services/livekit_tokens.py`, `api/join.py`, `api/devtools.py`)

- `POST /v1/join` (corps : `token`, `display_name` facultatif, `client_id` facultatif). Dans l'ordre : lien (signature, base, révocation) → créneau → nom → jeton LiveKit. **Rien n'est émis tant qu'une vérification échoue.**
- Réponse `200` : `livekit_url` (en `ws://` / `wss://`), `livekit_token`, `expires_at`, `seconds_until_end`, `client_id`, `meeting` (titre, créneau, options, dont `recording` pour l'indicateur) et `participant` (nom, `external_id`, rôle). Ni l'id de la réunion, ni l'id du lien, ni `metadata` ne sont renvoyés.
- Refus distincts : `404 invalid_link` (inventé, modifié, inconnu), `403 participant_excluded`, `409 not_open_yet` (avec `opens_at`, heure de Paris), `409 meeting_ended`, `409 meeting_cancelled`, `422 display_name_required`.
- Scripts : `deploy/dev/livekit-dev.ps1` lance LiveKit Server avec la clé et le secret de `.env.development` ; `GET /dev/livekit-test` est une page d'essai (voir point 37).

**Points confirmés par Walid le 2026-11-03** (la consigne ne les tranche pas ; acceptés tels que proposés, point 31 compris)

30. **`POST /v1/join`**, sous `/v1`, **publique** (le jeton du lien est l'identifiant, pas de clé d'API) ; HTTPS exigé en production comme pour le reste de l'API.
31. **Jeton LiveKit de courte durée : 10 minutes, jamais au-delà de la coupure.** *Précision par rapport au plan* (« expirent à `ends_at` »). Raison : LiveKit ne contrôle l'expiration qu'à la connexion, et un jeton valable jusqu'à `ends_at` permettrait à une personne exclue (étape 8) ou à un participant d'une réunion annulée de se reconnecter. Avec 10 minutes, toute reconnexion repasse par `POST /v1/join`, donc par toutes les vérifications. L'interface (étape 7) devra rappeler `POST /v1/join` à chaque reconnexion.
32. **Jeton fabriqué avec PyJWT**, pas avec le SDK `livekit-api` : le format est documenté et PyJWT est déjà là (aucune dépendance de plus). Le SDK arrive à l'étape 8, quand le backend devra appeler LiveKit (couper un micro, exclure).
33. **Salle : nom dérivé par HMAC** (`rm_` + 32 hex) de l'id de la réunion avec `LINK_SIGNING_SECRET`, donc opaque, stable, **sans colonne ni migration**. LiveKit crée la salle à la première connexion (comportement par défaut) : le backend n'en crée aucune lui-même, et sans jeton émis ici personne ne peut en ouvrir une. Conséquence : changer `LINK_SIGNING_SECRET` change aussi les noms de salle (il invalide déjà tous les liens, point 28). Si l'étape 12 a besoin du lien inverse « salle → réunion », on l'ajoutera avec une colonne.
34. **Identités LiveKit opaques.** Lien nominatif : identité stable dérivée du lien (même lien = même identité, une reconnexion remplace l'ancienne connexion). Lien générique : identité dérivée d'un `client_id` que le serveur fournit au navigateur et qu'il mémorise ; les autres participants ne voient que l'identité dérivée, donc ne peuvent pas la reprendre. C'est la base de l'exclusion à l'étape 8 (point d'attention de §6 ter).
35. **Droits LiveKit** : participant = rejoindre, publier (micro, caméra, partage d'écran), s'abonner, envoyer des données (chat). Organisateur = la même chose + `roomAdmin`. Aucun des deux ne peut créer de salle. `metadata` du jeton : `{"role", "external_id"}`.
36. **Nom** : sur un lien nominatif, le nom du lien l'emporte (un `display_name` envoyé en plus est ignoré : « ne se modifie pas »). Sur le lien générique, le nom est obligatoire mais vérifié **après** le créneau : la page « pas encore ouverte » n'a pas besoin de nom.
37. **Page d'essai `GET /dev/livekit-test`**, réservée à `APP_ENV=development` (404 en test et en production, absente de l'OpenAPI). Le client LiveKit est chargé depuis jsDelivr : acceptable pour un outil local, pas pour l'interface finale (étape 7 : le client sera embarqué).

**Limites connues (volontaires, traitées plus tard)**

- `join` **ne passe pas la réunion en `live`** et n'enregistre aucune présence : cela viendra des webhooks LiveKit (étapes 11 et 12). Émettre un jeton n'est pas se connecter.
- **Salle d'attente non appliquée** : avec `waiting_room: true`, `join` donne déjà un jeton complet. L'étape 9 la met en place (le champ `waiting_room` est déjà renvoyé à l'interface).
- Un jeton déjà émis reste utilisable **jusqu'à 10 minutes** après une annulation, une clôture ou une exclusion, tant que la salle n'est pas fermée côté LiveKit (étapes 8 et 10).

## 6 septies. Étape 7 — Interface de visio : ce qui est codé et ce qui a été confirmé

**Confirmé par Walid le 2026-11-03** : les points 38 à 47 ci-dessous sont acceptés tels que proposés.

38. **Interface servie par l'API, même domaine.** Application React/Vite dans `web/`, construite dans `web/dist` (`uv run task web-build`). `GET /j/<jeton>` renvoie **la même page pour tout jeton**, valide ou non : c'est l'application qui interroge ensuite l'API et affiche « lien invalide ». La page ne révèle donc rien sur la validité d'un lien. `GET /assets/...` sert les fichiers construits (noms à empreinte, cache longue durée, sortie du dossier impossible). Ces routes sont absentes de l'OpenAPI.
39. **`POST /v1/join/preview`** : route publique qui renvoie l'état (`not_open_yet`, `open`, `ended`, `cancelled`), l'heure du serveur, l'heure d'ouverture, les options de la réunion (`waiting_room`, `recording`, `transcription`) et le rôle / nom du lien (`name_required` si le lien est générique). **N'émet aucun jeton LiveKit** et ne démarre pas la réunion. Lien inventé ou modifié : `invalid_link` (404), même message dans tous les cas ; lien révoqué : `participant_excluded` (403).
40. **En-têtes de la page** : `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, car le jeton est dans l'URL. HTTPS exigé en production, comme pour le reste. Si l'interface n'est pas construite : erreur claire `web_not_built` (503).
41. **Marque blanche par la configuration** : `BRAND_NAME`, `BRAND_LOGO_URL`, `BRAND_PRIMARY_COLOR`, `BRAND_SECONDARY_COLOR` (couleurs au format `#RRGGBB`, vérifié au démarrage). Elles sont injectées dans la page **à chaque requête** : un redémarrage du serveur suffit, pas de reconstruction. La couleur du texte sur une couleur de marque est choisie automatiquement (blanc ou noir, meilleur contraste WCAG). Les valeurs sont échappées dans la page (une valeur piégée comme `</script>` n'exécute rien).
42. **Textes 100 % en français dans `web/src/fr.ts`**, aucune mention de la brique technique dans ce qui est affiché. Le client LiveKit est **embarqué** dans le bundle (plus de CDN, contrairement à la page d'essai du point 37). L'URL du serveur de médias visible dans l'onglet Réseau est inévitable et n'est pas une mention affichée.
43. **Heure de Paris et compte à rebours fidèle au serveur** : l'écran d'attente affiche l'heure d'ouverture et la date en heure de Paris, et calcule le compte à rebours avec l'écart entre l'horloge du serveur et celle de l'appareil (une horloge de PC fausse ne fausse pas l'attente). L'aperçu est relu toutes les 30 s avant l'heure (suivi d'un report ou d'une annulation) puis toutes les 2 s une fois l'heure atteinte, jusqu'à ce que le serveur dise « ouvert » : l'entrée se fait sans recharger.
44. **Reconnexion toujours par `POST /v1/join`** (suite du point 31). Jusqu'à 6 tentatives avec attente croissante (1,5 s à 8 s). Réponses du serveur traduites en écrans : réunion terminée, annulée ou pas ouverte → « réunion terminée / annulée » ; lien révoqué → « Accès retiré » ; retiré par l'organisateur → « Vous avez été retiré » ; même identité connectée ailleurs → « Connecté ailleurs » ; tentatives épuisées → « Reconnexion impossible » avec un bouton pour revenir.
45. **Mémoire locale du navigateur** (`localStorage`) : l'identifiant client fourni par le serveur pour un lien générique (point 34) et le dernier nom saisi. Jamais indispensable : chaque accès est protégé (mode privé, stockage bloqué).
46. **Nom** : lien nominatif = nom pré-rempli et verrouillé ; lien générique = nom obligatoire (le bouton refuse un champ vide), vérifié aussi par le serveur (point 36).
47. **Affichage seulement, pour ce qui vient plus tard** : le chat passe par le canal de données (messages de 2 000 caractères au plus) et **n'est pas conservé côté serveur** (étape 11) ; l'indicateur « Enregistrement en cours » suit l'état signalé par le serveur de médias et la pré-jointure annonce l'enregistrement et la transcription selon les options de la réunion, mais rien n'est enregistré ni transcrit avant les étapes 14 et 16 ; le texte de salle d'attente est prêt mais masqué jusqu'à l'étape 9.

**Tests E2E** : ils utilisent Microsoft Edge ou Chrome déjà installés (variable `E2E_BROWSER` pour imposer un choix), sans télécharger de navigateur ; `uv run playwright install chromium` n'est nécessaire que si aucun des deux n'existe. Le backend et LiveKit sont lancés sur des ports libres avec une base temporaire.

**Limites connues (volontaires, traitées plus tard)**

- Pas de modération (couper un micro, exclure, terminer) : étape 8.
- Salle d'attente non appliquée : étape 9.
- Pas d'avertissement avant la fin ni de coupure automatique à `ends_at` : étape 10.
- Présence et chat non persistés : étape 11.
- **Mobile** : micro et caméra exigent HTTPS ; la salle complète sur téléphone est à revérifier aux étapes 18 et 19 (TLS).

## 6 octies. Étapes 8 à 12 — Modération, salle d'attente, fin de créneau, présence, chat, webhooks : ce qui est codé et ce qui est à confirmer

Les cinq étapes ont été faites **d'un seul bloc** à ta demande (la règle « une étape à la fois » du plan est donc levée pour celles-ci). Elles sont codées et testées, et ont été **validées le 2026-11-04** après la recette manuelle (T1 à T8) : les points 48 à 67 sont acceptés tels que proposés.

**Modération (étape 8)**

48. **Autorisation par le jeton de lien** : `/v1/room/mute|remove|end|lobby|…` reçoivent le jeton du lien de l'appelant ; un lien participant reçoit `403 organizer_required`, un jeton falsifié `invalid_link`. Plusieurs organisateurs : chacun a son lien, tous modèrent.
49. **Pas de `roomAdmin` dans les jetons LiveKit** (écart au plan : « via les permissions LiveKit ») : aucun client ne peut modérer en parlant directement au serveur de médias ; seule l'API peut le faire. `canPublishData` est aussi désactivé pour tous (le chat passe par le backend, point 60).
50. **Exclusion définitive = entrée `excluded` dans `access_entries`** plutôt qu'un `jti` en liste de révocation : même effet (reconnexion refusée avec `participant_excluded`), mais par personne et non par jeton, ce qui couvre aussi le lien générique.
51. **Limite connue** : sur le **lien générique**, l'identité d'un participant est liée à l'identifiant du navigateur (`client_id`). Vider le stockage du navigateur permet donc de revenir. Les **liens nominatifs** sont étanches. À ton arbitrage : recommander les liens nominatifs quand l'exclusion compte.
52. Les organisateurs ne sont pas modérables (ni par un autre organisateur) ; couper une caméra ou un partage d'écran passe par `source`.

**Salle d'attente (étape 9)**

53. **Aucun jeton tant que non admis** (plus strict que « jeton sans `canSubscribe` ») : `POST /v1/join` renvoie `status: waiting` sans jeton ; l'interface relit la demande toutes les 2 s et entre toute seule à l'admission.
54. **Seuls les participants attendent** ; un organisateur entre toujours. Sans salle d'attente, entrée directe. Une demande n'est visible de l'organisateur que si le navigateur a relu dans les 20 dernières secondes (personne « fantôme » écartée).
55. **Refusé = reste refusé** (réponse à « règle à confirmer ») jusqu'à ce qu'un organisateur le réadmette. Tant qu'aucun organisateur n'est dans la salle, personne n'est admis.

**Fin de créneau (étape 10)**

56. **Planificateur sans état** : une boucle (`SCHEDULER_INTERVAL_SECONDS`, 1 s par défaut) relit la base ; aucune minuterie en mémoire à perdre. Une réunion dont l'heure est passée pendant une panne est terminée au redémarrage, avec `ended_at = ends_at`.
57. **Fermeture de la salle** : tentative immédiate, sinon tâche `close_room` avec relances (le serveur de médias peut être indisponible). Un participant qui se reconnecte avec un jeton encore valide (10 min, point 31) est retiré dès l'évènement `participant_joined` reçu : **le point 31 est ainsi refermé par l'application**, sans raccourcir le jeton.
58. **Avertissement** : bannière à moins de 5 minutes de la fin (texte d'urgence sous 1 minute), calculée avec l'horloge du serveur.

**Présence et chat (étape 11)**

59. **Présence = webhooks du serveur de médias** (`POST /v1/livekit/webhook`, authentifié par le JWT signé dont la claim `sha256` doit correspondre au corps). Identités opaques `pn_…` (lien nominatif) / `pg_…` (générique) dérivées par HMAC : aucun nom ni identifiant Eduvia dans la salle. **Règle d'émargement** : durée par personne = union de ses intervalles ; une personne qui sort et revient a plusieurs sessions mais une seule durée totale sans double compte.
60. **Chat conservé côté serveur** (remplace le canal de données du point 47) : `POST /v1/room/chat` (2 000 caractères max), diffusion aux présents, export `GET /v1/meetings/{id}/chat` avec auteurs vérifiés par le serveur (un participant ne peut pas écrire sous le nom d'un autre). Un participant en attente n'a ni lecture ni écriture.
61. Le client LiveKit officiel pour Python n'est **pas utilisé** : le serveur de médias est piloté en HTTP (API Twirp, `httpx`) avec un jeton d'administration de courte durée, pour éviter une dépendance de plus. `httpx` devient donc une dépendance d'exécution.

**Webhooks sortants (étape 12)**

62. Quatre événements : `meeting.started`, `participant.joined`, `participant.left`, `meeting.ended` (`docs/WEBHOOKS.md`). Format du corps : `id`, `type`, `created_at`, `meeting` (id, titre, créneau, `metadata`), `data`.
63. **Boîte d'envoi** : l'événement est écrit dans la même transaction que le changement ; livraison par le planificateur ; plus aucun événement perdu au redémarrage.
64. **Signature** `X-Visio-Signature: v1=<HMAC-SHA256 de "<timestamp>.<corps>">`, `X-Visio-Timestamp` et `X-Visio-Event-Id` (idempotence) ; tolérance de 5 minutes à la vérification.
65. **Relances** : 10 s, 1 min, 5 min, 30 min, 2 h (6 tentatives), puis `abandoned` ; rejeu manuel prévu à l'étape 17. Tout statut hors 2xx est un échec ; pas de redirection suivie.
66. **Ordre** : à la clôture, les `participant.left` partent avant `meeting.ended` ; en cas de relance l'ordre d'arrivée n'est pas garanti (Eduvia se fie aux dates).
67. **Tests** : 453 tests unitaires et d'intégration (mypy strict, ruff) + 25 E2E avec un vrai LiveKit. Les E2E ont révélé et fait corriger un défaut d'interface : une fin de réunion survenant pendant l'activation de la caméra et du micro pouvait laisser un participant dans une salle fantôme.

**Précision issue de la recette (T3, point 9)** : le test « `PATCH` sur une réunion en cours » est refusé par `409 meeting_already_started`, conformément au point 10 (§6 ter) : une réunion est « commencée » dès `starts_at` atteint ou dès la première connexion. La modification de créneau se teste donc sur une réunion **pas encore commencée** (créée avec `-DansMinutes 30 -DureeMinutes 7`, puis `PATCH` de `starts_at` et `ends_at`). Aucun changement de code.

**Écarts au plan, en bref** : pas de SDK serveur LiveKit (61) ; aucun jeton pour la salle d'attente (53) ; organisateurs sans `roomAdmin` (49) ; chat par le backend (60) ; exclusion par entrée d'accès plutôt que par liste de `jti` (50).

**Vérifié par toi (manuel, 2026-11-04)** : lancer `livekit-dev.ps1` (il pointe désormais le webhook sur `localhost:8000`), le backend, et `deploy/dev/webhook-receiver.py` avec `WEBHOOK_URL=http://localhost:9000/hooks/visio` ; jouer l'organisateur et deux participants (couper un micro, exclure, admettre, tout admettre), laisser un créneau court finir, puis lire `/attendance` et `/chat`.

## 6 nonies. Étape 13 — Docker de développement : ce qui est codé et ce qui est à confirmer

**13a (validée le 2026-11-04)** : `deploy/docker-compose.dev.yml` (LiveKit 1.13.11 + Redis) piloté par `deploy/dev/docker-dev.ps1` (`up`, `status`, `logs`, `down`, `reset`). La configuration LiveKit est générée à chaque `up` avec la clé de `.env.development` (dossier `deploy/dev/.generated/`, ignoré par Git). Constaté : le webhook LiveKit (Docker) → backend (`host.docker.internal:8000`) fonctionne, la présence est enregistrée.

**13b — S3 local et couche de stockage (validée le 2026-11-04)**

68. **SeaweedFS à la place de MinIO pour le S3 de développement.** *Écart au plan* (« MinIO (S3 de dev) »). Raison : MinIO a supprimé `minio/minio` et `minio/mc` de Docker Hub en septembre 2026 et son dépôt est archivé ; un `docker compose up` neuf échoue au téléchargement et plus aucune correction de sécurité n'est publiée. SeaweedFS (image `chrislusf/seaweedfs:4.06`, Apache 2.0) offre la même API S3 dans un seul conteneur, et supprime la contrainte AGPL de la liste des licences. Le code n'est pas concerné : il parle S3 standard, l'endpoint vient de `S3_ENDPOINT`.
69. **Configuration S3 de dev** : l'identité de SeaweedFS est générée par `docker-dev.ps1 up` à partir de `S3_ACCESS_KEY` / `S3_SECRET_KEY` de `.env.development` (même principe que LiveKit). La passerelle écoute sur le port 9000 : `S3_ENDPOINT=http://localhost:9000` (valeur par défaut de `env-init`) fonctionne tel quel. Ports liés à `127.0.0.1`.
70. **`services/storage.py`** : un seul point d'accès au S3 (`put`, `get`, `exists`, `delete`, `bucket_exists`, `ensure_bucket`), client `boto3` (nouvelle dépendance d'exécution) en adressage « path » et signature v4. Clés d'objet validées (pas de chemin absolu ni de `..`). Les erreurs ne contiennent jamais de clé d'accès. `delete` est rejouable (le S3 répond 204 même si l'objet n'existe plus) : c'est la base qui dira si l'artefact existait (étape 15). Branché dans l'application (`app.state.storage`) sans connexion au démarrage.
71. **`uv run task s3-init` / `s3-check`** : `s3-init` crée le bucket de dev (attend jusqu'à 30 s que le S3 démarre ; **refuse en production**, où le bucket est fourni par Eduvia). `s3-check` écrit, relit et supprime un objet de test, vérifie qu'il n'existe plus, et que le bucket est **privé** (accès sans signature refusé). Constat utile : hors `us-east-1`, un S3 de type AWS exige une `LocationConstraint` à la création du bucket ; elle est ajoutée.
72. **Tests** : 19 nouveaux tests d'intégration contre un vrai serveur S3 HTTP local (moto, en développement uniquement), donc sans Docker ; le vrai S3 de dev se vérifie avec `s3-check`.

**13c — Egress (enregistrement) : validée le 2026-11-04**

73. **Egress partage le réseau de LiveKit en dev** (`network_mode: "service:livekit"`). En dev, LiveKit annonce `127.0.0.1` aux clients WebRTC (`node_ip`, point 13a) ; le Chrome d'Egress, dans un conteneur à part, ne pourrait pas s'y connecter (127.0.0.1 serait lui-même). Partager le réseau règle cela sans changer la config LiveKit ni exposer de port. Conséquences : pas de `ports:` sur Egress, et Egress doit être recréé avec LiveKit (`docker-dev.ps1 up` le fait). **Dev uniquement** : en production (étape 18 et 19) LiveKit annonce son IP publique et Egress est un service normal.
74. **Configuration Egress générée** (`deploy/dev/.generated/egress.yaml`, ignoré par Git) par `docker-dev.ps1 up`, avec la clé LiveKit et les clés S3 de `.env.development` : même principe que LiveKit et le S3. La section S3 est écrite à deux endroits (`s3:` et `storage: s3:`) car les versions récentes d'Egress lisent la seconde (constaté : avec `s3:` seul, Egress tentait d'écrire en local). Le S3 y est joint par son nom de service (`http://s3:9000`, adressage « path »). **Les identifiants S3 ne transitent jamais par les ordres envoyés à Egress** : ils restent dans sa configuration (testé).
75. **`services/egress.py`** : passerelle Twirp (`StartWebEgress`, `ListEgress`, `StopEgress`), jeton de 60 s avec le seul droit `roomRecord`, même style que `services/rooms.py` (pas de SDK). Elle servira à l'étape 14 pour l'enregistrement des réunions. Le JSON est lu en camelCase ou snake_case, statut en nom ou en nombre, pour ne pas dépendre de la version.
76. **`uv run task egress-check`** : test de bout en bout sans participant. Enregistre 5 s de la page d'accueil de LiveKit (`http://127.0.0.1:7880`, vue depuis Egress), attend la fin, vérifie que le fichier est sur **le S3 que lit l'API**, qu'il est non vide et commence par la signature MP4 (`ftyp`), puis supprime l'objet. Attend jusqu'à 90 s qu'Egress s'enregistre après un `up`. N'abandonne jamais un enregistrement en cours. Lancé automatiquement à la fin de `docker-dev.ps1 up`.
77. **Image `livekit/egress` figée à `v1.15.0`** (version constatée sur ta machine avec `egress --version` après un premier `up` en `latest`), comme LiveKit (1.13.11) et SeaweedFS (4.06). Surchargeable par la variable `EGRESS_VERSION`. Egress demande `SYS_ADMIN` et un `/dev/shm` de 1 Go (Chrome), et la doc LiveKit recommande 4 CPU / 4 Go par instance : à garder en tête pour le dimensionnement (étape 20).
78. **Tests** : 16 nouveaux tests d'intégration (passerelle : jeton, pas de secret S3, statuts, erreurs sans fuite ; outil : réussite, attente d'Egress, abandon, échec avec motif, fichier absent / non MP4 / trop petit, nettoyage). Total : 488 tests verts (lint et mypy stricts verts) ; 25 E2E non relancés (inchangés, ils demandent LiveKit).

**Constaté en réel (Docker, 2026-11-04) :** `docker-dev.ps1 up` : les quatre services `healthy`, `egress-check` « Egress OK », MP4 de 21 151 octets arrivé sur le S3 puis supprimé. Deux défauts corrigés au premier essai : le test de santé du S3 était faux (il bloquait `up`), et Egress ignorait la section `s3:` (écriture locale refusée) : d'où `storage: s3:` (point 74). Le partage de réseau (point 73) fonctionne. Si `egress-check` échoue un jour : `docker-dev.ps1 logs egress`.

**Version d'Egress figée (point 77).**

**Non-régression contre LiveKit Docker (fin de l'étape 13)**

**Non-régression constatée le 2026-11-04 : 25 E2E passés en 1 min 59 s contre LiveKit Docker.**

79. **`uv run task e2e-docker`** : mêmes 25 tests E2E, mais contre le LiveKit du Docker de dev au lieu d'un LiveKit natif lancé par les tests (option `--livekit-docker`, clés lues dans `.env.development` par dotenvx). Le backend de test écoute sur le port **8000** (le webhook de LiveKit y est fixé, `host.docker.internal:8000`) et sur toutes les interfaces ; le test refuse de démarrer si ce port est déjà pris (`dev-lan` à arrêter). `uv run task e2e` reste inchangé (LiveKit natif). Couvre les scénarios 1, 3, 4, 5, 7 et les webhooks contre le vrai LiveKit 1.13.11 en Docker, avec Redis.

Reste pour clore l'étape 13 : non-régression des étapes 6 à 11 contre LiveKit Docker.

## 6 decies. Étape 14 — Enregistrement vers S3 : découpage et choix de conception (à confirmer)

**Découpage.** L'étape 14 est faite en quatre sous-étapes validées une à une (voir `PLAN-PROJET.md`) : 14a démarrer / arrêter, 14b fin + état + webhooks, 14c indicateur + recette navigateur, 14d longues réunions + mesures.

80. **Découpage 14a à 14d** plutôt qu'un bloc unique : quatre couches touchées, validation réelle dépendante de Docker, test de 60 min. Même méthode que l'étape 13 (13a à 13c).
81. **Enregistrement « room composite »** : une seule vidéo MP4 mise en page comme à l'écran, avec l'audio de tous et le partage d'écran (besoin §5). Réglage de départ : 720p à 30 images/s, pour limiter CPU et taille ; à mesurer en 14d, ajustable avant la recette.
82. **Démarrage à `meeting.started`** (première entrée admise), par une tâche `start_recording` dans la table `jobs` (relances, reprise après redémarrage), pas dans la requête du webhook LiveKit. Conséquence acceptée : les toutes premières secondes après la première entrée peuvent précéder le début du fichier.
83. **Arrêt dans `terminate_meeting`, avant `close_room`** : fermer la salle d'abord interromprait Egress brutalement et risquerait un fichier tronqué. Si l'arrêt échoue, la salle est quand même fermée (la fin du créneau prime) et l'artefact est marqué selon le résultat réel d'Egress.
84. **Clé S3 décidée par nous** : `meetings/<id>/recordings/<uuid>.mp4`, créée en base (`artifacts`, statut `pending`, `egress_id`) au démarrage et transmise à Egress. Migration `0003` (la chaîne est `0001` → `0002` → `0003` ; le plan parlait à tort de `0005`).
85. **Fin d'enregistrement** : événement LiveKit `egress_ended` reçu sur le webhook signé existant, **plus** un filet de sécurité : le planificateur interroge `ListEgress` pour tout artefact encore `pending`. Un webhook perdu ou un redémarrage ne laisse donc jamais un artefact bloqué. `complete` → `ready` (taille lue sur le S3 par `head_object`, présence de l'objet vérifiée) ; `failed` / `aborted` / `limit_reached` → `failed` avec motif.
86. **Webhooks sortants** `recording.ready` / `recording.failed` écrits dans la boîte d'envoi dans la même transaction que le changement d'état de l'artefact. Contenu : identifiant d'artefact, taille, durée si connue, motif en cas d'échec. **Ni clé S3 ni URL** : la récupération passe par l'API (étape 15).
87. **Indicateur d'enregistrement** : fourni par LiveKit (`room.isRecording`, vrai tant qu'un Egress est actif). Il ne s'allume donc qu'après le démarrage réel, ce qui est le comportement honnête voulu par le RGPD (besoin §6). L'interface sait déjà l'afficher ; 14c vérifie qu'il n'apparaît jamais avec `recording: false`.
88. **Réunions longues** : hypothèse de travail = un seul MP4. Plan B = segments (une ligne `artifacts` par segment). Décision en 14d selon les mesures (taille, mémoire d'Egress, risque de perte sur 8 h).
89. **Participant « enregistreur »** : Egress rejoint la salle avec une identité qui n'a pas notre préfixe, donc `HUMAN_IDENTITY` l'écarte déjà de la présence (étape 11). À prouver par un test en 14a. À vérifier aussi : il ne doit pas compter dans la limite de 30 participants.

### 14a — ce qui est codé (2026-11-04, validé le 2026-11-04)

90. **Préparation à `meeting.started`, démarrage par tâche** : `recording.schedule_start` crée, dans la même transaction que le passage à `live`, l'artefact `recording` (`pending`, clé S3 opaque `meetings/<id>/recordings/<uuid>.mp4`) et une tâche `start_recording`. Le planificateur l'exécute ; mêmes relances que la fermeture de salle (5 s, 30 s, 2 min, 10 min, 1 h), puis abandon : l'artefact passe à `failed` (« démarrage impossible : … »). `recording: false` → ni artefact, ni tâche, aucun appel à Egress.
91. **Un seul enregistrement par réunion** : une relance commence par chercher l'enregistrement actif de la salle (`ListEgress` avec `room_name` + `active`) et l'adopte s'il existe (réponse d'Egress perdue après un démarrage réussi). Un webhook de présence rejoué ne crée rien de plus.
92. **Arrêt dans la tâche `close_room`, avant `DeleteRoom`** (et non dans `terminate_meeting` directement) : l'ordre est ainsi garanti aussi lors d'une relance ou après un redémarrage. Un arrêt qui échoue est journalisé et la salle est fermée quand même (point 83). Si l'enregistrement n'avait pas encore démarré, la tâche `start_recording` est annulée et l'artefact passe à `failed` (« la réunion s'est terminée avant le démarrage »). Vaut pour les trois chemins de fin : fin de créneau, API, organisateur.
93. **L'artefact reste `pending` après l'ordre d'arrêt** : c'est 14b qui constate la fin d'Egress (`ready` / `failed`) et envoie les webhooks. Le point d'accroche est marqué dans `recording._fail`.
94. **`uv run task recordings-list`** (lecture seule) : liste les MP4 du S3 avec leur taille et l'état de l'artefact en base, et signale les artefacts sans fichier. `nouvelle-reunion.ps1 -Enregistrement` crée une réunion de test enregistrée.
95. **Gabarit de mise en page d'Egress : pas de dépendance externe (correction de ma première hypothèse).** Vérifié dans le code d'Egress 1.15.0 : sans `template_base`, la page de mise en page est servie **localement** par Egress (`http://localhost:<port>/`), pas par un site LiveKit. Rien à auto-héberger, et pas de contenu mixte (page `http`, WebSocket `ws`).
    **Comment l'enregistrement démarre vraiment** : le gabarit ne donne le signal de départ (`START_RECORDING`) qu'une fois qu'il a **reçu au moins une piste** d'un participant (vidéo décodée, ou au bout de 5 s s'il y a des pistes abonnées). Si personne ne publie de piste pendant toute la vie de la salle, Egress reste en attente puis se termine en `aborted` (« Start signal not received », code 412) quand la salle est fermée : aucun fichier. À traiter en 14b : cet état doit donner un artefact `failed` avec un motif lisible (« aucune piste audio/vidéo publiée »), distinct d'une vraie panne.
96. **Tests** : 17 nouveaux (16 dans `test_recording.py`, 1 dans `test_storage.py`). Un test de l'étape 10 adapté (il supposait « une seule tâche en base » ; il filtre maintenant `close_room`). `FixedClock.advance` ajouté. **505 tests verts**, `ruff` et `mypy` stricts verts. 25 E2E non relancés (Docker requis, code E2E inchangé).

**Checklist manuelle 14a (à faire sur ta machine)**
1. `uv run task db-upgrade` (migration `0003`).
2. `.\deploy\dev\docker-dev.ps1 up` : 4 services `healthy`, « Egress OK ». Puis `uv run task dev`.
3. `.\deploy\dev\nouvelle-reunion.ps1 -Enregistrement` ; ouvre le lien organisateur, active micro et caméra, parle ~30 s, partage un écran ~10 s.
4. Pendant la réunion : l'indicateur « Enregistrement en cours » s'affiche ; `docker-dev.ps1 logs egress` montre un enregistrement actif (et regarde s'il contacte un site externe : point 95).
5. Termine la réunion (bouton organisateur), attends ~10 s, puis `uv run task recordings-list` : un MP4 de taille non nulle apparaît (état `pending`, normal avant 14b).
6. Variante `recording` désactivé (lance sans `-Enregistrement`) : aucun indicateur, rien dans `recordings-list`.

**À trancher par toi** (je pars des valeurs proposées si tu ne dis rien) : 81 (720p30), 82 (démarrage à la première entrée), 88 (un fichier d'abord, segments si les mesures l'exigent).

### 14b — ce qui est codé (2026-11-04, validé le 2026-11-05)

97. **Un seul chemin de conclusion, `recording.settle`** (idempotent) : il passe l'artefact `pending` à `ready` ou `failed` selon l'état final d'Egress, et écrit `recording.ready` / `recording.failed` dans la boîte d'envoi dans la même transaction. Deux déclencheurs mènent à lui : le webhook LiveKit `egress_ended` (rapide, route existante) et le filet `recording.reconcile` du planificateur. Un événement rejoué, ou webhook + filet, n'envoie qu'un seul webhook sortant.
98. **`ready` seulement si le fichier existe vraiment** : taille lue sur le S3 (`Storage.size`, `head_object`). Egress `complete` mais objet absent (ou vide) → `failed` (« fichier absent du stockage »). S3 injoignable → l'artefact **reste `pending`** et le filet réessaie : ni faux succès, ni faux échec.
99. **Filet `ListEgress`** : pour les artefacts `pending` d'une réunion terminée depuis **20 s** (`RECONCILE_GRACE`, le webhook arrive normalement avant), un appel `ListEgress` par artefact et par tour tant qu'Egress n'a pas fini. Egress injoignable → on réessaie, ce n'est pas une issue. Egress sans aucune trace de l'enregistrement **15 min** (`LOST_AFTER`) après la fin → `failed` (« aucune nouvelle d'Egress »). Pas d'interrogation pendant la réunion : un Egress qui tombe en cours de route est constaté à la fin.
100. **Motifs lisibles** (`failure_reason`) : « Start signal not received » (point 95) → « aucune piste audio ou vidéo n'a été publiée » ; `aborted`, `limit_reached` et `failed` ont chacun leur formulation. Les échecs de 14a (démarrage abandonné, réunion finie avant le démarrage) émettent maintenant eux aussi `recording.failed` (hook laissé dans `_fail`).
101. **Contenu des webhooks** : `recording_id`, `size_bytes`, `duration_seconds` (durée donnée par Egress, `null` sinon) ; `reason` pour l'échec. Pas de clé ni d'URL (point 86). Documenté dans `WEBHOOKS.md`.
102. **Tests** : 17 nouveaux (dont 1 sur `Storage.size`). `FakeRecorder.finish` et `FakeStorage` simulent la fin d'Egress et le S3. Un test de l'étape 10 adapté (il supposait que `meeting.ended` était le dernier webhook ; `recording.failed` peut maintenant le suivre). **522 tests verts**, `ruff` et `mypy` stricts verts. 25 E2E non relancés (Docker requis, code E2E inchangé).

103. **Vérification en une commande** : `uv run task recording-check [mtg_…] [--wait 90] [--expect failed] [--probe]` contrôle la base, le S3 et la livraison des webhooks `recording.*` (statut `delivered` = le récepteur a répondu 2xx, donc signature acceptée) et rend `OK` / `ECHEC`. `deploy/dev/test-enregistrement.ps1 [-Mode Nominal|SansEnregistrement|SansPiste]` crée la réunion, ouvre le lien, la termine par l'API et lance ce contrôle : seule la parole dans le navigateur reste manuelle. 6 tests ajoutés (528 verts). Le récepteur de dev doit écouter sur le port **9100** (le 9000 est celui du S3 de dev).

104. **Corrigé après la recette réelle (2026-11-05)** : (a) `recording-check` attend la première tentative de livraison au lieu de conclure « non livré » trop tôt ; (b) un ordre d'arrêt refusé parce qu'Egress a **déjà terminé** (il s'arrête seul quand le dernier participant part : 412 `EGRESS_COMPLETE cannot be stopped`) est journalisé en `INFO`, plus en `WARNING`. Constaté en réel : `recording.ready` livré et signé, MP4 h264 + aac, durée cohérente (31,8 s pour 30 s annoncées), 3 anciens artefacts `failed` avec le bon motif « aucune piste » ; réunion sans `recording` : aucun événement `recording.*`.

**Checklist manuelle 14b (à faire sur ta machine)**
1. `docker-dev.ps1 up`, puis `uv run task dev`, et dans une autre fenêtre le récepteur de webhooks (`WEBHOOK_SECRET=… uv run python deploy/dev/webhook-receiver.py`, avec `WEBHOOK_URL=http://localhost:9000/hooks/visio`).
2. `.\deploy\dev\nouvelle-reunion.ps1 -Enregistrement` ; micro, caméra, ~30 s, termine la réunion.
3. Dans les 10 à 20 s : le récepteur affiche `meeting.ended` puis `recording.ready` (taille non nulle, durée proche de la réelle, signature valide). `uv run task recordings-list` montre l'état `ready`.
4. Variante « personne ne publie de piste » (entre sans micro ni caméra, termine) : `recording.failed` avec « aucune piste audio ou vidéo… ».
5. Variante « webhook perdu » : arrête l'API avant la fin, relance-la ~1 min après : l'artefact est tranché au premier tour, `recording.ready` part.

### 14c — ce qui est codé (2026-11-05, à valider)

105. **L'indicateur reste celui de LiveKit** (`room.isRecording`, point 87) : l'interface n'a pas changé. 14c le **prouve** par des tests navigateur (`tests/e2e/test_recording.py`) : (a) `recording: false` → ni avis préalable, ni indicateur, ni artefact, ni événement `recording.*` ; (b) `recording: true` **sans enregistrement réel** (LiveKit natif, pas d'Egress) → l'avis préalable est affiché, l'indicateur ne l'est pas : il ne se déduit jamais de l'option ; (c) avec Egress (point 107) : il s'allume chez les deux participants une fois l'enregistrement réellement démarré.
106. **Défaut trouvé pendant ces tests, corrigé (il datait de 14a et du réseau de l'étape 10).** Le planificateur tenait le verrou d'écriture de SQLite pendant l'appel à Egress (démarrage), à l'arrêt d'Egress et à la fermeture de salle. Si Egress ou le serveur de médias tarde à répondre (conteneur arrêté, Redis lent : jusqu'à 10 s, plus que les 5 s d'attente de SQLite), **toute écriture de l'API échouait en 500 « database is locked »**, y compris `POST /v1/meetings` et les entrées en salle. Constaté en E2E (réunion `recording: true` sans Egress, puis création d'une autre réunion), cause confirmée par un vidage des piles de threads au moment de l'erreur. Correctif : `commit` du compteur d'essais **avant** l'appel réseau dans `recording.run_jobs` et `lifecycle.run_jobs`. 2 tests d'intégration le gardent (une sonde `BEGIN IMMEDIATE` sur une autre connexion pendant les appels `start`, `stop` et `delete_room` ; le premier échoue sans le correctif). À garder en tête pour 14d : un second artefact par réunion (segments) doit rester dans ce schéma (aucune écriture en attente pendant un appel réseau).
107. **Recette réelle automatisée, `uv run task e2e-docker`** (Docker de dev : LiveKit, Egress, SeaweedFS) : `test_scenario_6_recording_with_screen_share_ends_up_on_the_s3`. Deux navigateurs (organisatrice et participante nominative), avis préalable, indicateur chez tous, **partage d'écran** de l'organisatrice vu par l'autre, 120 s de réunion (`E2E_RECORDING_SECONDS=30` pour abréger), fin par le bouton. Contrôles : Egress reste invisible (2 vignettes, 2 lignes de présence) ; `recording.ready` reçu **après** `meeting.ended`, signé, avec les `metadata`, sans `recording.failed` ; puis les contrôles de `recording-check` (un seul artefact, taille en base = taille sur le S3, webhook livré) et de `--probe` (pistes h264 et aac, durée cohérente avec le webhook), plus la signature `ftyp` du MP4. En mode Docker, le backend de test lit le **même S3 que le dev** (réglages `S3_*` de `.env.development` injectés par dotenvx) ; hors Docker, ce test est ignoré (fixture `needs_egress`). Le MP4 est laissé dans `data/recordings/<id>/` pour la recette à l'œil.
108. **Ce qui reste manuel** : juger **à l'œil et à l'oreille** le MP4 (partage d'écran lisible, audio de chacun), car aucun test ne sait dire qu'un écran partagé est « visible ». `deploy/dev/test-enregistrement.ps1 -Mode Recette` prépare la séance (deux fenêtres, consignes pas à pas, lien participant dans le presse-papiers), lance `recording-check --probe`, puis **ouvre le MP4**.
109. **Limites assumées** : `transcript.ready` (scénario 6, seconde moitié) vient à l'étape 16 ; la durée, la taille par heure et le découpage en segments sont l'objet de 14d.
110. **Tests** : 2 d'intégration (point 106) et 4 E2E ajoutés (3 passent avec un LiveKit natif, 1 demande Docker). Dans mon environnement : `ruff` et `mypy` stricts verts, **532 tests verts** ; les 3 E2E natifs passent. **Le test E2E avec Egress n'a pas pu être exécuté chez moi** (pas de Docker) : il est écrit d'après le comportement constaté en 14a/14b et doit être lancé par toi (checklist ci-dessous).

**Checklist manuelle 14c (à faire sur ta machine)**
1. `uv run task check` : vert (532 tests).
2. `.\deploy\dev\docker-dev.ps1 up` (4 services `healthy`, « Egress OK »), **sans** `uv run task dev-lan` lancé (le port 8000 doit être libre).
3. `uv run task e2e-docker` : 28 E2E verts et 1 ignoré (le test « sans Egress », qui n'a pas de sens avec Egress), dont le scénario 6 réel (~3 min). Si le test d'enregistrement échoue : colle-moi le message, et `.\deploy\dev\docker-dev.ps1 logs egress`.
4. Recette à l'œil : lance l'API (`uv run task dev`) et le récepteur de webhooks (voir l'en-tête de `test-enregistrement.ps1`), puis `.\deploy\dev\test-enregistrement.ps1 -Mode Recette` et suis les consignes affichées. Dans le MP4 qui s'ouvre : les **deux phrases** audibles, les deux vidéos, le **partage d'écran lisible**, l'indicateur visible dans les deux fenêtres pendant la séance.
5. Variante `-Mode SansEnregistrement` : aucun indicateur, rien dans `recordings-list`.
6. Relis les points 105 à 110, surtout le 106 (défaut corrigé hors du périmètre strict de 14c).

## 6 undecies. Étape 15 — API artefacts : URL temporaires et suppression (à confirmer)

111. **Téléchargement par le proxy de l'API, jamais par une URL S3** (choix du plan, à valider). `GET /v1/downloads/{jeton}` relit l'artefact en base **à chaque requête**, vérifie qu'il est `ready`, puis diffuse le fichier depuis le S3 privé. Une URL S3 présignée vit jusqu'à son expiration quoi qu'on fasse ; ici, supprimer la ligne en base tue toutes les URL déjà émises, à la seconde (scénario 8). Coût assumé : les octets passent par l'API (flux par morceaux de 1 Mo, jamais en entier en mémoire).
112. **Jeton de téléchargement** (`services/download_tokens.py`) : JWT HS256, claims `aid` (artefact), `use`, `iat`, `exp`. **Clé dérivée** de `LINK_SIGNING_SECRET` (HMAC-SHA256 avec un contexte propre) : un jeton de lien de visio n'ouvre aucun téléchargement et inversement (testé). Expiration comparée à l'**horloge de l'application** (testable), arrondie vers le bas : jamais plus de 15 min, valable jusqu'à `exp` exclu. Même durcissement que les liens (HS256 seul, encodage canonique, taille plafonnée, message unique). Changer `LINK_SIGNING_SECRET` invalide aussi les URL en cours (15 min au plus).
113. **Aucune clé d'API sur `/v1/downloads/{jeton}`** : le jeton fait foi, pour que le backend d'Eduvia puisse transmettre l'URL à un navigateur. HTTPS exigé en production comme ailleurs. Réponse `Cache-Control: private, no-store`, `Referrer-Policy: no-referrer`, `nosniff`, `Content-Disposition: attachment`.
114. **Codes d'erreur** : jeton falsifié ou inventé → `404 invalid_download_link` (message unique) ; authentique mais périmé → `410 download_link_expired` ; artefact supprimé → `410 artifact_deleted` ; fichier absent du S3 alors que la base dit « prêt » → `404 artifact_file_missing` (journalisé en ERREUR : incident d'exploitation) ; S3 injoignable → `502 storage_unavailable` ; artefact pas prêt → `409 artifact_not_ready`.
115. **`Range` et `HEAD`** : une plage simple est servie en `206` (reprise d'un téléchargement de plusieurs centaines de Mo, lecture vidéo). Plage hors du fichier → `416` avec `Content-Range: bytes */taille`. Plusieurs plages, unité inconnue ou syntaxe invalide → ignorées, fichier entier en `200` (permis par la RFC 9110). ETag fort = identifiant de l'artefact (le contenu ne change jamais) ; `If-Range` qui ne le reprend pas → plage ignorée. `HEAD` : mêmes contrôles et en-têtes, sans corps (non documenté dans OpenAPI).
116. **Le flux est ouvert avant d'envoyer les en-têtes** : si le S3 refuse, l'appelant reçoit une vraie erreur JSON, pas un téléchargement coupé en route. Limite connue : une suppression qui survient pendant un téléchargement déjà commencé peut le laisser aller à son terme ; toute nouvelle requête (même `Range`) est refusée.
117. **`GET /v1/meetings/{id}/recordings`** : liste (plus ancien d'abord) avec statut (`pending`, `ready`, `failed`), taille, motif d'échec et, si `ready` seulement, `download_url` + `download_url_expires_at` (heure de Paris). Chaque appel émet une URL neuve. Réunion inconnue → `404 meeting_not_found` ; réunion sans enregistrement → liste vide. Jamais de clé d'objet, d'endpoint ni de bucket dans les réponses (testé).
118. **Ajout non demandé : `GET /v1/recordings/{id}`.** Le webhook `recording.ready` donne `recording_id` : cette route donne directement une URL fraîche, sans repasser par la liste. À retirer si tu préfères rester au plus près du PDF.
119. **`DELETE /v1/recordings/{id}` → `204`.** Ordre : (1) l'artefact existe et est du bon type ; (2) il n'est pas `pending` ; (3) **purge S3** puis vérification que le fichier a disparu ; (4) seulement alors, suppression de la ligne. Si le S3 échoue ou ne confirme pas : `502`, **base intacte**, appel rejouable : jamais de fichier orphelin dont plus rien ne parle (RGPD). Inexistant ou déjà supprimé → `404 artifact_not_found` (comme le demande le plan).
120. **Refus de supprimer un enregistrement `pending`** (`409 artifact_not_ready`) : Egress ou la fusion des segments écrit encore dans le S3, supprimer laisserait un orphelin. Un artefact `pending` devient toujours `ready` ou `failed` (filet de 14b, abandon des fusions), puis se supprime. Un artefact `failed` se supprime : il peut avoir laissé des segments.
121. **La purge supprime tout ce qui porte le chemin opaque de l'artefact** (`Storage.purge`, `purge_prefix`) : `…/<uuid>.mp4`, le manifeste qu'Egress écrit à côté (`.json`), les segments non fusionnés (`<uuid>/seg_*.ts`). Garde-fou : si le nom n'est pas un UUID, seule la clé exacte est visée, jamais un préfixe large ; un UUID ayant une longueur fixe, un préfixe ne déborde jamais sur un autre artefact (testé, y compris sur la même réunion).
122. **Bucket versionné** : si le bucket garde les versions (`Enabled` ou `Suspended`), `purge` supprime **toutes les versions et marqueurs** (un simple `delete` laisserait l'enregistrement récupérable : testé). Si la clé d'accès ne peut pas lire le versionnement, on suppose « non versionné » et on le journalise. **À savoir pour l'exploitation** : la clé S3 d'Eduvia doit pouvoir lister et supprimer les versions (`s3:ListBucketVersions`, `s3:DeleteObjectVersion`) si leur bucket est versionné, et aucune règle de verrouillage d'objet ne doit s'appliquer, sinon « supprimé » serait impossible (la suppression échoue alors proprement en `502`).
123. **Un seul mécanisme pour les artefacts** (`services/artifacts.py`) : liste, URL, téléchargement et suppression sont indépendants du type (`ArtifactKind`) ; type de contenu et nom de fichier viennent de l'extension de la clé. L'étape 16 n'aura qu'à brancher `GET` / `DELETE /meetings/{id}/transcript` dessus (testé avec un artefact `transcript`). Un jeton désigne **un** fichier : si l'étape 16 décide de plusieurs fichiers par transcription (JSON, SRT, VTT), le jeton recevra une variante de format.
124. **Audit** : la suppression écrit une ligne de journal (`artefact supprimé`, ids d'artefact et de réunion, nombre d'objets) sans clé ni URL ; chaque téléchargement aussi, sans le jeton. Le jeton figure toutefois dans le **journal d'accès d'uvicorn** (chemin de l'URL), comme déjà celui des liens de visio : limiter l'accès aux journaux ou lancer uvicorn avec `--no-access-log` en production (à voir à l'étape 17).
125. **Aucune migration, aucune nouvelle dépendance.** `FakeStorage` devient un S3 en mémoire complet (contenu, plage, purge, panne simulée) et respecte le protocole `ObjectStore` ; `_is_canonical` de `links.py` devient `is_canonical_token` (partagé avec les jetons de téléchargement).
126. **Tests** : 161 de plus, **698 verts**, ruff et mypy stricts verts. `test_byte_range.py` (33), `test_download_tokens.py` (39), `test_artifacts.py` (73, S3 en mémoire), `test_artifacts_s3.py` (16, vrai serveur S3 HTTP via moto : plages, purge, bucket versionné, scénario 8, outil). **Non couvert ici** (pas de Docker) : le vrai SeaweedFS et un vrai MP4 d'Egress, d'où `artifacts-check`.

**Checklist manuelle 15 (à faire sur ta machine)**
1. `uv run task check` : vert (698 tests).
2. `.\deploy\dev\docker-dev.ps1 up`, `uv run task dev`, puis produis un enregistrement réel (`.\deploy\dev\test-enregistrement.ps1`, ou `nouvelle-reunion.ps1 -Enregistrement`, parle 30 s, termine la réunion) : `uv run task recordings-list` doit montrer l'état `ready`.
3. `docs/exemples.http`, section 11 : `GET …/recordings` → note `download_url` ; ouvre-la dans le navigateur (le MP4 se télécharge, sans clé) ; recharge après 15 min → `410 download_link_expired`.
4. `uv run task artifacts-check` (lecture seule) : tous les contrôles `OK`, dont « accès direct au S3 sans signature : 403 ».
5. **Scénario 8** : copie une `download_url` fraîche, puis `uv run task artifacts-check --delete` (⚠ détruit l'enregistrement) : `DELETE 204`, objet absent du S3, URL déjà émise → `410 artifact_deleted`. Recolle ta `download_url` copiée dans le navigateur : `410`.
6. Variante « en cours » : `DELETE` d'un enregistrement `pending` → `409 artifact_not_ready`.
7. Relis les points 111 à 126, surtout 111 (proxy), 118 (route ajoutée), 120 (refus si `pending`) et 122 (bucket versionné : à vérifier avec Eduvia).

## 6 duodecies. Étape 16 — Transcription : découpage et 16a (à confirmer)

Découpage de l'étape 16 en quatre sous-étapes (voir `PLAN-PROJET.md`) : 16a formats, stockage et API ; 16b pistes audio par participant ; 16c worker faster-whisper ; 16d mesures et qualité.

### 16a — ce qui est codé (2026-11-05, à valider)

127. **Une transcription par réunion.** Un seul artefact `transcript` par réunion ; la relancer réutilise la même ligne et les mêmes noms de fichiers (jamais de doublon après une reprise).
128. **Quatre fichiers, un seul nom opaque** : `meetings/<id>/transcripts/<uuid>.{json,txt,srt,vtt}`. Le JSON est la référence (`storage_key`) ; la purge par préfixe de l'étape 15 supprime les quatre d'un coup. Ordre d'écriture : txt, srt, vtt, puis json, puis la base : jamais de transcription « prête » à qui il manque un fichier.
129. **Pas d'URL temporaire pour la transcription** : ce sont de petits fichiers texte, l'API les lit sur le S3 et les renvoie directement (`?format=`), derrière la clé d'API. Le bucket reste privé et rien du S3 n'apparaît dans les réponses. La suppression est immédiate : plus rien à servir. (Le plan prévoyait « même mécanisme » : c'est la même suppression et la même sécurité, sans jeton, puisqu'il n'y a pas d'URL à faire vivre.)
130. **Temps en secondes depuis le début de l'enregistrement**, avec `started_at` (heure de Paris) dans le JSON pour retrouver l'heure réelle d'une phrase.
131. **Fusion déterministe** : tri par début, fin, nom ; le résultat ne dépend pas de l'ordre de traitement des participants. Segments vides écartés, texte ramené à une ligne.
132. **Réunion sans parole** : transcription `ready` et vide (JSON sans segments, SRT vide, VTT réduit à `WEBVTT`), pas une erreur.
133. **Réponses** : `pending`/`failed` donnent `200` avec l'état en JSON, mais `409 artifact_not_ready` pour un export ; `404 artifact_not_found` s'il n'y a pas de transcription ; `DELETE` refusé (`409`) tant qu'elle est en cours.

### 16b — ce qui est codé (2026-11-05, validé)

134. **Une piste par micro publié, enregistrée à part** (Egress « track », Opus dans un fichier `.ogg`, sans transcodage) : le locuteur d'un fichier est connu avec certitude, sans reconnaissance de voix. Déclencheur : le webhook LiveKit `track_published` (nouveau cas du webhook existant, même signature).
135. **Le nom vient du lien** : nom et `external_id` sont ceux de la ligne d'accès (`access_entries`), figés à la publication. Une personne non admise, ou sans ligne d'accès, n'est pas enregistrée.
136. **Seulement si `transcription: true`**, seulement les micros (ni caméra, ni partage d'écran, ni son du partage), seulement les personnes (pas l'enregistreur ni un agent).
137. **Une ligne par piste, pas par personne** : quelqu'un qui coupe puis rouvre son micro, ou se reconnecte, a plusieurs pistes ; `track_sid` est unique (webhook rejoué = pas de doublon). Table `audio_tracks` (migration `0004`).
138. **Même fiabilité que l'enregistrement** : tâche `start_track` avec relances (`JOB_RETRY_DELAYS`), `egress_ended` pour conclure, filet du planificateur si le webhook est perdu, arrêt des pistes **avant** la fermeture de la salle. Une piste en échec n'empêche ni la réunion, ni l'enregistrement, ni la transcription des autres.
139. **Chronologie** : `started_at` = instant de publication, remplacé par le début réel du fichier annoncé par Egress ; `tracks.offset_seconds` donne le décalage de chaque piste (utilisé par le worker, 16c).
140. **Fichiers de travail** : `meetings/<id>/audio/<uuid>.ogg`, supprimés après la transcription (`tracks.purge`, S3 d'abord puis base) ; ils ne sont jamais exposés par l'API (RGPD : seule la transcription reste).
141. **Limite connue** : si la réponse d'Egress au démarrage d'une piste se perd, la relance peut ordonner une seconde fois la même piste (Egress n'offre pas de moyen simple de retrouver un « track egress » par piste). À observer pendant la recette réelle.

### 16c — ce qui est codé (2026-11-05, validée 2026-11-06)

142. **Deux processus, une seule file.** L'API (planificateur) se contente de **créer** la tâche `transcribe` (`transcription.enqueue_due` : une par réunion terminée avec `transcription: true`, jamais deux, rattrape tout après un redémarrage). Le calcul tourne dans un **processus à part**, `uv run task transcriber` (`workers/transcriber.py`) : un calcul de plusieurs minutes ne doit pas ralentir l'API, et ce processus peut tourner sans accès à Internet. **Un seul worker à la fois** (SQLite : deux workers se disputeraient les tâches).
143. **Moteur : faster-whisper en dépendance optionnelle** (`uv sync --extra transcription`), seul `services/whisper_engine.py` le connaît (import paresseux ; l'API et les tests n'en ont pas besoin). Réglages `TRANSCRIPTION_MODEL` (défaut `medium`, à trancher en 16d selon les mesures), `TRANSCRIPTION_MODEL_DIR`, `TRANSCRIPTION_DEVICE` (`auto`), `TRANSCRIPTION_COMPUTE_TYPE` (`int8`), `TRANSCRIPTION_POLL_SECONDS`.
144. **Aucun appel réseau sortant.** Le modèle est chargé avec `local_files_only=True` et `HF_HUB_OFFLINE=1` : s'il manque, erreur claire, jamais de téléchargement. Preuves : test unitaire du chargement ; test d'intégration qui **coupe les sockets** pendant tout le traitement ; `uv run task transcriber-check` qui coupe le réseau du processus, charge le **vrai** modèle et transcrit 2 s de silence. À copier le modèle sur le serveur à la main (étape 18).
145. **Le locuteur vient de la piste, jamais de la voix** (scénario 5) : chaque segment reçoit le nom et l'`external_id` de la ligne `audio_tracks` (donc du lien), puis `domain.transcript.merge` fusionne. Une personne avec plusieurs pistes (micro coupé puis rouvert) apparaît sous un seul nom.
146. **Chronologie** : origine = `started_at` de la réunion (première connexion) ; chaque segment est décalé de `tracks.offset_seconds` (début réel de sa piste). Le JSON garde `started_at` (heure de Paris) pour retrouver l'heure réelle d'une phrase. Écart possible de quelques secondes avec le MP4 (qui démarre à son propre signal) : à mesurer en 16d.
147. **Filtre de silence activé** (`vad_filter`, pas de `condition_on_previous_text`) : une piste de micro est surtout du silence, et Whisper y invente du texte sans ce filtre. Qualité réelle à juger en 16d.
148. **Attente des pistes** : tant qu'une piste n'est pas tranchée (`pending`), la tâche attend 10 s (sans compter d'essai) ; au bout de 2 h après la fin de la réunion, on transcrit les pistes déjà prêtes. Une piste en échec n'empêche jamais la transcription des autres (point 138).
149. **Reprise sans doublon** : la tâche reste due tant qu'elle n'est pas finie ; `transcripts.begin`/`complete` réutilisent la même ligne et les mêmes fichiers. Le drapeau `written` (dans la tâche) évite de **refaire et ré-annoncer** une transcription déjà écrite quand seul le nettoyage audio a échoué. Limite : une coupure en plein calcul refait tout le calcul (pas de reprise piste par piste) ; livraison **au moins une fois** (un crash entre l'écriture et l'annonce peut recalculer, jamais annoncer deux fois).
150. **Échec** : relances `JOB_RETRY_DELAYS` (5 s … 1 h) puis `transcript.failed` (`transcript_id`, `reason` : « transcription impossible : … ») et artefact `failed`. **Les fichiers audio sont alors conservés** pour permettre une relance ; ils restent des données personnelles : leur suppression après échec définitif est à trancher avec Eduvia (proposition : purge manuelle ou après N jours, étape 17).
151. **Webhooks** : `transcript.ready` (`transcript_id`, `language`, `segments`, `speakers`, `duration_seconds`) et `transcript.failed`, signés comme les autres, avec les `metadata`. Voir `WEBHOOKS.md`.
152. **Nettoyage** : audio supprimé (S3 puis base, `tracks.purge`) seulement après l'écriture de la transcription et son annonce. Une réunion terminée **sans aucune piste** (personne n'a parlé ou n'est venu) donne une transcription vide `ready` + `transcript.ready` (point 132), pas un échec.
153. **Tests** : 14 nouveaux (3 unitaires du moteur, 11 d'intégration : scénario 5, fusion, webhook unique, tâche unique, réunion muette, option désactivée, attente des pistes, reprise, nettoyage rejouable, échec définitif, aucun réseau). Dans mon environnement : `ruff` propre, **786 tests verts** (771 + 15). `mypy` : voir le point 154.
154. **Environnement de test sans l'extra** : avec `--extra transcription`, `huggingface-hub` 2.x installe `httpx2`, ce qui change le `TestClient` et fait apparaître 5 erreurs `mypy` dans `test_artifacts_s3.py` (fichier non modifié). On lance donc `uv sync` (sans extra) pour `check`, et `uv sync --extra transcription` seulement pour `transcriber` et `transcriber-check`. Constaté chez Walid : `check` vert sans l'extra (786 tests). **Trouvé à la recette** : `av` 19 est incompatible avec faster-whisper 1.2 (`open() got an unexpected keyword argument 'metadata_errors'`) ; `av>=14,<17` est épinglé dans l'extra. `uv.lock` : 17 paquets ajoutés (faster-whisper et ses dépendances), aucun existant modifié.
155. **Qualité constatée à la recette réelle (Walid, modèle `small`, français forcé)** : attribution des locuteurs correcte (scénario 5 vérifié), mais les mots anglais sont déformés (« Airflow » → « album », « air link »). Réponse : (a) vocabulaire configurable `TRANSCRIPTION_VOCABULARY`, donné au moteur comme contexte (`initial_prompt`) ; (b) outil `uv run task transcribe-file` pour comparer modèles et vocabulaires **sur le même audio**, avec le temps de calcul ; (c) le choix du modèle (`large-v3-turbo` ou `large-v3` contre `small`) se tranche en 16d sur les mesures. Limite connue : le vocabulaire est global ; un vocabulaire par réunion demanderait un champ d'API et une migration (à décider avec Eduvia).

156. **Validation de 16c (2026-11-06).** `transcriber-check` et la recette sur la machine de Walid sont passés : le moteur tourne hors ligne avec les vrais modèles (`large-v3-turbo` et `large-v3` téléchargés dans `data/models/whisper/`). **Modèle retenu pour la suite : `large-v3`** (défaut de `TRANSCRIPTION_MODEL`). Ce n'est **pas** le choix définitif : le besoin (§5) dit « Whisper ou équivalent » sans imposer de modèle, la décision finale est reportée **à la fin du projet**, sur les mesures de 16d.
157. **Comparaison faite à la recette (68 s, CPU, `int8`, français forcé)** : `large-v3-turbo` 29,8 s de calcul, `large-v3` 48,0 s. Qualité très proche ; **les deux se trompent sur « RAG »** (« RAC », « URAC », « rack »), et la phrase anglaise est forcée en français. Comparaison **incomplète** : `small` n'a pas tourné (`--vocab ""` mal transmis par PowerShell), et le vocabulaire donné (Airflow, Python, DevOps) n'était pas celui prononcé (NLP, RAG, chatbots, prompting). À refaire en 16d avec un texte de référence.
158. **Corrigé en passant** : un modèle copié à la main est un dossier simple, pas un cache Hugging Face. `WhisperTranscriber.load` charge maintenant `TRANSCRIPTION_MODEL_DIR/<nom>` quand ce sous-dossier existe (avant, seul `transcribe-file` le faisait, et le worker ne retrouvait pas `large-v3` par son nom). Test unitaire ajouté.
159. **Pistes d'amélioration notées pour 16d** (à mesurer, pas encore codées) : (a) langue non forcée (`language=None`, `multilingual=True`) pour les passages dans une autre langue, le besoin ne demandant que le français ; (b) `hotwords` en plus de `initial_prompt`, avec un glossaire global + par réunion (métadonnées) ; (c) correction après transcription, **auto-hébergée** : rapprochement phonétique des mots peu sûrs avec le glossaire, puis relecture par un LLM local des seuls segments douteux, la version brute étant conservée ; (d) autres moteurs à comparer en fin de projet : Cohere Transcribe (2B, Apache 2.0, mais sans horodatage ni détection de langue), Qwen3-ASR 1.7B (Apache 2.0, 52 langues). Voxtral « Mini Transcribe V2 » est exclu (API seulement). Les chiffres lus sur ces modèles viennent de blogs, aucun n'est mesuré sur du français avec termes techniques.

**Checklist manuelle 16c (à faire sur ta machine)**

1. `uv sync --extra transcription`, puis copie un modèle faster-whisper (ex. `medium`) dans `data/models/whisper` (dossier au format CTranslate2).
2. `uv run task transcriber-check` : « réseau coupé pour ce processus », puis `OK : modèle « medium » chargé et utilisé hors ligne`. Si le modèle manque : `ÉCHEC` clair, sans téléchargement.
3. `uv sync` (sans extra) puis `uv run task check` : tout vert (786 tests, constaté), puis `uv sync --extra transcription` pour la suite.
4. Docker dev + `uv run task dev` + `uv run task transcriber` (second terminal) ; `.\deploy\dev\nouvelle-reunion.ps1 -Transcription`, parle 30 s avec deux personnes, termine la réunion.
5. `uv run task tracks-list` : deux pistes `ready`, puis (après le calcul) plus aucune piste.
6. `docs/exemples.http` : `GET …/transcript` → `ready`, ton nom et ton `external_id` sur chaque phrase (**scénario 5**) ; `?format=srt` lisible.
7. Le webhook `transcript.ready` arrive signé (récepteur `webhook-receiver.py`).
8. Relis les points 142 (deux processus), 146 (origine du temps), 149 (limite de reprise) et 150 (audio gardé après échec).

### 16d — mesures et qualité (2026-11-06)

160. **Règle de mesure.** Le score d'un modèle se juge **sans vocabulaire** : donner au moteur les termes qu'on teste est de la triche, et une vraie réunion n'a pas de liste fermée de termes. Le vocabulaire (`TRANSCRIPTION_VOCABULARY`, `hotwords`) est un bonus à part, jamais un score. Les termes ne servent qu'à **compter** les réussites (`uv run task wer`).
161. **Mesure sur fichier direct** (texte de référence de 167 mots, 16 termes techniques anglais, lu et enregistré par Walid, CPU, `int8`, français forcé, aucun vocabulaire) : `large-v3` **WER 26,3 %**, termes **12/16 (75 %)** ; `large-v3-turbo` **WER 26,9 %**, termes **13/16 (81 %)**. Écart négligeable. Ratés communs : *Vector Databases*, *Embeddings*, *REST APIs* ; *RAG* en plus pour `large-v3`.
162. **Mesure sur une vraie réunion** (deux personnes, navigateur, même texte, `large-v3`) : **WER 65,3 %**, termes **3/16 (19 %)**. Exemples : « machine learning typologique », « le DOM, le STAPI, le Rector Database » (Python, FastAPI, Vector Databases), « IELTS » (React). **Le même modèle fait bien mieux sur un fichier direct : le problème est dans la chaîne audio de la plateforme, pas seulement dans le modèle.** Piste (non prouvée) : `useMeetingRoom.ts` active `echoCancellation`, `noiseSuppression` et `autoGainControl`, qui abîment la parole pour la reconnaissance ; prise de son (micro, pièce, deux onglets sur un même poste) aussi à écarter. Un essai avec un faux micro Chrome n'a rien produit (transcription vide, cause non élucidée) et n'a pas été refait.
163. **Conclusion qualité : insuffisante en l'état.** La porte « qualité jugée suffisante » de l'étape 16 reste **non cochée**. Elle est reportée à la décision de fin de projet (déjà prévue : le besoin §5 dit « Whisper ou équivalent » sans imposer de modèle). À traiter alors : (a) filtres audio du navigateur, (b) glossaire par réunion + `hotwords`, (c) correction après transcription **auto-hébergée** (rapprochement phonétique, puis LLM local sur les segments douteux, version brute conservée), (d) autres moteurs à comparer (Cohere Transcribe, Qwen3-ASR 1.7B ; Voxtral Mini Transcribe V2 exclu car API seulement), (e) langue non forcée pour les passages hors français.
164. **Vitesse et ressources (estimation).** Mesurées sur 66-68 s de parole : `large-v3` 48,0 s de calcul (≈ 0,73 × la durée), `turbo` 29,8 s (≈ 0,44). Estimation pour 1 h de **parole** sur ce CPU : ≈ 44 min (`large-v3`), ≈ 26 min (`turbo`). Le VAD saute les silences, donc une vraie réunion coûte moins, mais il y a une piste par participant, traitées l'une après l'autre. **Non mesuré sur 1 h** : le test de 96 min a été interrompu, aucun résultat. RAM du processus observée entre 1,2 et 4,0 Go (relevés ponctuels, le pic réel peut être plus haut). **Matériel recommandé** : GPU (`float16`) pour la production (jusqu'à 10 réunions simultanées, 30 participants) ; le CPU `int8` suffit pour le développement. CPU/RAM exacts de la machine de test : à compléter.
165. **Scénario 5** : vérifié en réel pour le lien **organisateur** (« Marie Durand », `staff_42` devant ses phrases) et le lien générique (nom saisi, pas d'`external_id`, normal). Le lien **nominatif** est couvert par un test d'intégration, mais pas observé en réel.
166. **Scénario 6** : la partie enregistrement est vérifiée (E2E avec Egress, étape 14c, et `egress-check` du 2026-11-06). La seconde moitié, le webhook `transcript.ready`, est couverte par un test d'intégration (livré une seule fois, signé) ; **sa livraison réelle n'a pas été observée** (le récepteur a démarré après la réunion). Réserve acceptée par Walid.
167. **Outils ajoutés** : `uv run task wer` (WER et termes retrouvés, `src/visio/tools/wer.py`). Pour `transcribe-file`, écrire `--vocab=` (avec le `=`) pour un vocabulaire vide sous PowerShell. `WhisperTranscriber.load` charge `TRANSCRIPTION_MODEL_DIR/<nom>` quand ce sous-dossier existe (point 158).

## 6 terdecies. Étape 17 — Robustesse et observabilité : découpage, 17a à 17d (validée)

Découpage de l'étape 17 en quatre sous-étapes (voir `PLAN-PROJET.md`) : 17a logs structurés ; 17b `/health` enrichi ; 17c jobs en échec visibles et rejouables ; 17d reprise après redémarrage (tests + démo).

### 17a — logs structurés (2026-11-06, validée le 2026-11-06)

168. **Format** (`logging_setup.py`) : un objet JSON par ligne (`ts` UTC avec millisecondes, `level`, `logger`, `message`, champs de corrélation, champs `extra=`, `exception` en texte). `LOG_FORMAT=auto` (défaut) = JSON en production, texte lisible ailleurs ; `LOG_LEVEL` (défaut `INFO`). Le formateur ne lève jamais (valeur non sérialisable → `str`, structure circulaire → champs de base seulement).
169. **Corrélation** : `request_id` et `meeting_id`. Le middleware HTTP (`api/observability.py`, ASGI pur pour que le contexte suive les fonctions synchrones exécutées dans un fil) les pose pour toute la requête ; `meeting_id` vient de l'URL `/v1/meetings/<id>/...`. Le code hors requête (planificateur, worker) passe `extra={"meeting_id": ...}`. Les anciens `extra` (`id`, `meeting`) ont été **renommés `meeting_id`** pour avoir un seul filtre (un test adapté).
170. **`X-Request-ID`** : repris de l'appelant s'il est sain (8 à 64 caractères `A-Za-z0-9._-`), sinon généré (`req_` + 64 bits) ; toujours renvoyé dans la réponse. Permet à Eduvia de retrouver une requête dans nos logs.
171. **Aucun secret dans les logs** : (a) le journal d'accès (`visio.access`) note la **route générique** (`/j/{token}`, `/v1/downloads/{token}`), jamais l'URL brute ni la query string ; une URL inventée est notée `(route inconnue)` (scénario 2) ; (b) le journal d'accès d'**uvicorn est coupé** (il imprime l'URL brute, donc les jetons) ; (c) `httpx` / `httpcore` ramenés à `WARNING` (ils impriment l'URL complète des appels sortants) ; (d) le formateur masque (`***`) tout champ nommé `token`, `secret`, `password`, `authorization`, `api_key` en dernier recours.
172. **Bruit** : `/health` et `/assets/*` sont journalisés en `DEBUG` (sondes Coolify toutes les quelques secondes) ; les réponses `5xx` en `ERROR`.
173. **`configure_logging`** n'installe que **son** gestionnaire (marqué) : idempotent, et ne retire pas ceux d'autres outils (pytest). Appelé au démarrage de l'API (`lifespan`) et du worker `transcriber`.
174. **Limite connue** : les logs du planificateur ne portent `meeting_id` que là où le code le passe en `extra` ; il n'y a pas de contexte par tâche (le rendre systématique obligerait à ré-indenter chaque boucle de tâches). À reprendre en 17c si les tâches en échec en ont besoin.
175. **Tests** : 33 nouveaux (`test_logging_setup.py` unitaire, `test_observability.py` intégration). Total : **829 verts**, `ruff` vert, `mypy` vert hors `tools/chaintest.py` (module `av` seulement installé avec `--extra transcription`).

### 17b — `/health` enrichi (2026-11-06, validée le 2026-11-06)

176. **Deux routes.** `GET /health` : public, **vivant ou non** (`{"status": "ok"}`), ne touche aucune dépendance, pour que la sonde Docker / Coolify (étape 19) ne redémarre pas l'API parce que le S3 est en panne. `GET /v1/health` : derrière la clé d'API, le détail. Le préfixe `/v1` est repris pour que la clé, HTTPS en production et l'OpenAPI s'appliquent comme aux autres routes.
177. **Statuts et code de retour.** `ok` (HTTP 200), `degraded` (503) : LiveKit ou S3 en panne, file de tâches en souffrance ou illisible ; `down` (503) : la base est injoignable. Même corps dans tous les cas, pour lire ce qui ne va pas. Une supervision (Uptime Kuma, etc.) alerte donc sur le seul code.
178. **Sondes.** Base : `SELECT 1`. LiveKit : `ListRooms` avec un jeton d'administration sans salle (méthode `ping()` ajoutée au protocole `RoomGateway`, aucune salle créée). S3 : `head_bucket` (méthode `ping()` ajoutée à `ObjectStore`), **client `boto3` dédié** sans nouvelle tentative et avec des délais de 2 s : avec le client normal (3 essais), un S3 coupé finissait en « délai dépassé » au lieu de « injoignable ». Les quatre lectures tournent **en parallèle** dans des fils, chacune bornée à `CHECK_TIMEOUT_SECONDS` = 3 s (constante de `services/health.py`, pas une variable de configuration) : la route répond en 3 s au plus même si une dépendance se bloque. Mesuré avec les vrais clients vers des ports fermés : 0,4 s, `injoignable` pour les deux.
179. **Egress n'est pas sondé.** Aucun appel de l'API ne prouve qu'Egress tourne : `ListEgress` passe par LiveKit et Redis et répond même sans Egress. Un enregistrement qui ne démarre pas reste visible par les tâches et les artefacts en échec (ci-dessous, puis 17c). À reprendre à l'étape 19 (contrôle de santé du conteneur Egress dans le compose).
180. **File de tâches.** Tâches (`jobs`) : `pending`, `failed` (un essai a échoué, le suivant est programmé), `abandoned`, `oldest_due_age_seconds` (âge de la plus ancienne tâche **due** : en attente ou à reprendre, heure passée), `late`. Webhooks : `pending`, `abandoned`, `oldest_due_age_seconds`, `late`. Artefacts en échec : compteur d'information, **sans effet sur le statut** (un enregistrement sans aucune piste est un échec normal, 14b). `degraded` si une tâche ou un webhook est abandonné, ou si l'un est en retard. Le statut `running` n'existe pas dans le code (jamais écrit) : non exposé.
181. **Seuils de retard.** Une tâche due non prise en charge depuis plus de **2 min** est en retard (le planificateur tourne chaque seconde) ; **30 min** pour `merge_recording` (la fusion d'une journée prend des minutes) ; **3 h** pour `transcribe` (processus à part, « quelques heures » acceptées au §5 du besoin). Constantes `LATE_AFTER` et `LATE_AFTER_BY_KIND`. Un webhook dû depuis plus de 2 min est en retard (le planificateur ne tourne plus).
182. **Rien de sensible.** La réponse ne contient que des états, des compteurs et des durées : jamais une adresse, un nom de bucket, une clé ni le texte d'une exception. `detail` vaut `injoignable` ou `délai dépassé`. Chaque sonde en échec laisse un `WARNING` (`visio.health`) avec le nom de la dépendance et le **type** d'erreur ; le message n'est consigné que pour nos propres erreurs (`DomainError`, `StorageError`), écrites sans secret, jamais pour celles d'une bibliothèque (SQLAlchemy, boto3).
183. **Journal d'accès.** `/v1/health` est en `DEBUG` comme `/health` (une supervision l'appelle toutes les quelques secondes) ; son 503 est en `WARNING` et non `ERROR` : c'est le message de la route, et la cause est déjà consignée par la sonde. Tout autre 5xx reste en `ERROR`.
184. **Limites connues.** (a) Un fil de sonde abandonné au délai continue jusqu'à la fin de son appel (au plus quelques secondes avec les délais du client) ; une supervision très rapprochée contre une dépendance bloquée peut en empiler quelques-uns. (b) `/health` public ne détecte ni un planificateur mort ni une base coupée : c'est voulu (vivant ≠ en forme) ; le planificateur mort se voit à `/v1/health` par l'âge des tâches dues. (c) La sonde LiveKit prouve que le serveur répond et accepte notre clé, pas qu'une visio fonctionne (réseau média, TURN : étape 18).
185. **Tests.** 54 nouveaux : `tests/unit/test_health.py` (statut global, sondes bornées dans le temps, texte d'erreur jamais exposé), `tests/integration/test_health_detail.py` (route publique indépendante des dépendances, clé d'API et HTTPS, chaque dépendance coupée puis rétablie, base coupée, délai, sondes en parallèle, rien de sensible dans les réponses ni les logs, niveaux du journal d'accès, file de tâches : retards par type, abandons, webhooks, artefacts, sonde LiveKit sur transport simulé), 4 dans `test_storage.py` (`ping`). Total : **883 verts**, `ruff` vert, `mypy` vert hors `tools/chaintest.py` (module `av`). Essai réel avec les vrais clients vers des ports fermés : 503 en 0,4 s.

### 17c — jobs en échec visibles et rejouables (2026-11-06, validée le 2026-11-07)

186. **Deux commandes, une logique.** `uv run task jobs-list [mtg_…]` (lecture seule ; code de retour 0 s'il n'y a rien en échec, 1 sinon : utilisable par une supervision) et `uv run task jobs-retry <id>… | --all [mtg_…]`. Toute la logique est dans `services/replay.py` (testée sans passer par les commandes) ; `tools/jobslist.py` et `tools/jobsretry.py` ne font que l'affichage. **Pas de route d'API** : le plan demande des commandes, et rejouer est un geste d'exploitation, pas d'Eduvia.
187. **Ce qui est listé.** Tâches `failed` (nouvel essai programmé) et `abandoned` ; webhooks `abandoned` ou `pending` avec au moins un échec (relance programmée) ; artefacts `failed` (enregistrements, transcriptions) ; pistes audio `failed`. Chaque ligne : identifiant, type, état, nombre d'essais, prochain essai, erreur (**une ligne, 200 caractères au plus**), identifiant à passer à `jobs-retry`. Jamais le contenu d'un événement, l'URL ni le secret des webhooks, ni une clé d'objet S3.
188. **Rejouer = remettre à zéro, jamais créer.** Tâche : `status=pending`, `attempts=0`, `run_at=maintenant`. Webhook : idem avec `next_attempt_at`. Le planificateur (ou le worker `transcriber`) la reprend au tour suivant. **Aucune ligne n'est ajoutée** et le webhook garde son identifiant d'événement (`X-Visio-Event-Id`) : Eduvia peut dédoublonner. Rejouer deux fois : « déjà en attente » ; tâche terminée : refusé ; webhook déjà livré : **refusé, jamais de second envoi**. Le passage d'état est un `UPDATE … WHERE status = …` : si deux rejeux se croisent, un seul agit.
189. **Artefact ou piste audio → sa tâche.** Un artefact en échec n'a pas de rejeu propre : on rejoue la tâche qui le produit (`start_recording` ou `merge_recording` pour un enregistrement, `transcribe` pour la transcription, `start_track` pour une piste), et l'artefact repasse à `pending` (sans cela `merge_recording` ignorerait la tâche : il saute les artefacts qui ne sont pas `pending`). Un échec **sans tâche à rejouer** (Egress a échoué pendant la réunion, réunion finie avant le démarrage) est définitif : refusé, avec « le direct ne se rejoue pas ».
190. **Démarrage : seulement pendant la réunion.** `start_recording` et `start_track` abandonnés ne sont rejouables que si la réunion est encore `live` ; sinon refus (« la réunion n'est plus en cours ») et **rien n'est modifié**. Une tâche abandonnée dont l'enregistrement a été supprimé est refusée aussi.
191. **`recording.failed` puis `recording.ready`.** Si l'échec avait déjà été annoncé (`recording.failed`, `transcript.failed`) et que le rejeu réussit, Eduvia reçoit ensuite `recording.ready` / `transcript.ready` pour le **même** `recording_id` / `transcript_id`. **Le dernier événement fait foi** ; noté dans `WEBHOOKS.md`. Pas d'événement « annulation de l'échec ».
192. **`--all` n'agit que sur l'abandonné** (tâches, puis webhooks) : ce qui est seulement en relance se rattrape seul, et les artefacts suivent leurs tâches (les rejouer à part compterait deux fois). Hors `--all`, on peut quand même rejouer par identifiant une tâche en relance (ne pas attendre l'heure suivante, la dernière relance est à 1 h).
193. **Transcription.** Le drapeau `written` de la tâche est gardé : une transcription déjà écrite n'est pas recalculée (seule la purge audio est refaite). Les fichiers audio sont conservés après un échec définitif (point 150), donc le rejeu est possible. **Non traité en 17c, à trancher avec Eduvia** : la purge de ces fichiers (données personnelles) après un échec définitif jamais rejoué (proposition du point 150 : purge manuelle ou après N jours).
194. **Corrélation (limite 174 de 17a).** Les logs d'abandon (démarrage d'enregistrement, fusion, piste audio, fermeture de salle, webhook) portent maintenant `meeting_id` ; la transcription l'avait déjà. `jobs-list` affiche le `meeting_id` : on retrouve les logs de la réunion avec un seul filtre. Le rejeu écrit une ligne `INFO` (`visio.replay`, identifiant rejoué).
195. **Limites connues.** (a) Les commandes lisent la **même base SQLite** que l'API : à lancer sur la même machine ou dans le même conteneur (`docker exec` avec Coolify, à documenter à l'étape 19). (b) Rejouer une tâche `failed` **pendant qu'elle s'exécute** peut voir le planificateur réécrire son état à la fin de l'essai ; sans conséquence grave (au pire un essai de plus), mais préférer rejouer ce qui est abandonné. (c) Une fusion rejouée ne vérifie pas que les segments existent encore : s'ils ont disparu, elle échoue jusqu'à l'abandon (six essais, environ 1 h 13 de relances). (d) Pas de journal d'audit du rejeu au-delà de la ligne de log (qui rejoue quoi n'est pas enregistré : un seul opérateur à ce stade).
196. **Tests.** 23 nouveaux dans `tests/integration/test_replay.py` : démarrage d'enregistrement abandonné → listé → rejoué → succès (un seul enregistrement), double rejeu, démarrage refusé réunion finie, échec définitif refusé, identifiants inconnus, fusion abandonnée → rejouée → `recording.ready` une seule fois, fusion qui échoue à nouveau, transcription et piste audio remises en file, webhook abandonné → rejoué → livré une fois avec le même identifiant puis refusé, webhook non encore livré non listé, webhook en relance avancé, `--all` idempotent et limité à l'abandonné, filtre par réunion, affichage borné et sans secret, les deux commandes et leurs codes de retour, `meeting_id` dans le log d'abandon. Total : **906 verts**, `ruff` et `mypy` verts.

197. **Recette automatique : `deploy/dev/test-rejeu.ps1`** (même esprit que `test-sante.ps1`, aucune saisie). Elle crée **sa propre réunion de test** et limite tout à elle : les anciens échecs de la base de dev ne gênent pas et ne sont pas touchés. Parcours : pytest ; **vraie panne** (LiveKit arrêté, une tâche `close_room` posée échoue pour de vrai, `jobs-list` la montre avec son erreur et la commande de rejeu) ; passage à l'abandon **forcé en base** (on ne peut pas attendre 1 h 13 de relances) ; LiveKit rallumé ; `jobs-retry --all` → la tâche se **termine**, sans doublon ; second rejeu refusé ; webhook abandonné **injecté** → rejoué → repris par le planificateur (livré si le récepteur tourne, sinon tentative constatée) ; échec définitif listé mais refusé au rejeu ; aucune fuite (clé d'API, secret et URL de webhook, bucket, `metadata`) dans les sorties. Options `-SansPytest`, `-SansDocker` (échec injecté, déjà abandonné). LiveKit est toujours rallumé et les lignes injectées retirées à la fin. **Écrite sans pouvoir l'exécuter** (pas de PowerShell ni de Docker dans mon environnement) : premier lancement chez Walid à vérifier ; une erreur de script se corrige, elle ne dit rien du code testé par pytest.

**Vérification manuelle (M), si tu préfères la faire à la main.** (1) Lance l'API puis coupe Egress (`docker stop`) ou mets un mauvais `WEBHOOK_URL` ; (2) fais passer une réunion avec `recording: true` ; (3) `uv run task jobs-list` doit montrer l'échec, son erreur et la commande à copier ; (4) rétablis le service ; (5) `uv run task jobs-retry <id>` puis `jobs-list` : plus rien en échec, et l'enregistrement ou le webhook est arrivé (une seule fois).

### 17d — reprise après redémarrage (2026-11-07, validée le 2026-11-07)

198. **Rien à coder dans l'application : on prouve.** Le planificateur ne garde rien en mémoire (tout se relit en base) et le statut `running` n'existe pas : une tâche interrompue reste `pending` ou `failed`, donc due, et le tour suivant la reprend. 17d ajoute donc les **tests de redémarrage** et la démo Docker, sans changer le code de `visio/`.
199. **Simulation d'un redémarrage.** `with TestClient(app) as restarted:` ouvre une **nouvelle vie de l'application** sur la même base, la même horloge simulée et les mêmes faux serveurs (médias, Egress, S3, webhooks). Le planificateur de la première vie n'est jamais repris : seul compte ce que la base a gardé.
200. **Scénario 1 : redémarrage pendant une réunion.** Réunion en cours à 16 h 30, API redémarrée : elle reste `live`, un participant peut encore entrer, la salle n'est pas fermée et l'enregistrement n'est pas redémarré (un seul démarrage Egress). À 18 h 01, la nouvelle vie la termine, ferme la salle, refuse les entrées, et `meeting.ended` n'est annoncé qu'une fois. Variante : fin de créneau pendant une panne du serveur de médias, l'ordre `close_room` reste en base, puis il est exécuté **une seule fois** après le redémarrage.
201. **Scénario 2 : redémarrage après la fin d'un enregistrement.** Egress termine pendant l'arrêt de l'API (webhook perdu) : au redémarrage, l'artefact passe `ready` avec sa taille, **même objet S3**, l'API le liste, `recording.ready` part une seule fois, y compris après un second redémarrage (cas déjà couvert en partie par 14b, repris ici de bout en bout).
202. **Scénario 3 : traitement interrompu.** Une fusion qui plante en plein travail (exception non prévue) laisse la tâche `pending` et l'artefact `pending` ; après le redémarrage elle se termine, une seule fois, avec un seul `recording.ready`.
203. **Limites connues.** (a) **Pendant l'arrêt de l'API, personne ne coupe la réunion** : si le créneau finit pendant la panne, les gens déjà connectés restent dans la salle (côté LiveKit) jusqu'au redémarrage, où la coupure est faite avec retard. Aucune nouvelle entrée n'est possible (la jointure passe par l'API). La parade est un redémarrage automatique rapide (`restart: unless-stopped`, sonde `/health` : étape 19). (b) `attempts` est incrémenté **avant** la fusion : un processus qui plante en boucle consomme ses six essais et finit `abandoned`, visible et rejouable (17c). (c) Un plantage entre l'écriture du MP4 et le passage de la tâche à `done` refait la fusion : elle doit rester idempotente (`segments.merge` réécrit la même clé), non vérifié contre un vrai S3.
204. **Tests.** 5 nouveaux dans `tests/integration/test_restart.py` : redémarrage pendant la réunion (maintenue puis coupée à l'heure), redémarrage avant la fin (pas de coupure anticipée), fermeture de salle interrompue puis reprise, enregistrement terminé pendant l'arrêt (conservé, référencé, annoncé une fois), fusion interrompue puis reprise. Total : **911 verts**, `ruff` vert.
205. **Démo Docker automatique : `deploy/dev/test-reprise.ps1`** (même esprit que `test-rejeu.ps1`). Pour chaque conteneur choisi (`livekit` et `s3` par défaut, `-Services livekit,s3,redis` pour d'autres) : arrêt réel, `/v1/health` passe à 503 pendant que `/health` reste à 200, redémarrage, retour à 200, `jobs-list` sans **nouvel** échec (la base de dev garde d'anciens échecs). Le conteneur est toujours rallumé. **Non exécutée de mon côté** (pas de Docker ici) : à lancer sur ta machine.

**Vérification manuelle (M).** (1) Lance l'API puis une réunion de test avec `recording: true` ; (2) connecte-toi, puis **arrête l'API** (Ctrl+C) en pleine réunion et relance-la : tu dois pouvoir revenir avec le même lien et la réunion se coupe à l'heure ; (3) coupe un conteneur au hasard (`docker stop`) pendant la réunion, puis rallume-le : `uv run task jobs-list`, puis `jobs-retry` si besoin ; (4) après la réunion, l'enregistrement est sur le S3 une seule fois.

## 6 quaterdecies. Étape 18 — Réseau : TURN, pare-feu, TLS 443 : 18a, 18b, 18c (validée)

Découpage : 18a configuration TURN et documentation des ports ; 18b relais forcé côté client (diagnostic) ; 18c contrôle réseau depuis l'extérieur. **Rien de ceci ne peut être validé de bout en bout sur ton poste Windows** : le 443 sortant filtré, le certificat du domaine TURN et le scan de ports se vérifient sur le vrai serveur (étape 19) ; voir la porte de validation du plan.

### 18a — config TURN et ports (2026-11-07, validée le 2026-11-07)

206. **TURN intégré de LiveKit** (pas de coturn : une brique de moins, authentifié par les mêmes jetons que la visio : seul un client déjà authentifié par la signalisation obtient un relais). `deploy/network/livekit.turn.yaml` : `turn.enabled`, `tls_port: 443`, `udp_port: 443`, domaine `turn.eduvia.app` **distinct** de celui de l'API et de son propre certificat, `rtc.tcp_port: 7881`, `rtc.udp_port: 7882` (un seul port UDP pour tout le monde : peu de ports à ouvrir), `use_external_ip: true`.
207. **Aucun secret dans ce fichier** (testé) : les clés d'API de LiveKit arrivent par l'environnement au déploiement (étape 19, dotenvx).
208. **Coolify occupe le 443.** Trois montages décrits dans `docs/RESEAU.md` : A, seconde IP pour le domaine TURN (recommandé) ; B, une IP et routage par SNI en passthrough dans Traefik (`tls_port: 5349`, **à valider sur le serveur**) ; C, TLS terminé par un équilibreur (`external_tls: true`). À trancher avec Eduvia selon ce que l'hébergeur propose.
209. **Ports publics** : 443 TCP+UDP, 7881 TCP, 7882 UDP, 80 TCP (certificats). Redis, S3, Egress et l'API de LiveKit (7880) restent internes.
210. **Certificat du domaine TURN** : signé par une autorité reconnue, renouvelé (environ 60 jours) avec rechargement de LiveKit ; le mécanisme (certbot, acme.sh, volume Coolify) est à choisir à l'étape 19.
211. **TURN de dev facultatif.** Par défaut le `livekit.yaml` de dev n'active pas le TURN. `$env:VISIO_DEV_TURN = "udp"` (TURN/UDP sur 3478) ou `"tls"` (TURN/TLS seul sur 5349, domaine `turn.localtest.me`, certificat `mkcert` dans `deploy/dev/.generated/certs`) puis `docker-dev.ps1 up` l'active ; ports de relais 30000-30010/UDP mappés dans le compose. **Non vérifié sur Docker Desktop** (relais, retour par `node_ip`) : un échec local peut venir de Docker et non de la config. Le port 443 lui-même et le certificat du vrai domaine se testent sur le serveur. **Vérifié depuis : voir points 217 à 220.**
212. **Tests** : 6 dans `tests/unit/test_network_config.py` (TURN actif en TLS 443 avec son domaine, certificat fourni sauf équilibreur, UDP et IP publique, aucun secret, chaque port de la config est dans le tableau de `RESEAU.md`, aucun service interne listé comme public). Le test lit le YAML avec `pyyaml`, déjà présent via les dépendances de dev (`uv.lock`) mais **non déclaré** : à déclarer si tu veux t'en passer.

### 18b — relais forcé côté client (2026-11-07, validée le 2026-11-07)

213. **`?relais=1`** dans l'URL de la visio impose `iceTransportPolicy: "relay"` (`useMeetingRoom.ts`) : aucun trafic direct, tout passe par le TURN. Si la visio marche ainsi, le TURN marche. Sans le paramètre, rien ne change. Aucun effet de sécurité : le TURN reste derrière l'authentification de LiveKit.
214. **Statistiques de connexion** : lues à la main dans `chrome://webrtc-internals` (paire de candidats retenue, type `relay`). Pas d'affichage dans l'interface : l'interface n'a pas de panneau de diagnostic et le plan n'en demande pas.
215. `tsc --noEmit` vert. Pas de test automatique (le relais exige un vrai TURN, point 211).

### 18c — contrôle réseau depuis l'extérieur (2026-11-07, validée le 2026-11-07)

216. **`deploy/dev/test-reseau.ps1 -Domaine turn.eduvia.app [-Hote <ip>]`**, à lancer d'un autre poste que le serveur : 443 et 7881 TCP ouverts ; 6379, 9000 et 7880 **fermés** ; poignée de main TLS sur le domaine TURN (SNI), certificat de confiance, au bon nom, non expiré (7 jours de marge). Les ports UDP ne se testent pas fiablement depuis PowerShell : `nmap -sU` ou la visio avec `?relais=1`. **Non exécuté** (pas de serveur).

**Vérification manuelle sur le serveur (M).** (1) Déployer la config (étape 19) ; (2) `test-reseau.ps1` depuis un autre poste ; (3) depuis un réseau qui ne laisse sortir que le 443 (partage de connexion filtré, ou VM avec règle), ouvrir un lien de visio **avec `?relais=1`** puis **sans** : l'image et le son passent ; (4) `chrome://webrtc-internals` : type `relay`.

### 18 — recette locale sur Docker Desktop (2026-11-07, validée le 2026-11-07)

217. **TURN/UDP (`VISIO_DEV_TURN=udp`).** Le TURN démarre (`Starting TURN server`, `turn.portUDP: 3478`, relais 30000-30010) et Docker publie `3478/udp` et `30000-30010/udp`. Avec `?relais=1`, Chrome reçoit bien un candidat de type `relay` : LiveKit journalise `[remote][trickle] udp relay 192.168.1.2:300xx`. **La visio ne s'établit pas pour autant** : la paire ICE testée par LiveKit (`192.168.1.2:7882` vers le relais) envoie 8 requêtes et reçoit 0 réponse (`state: failed`).
218. **Cause probable (hypothèse, non prouvée) : limite de Docker Desktop, pas de la configuration.** LiveKit et son TURN sont dans le même conteneur, derrière le NAT de Docker ; le trafic relayé doit ressortir puis revenir par l'adresse du poste (`node_ip`), trajet UDP que Docker Desktop sous Windows ne gère pas. Sur un serveur avec une adresse publique ce détour n'existe pas. **Conséquence : le relais de bout en bout n'est pas vérifiable en local.**
219. **TURN/TLS (`VISIO_DEV_TURN=tls`).** Certificat `mkcert` pour `turn.localtest.me` ; LiveKit démarre avec `turn.portTLS: 5349` et le certificat, `Egress OK`. **Non vérifié :** la poignée de main TLS depuis Chrome (pas de visio avec `?relais=1` faite dans ce mode) ; elle ne fait pas partie de ce qui a été recetté en local.
220. **Mode normal et non-régression.** Sans `?relais=1`, la visio fonctionne très bien (candidat `host`), avec ou sans TURN activé ; `Egress OK` (MP4 sur le S3) avec le TURN désactivé, en UDP et en TLS ; retour au mode normal (`VISIO_DEV_TURN` supprimée) vérifié. `test_network_config.py` : 6 tests verts ; suite complète : 917 tests verts ; lint propre.
221. **Reporté à l'étape 19 (serveur réel) :** visio de bout en bout via `?relais=1` (type `relay` retenu), poignée de main TLS du TURN, `test-reseau.ps1`, test « 443 seul » avec les règles du pare-feu Windows (test C), scan de ports, montage du 443 avec Coolify (point 208). La porte « visio fonctionnelle en 443 seul » reste donc **ouverte** dans le plan.

## 6 quindecies. Étape 20 — Tests de charge et dimensionnement (20a à 20d)

> **Statut des chiffres : PROVISOIRES.** Mesurés sur le PC de développement (Docker Desktop, ~7,5 Gio de RAM visibles par Docker) avec le générateur de charge **sur la même machine**. Le test à 300 connexions n'y a pas tenu : à confirmer sur un serveur dédié avant tout engagement contractuel (réserve acceptée par Walid pour 20b, 2026-11-07).
>
> **Correction du 2026-11-08 :** les essais « N × 30 » de cette section comptaient en réalité **26 participants par salle** (`lk` fait publier un micro aux publieurs vidéo : publieurs = max(vidéo, audio), pas leur somme). Le « 10 × 30 » était un **10 × 26 = 260 connexions**. Détail et recalcul : points 248 à 254.

222. **Outil de charge.** `deploy/dev/test-charge.ps1` lance `lk perf load-test` (CLI officielle LiveKit) par salle, relève `docker stats` dans un CSV et appelle `uv run task load-report`. Par défaut 4 vidéos + 6 audios + 20 auditeurs par salle, grille 3×3 : **cas défavorable** (chaque auditeur reçoit 9 vidéos), plus lourd qu'une classe réelle. `-Recorded N` enregistre N salles par la même mise en page qu'en production (`uv run task load-record`). Les clés viennent de la config du conteneur LiveKit (le `.env.development` de Walid donnait `APItestkey`, le serveur `devkey` : écart non élucidé).
223. **Mesures (PC de dev, 2026-11-07).**

| Essai | LiveKit CPU moy / max | LiveKit réseau ↓ / ↑ | LiveKit RAM max | Egress CPU moy / max | Egress RAM max |
|---|---|---|---|---|---|
| 2 × 10, 2 min, sans auditeurs | 10 % / 20 % | 2,5 / 0,1 Mbit/s | 69 Mio | - | - |
| 2 × 30, 5 min, sans enregistrement | 220 % / 246 % | 17 / 98 Mbit/s | 307 Mio | - | - |
| 2 × 30, 5 min, 1 salle enregistrée | 248 % / 303 % | 20 / 56 Mbit/s | 459 Mio | 164 % / 312 % | 1,07 Gio |
| 10 × 30, 33 min, 2 salles enregistrées (1 démarrage refusé : `ReadTimeout`) | 213 % / 821 % | 32 / 47 Mbit/s | 661 Mio | 382 % / 746 % | 1,88 Gio |

224. **Ce que valent ces chiffres.** Fiables : l'ordre de grandeur de LiveKit pour 60 connexions (≈ 2,2 à 2,5 cœurs, 56 à 98 Mbit/s sortants), le coût d'**un** enregistrement (≈ 1,6 cœur en moyenne, 3 en pic, ~1 Gio de RAM), l'absence de fuite visible de LiveKit (+1,7 % sur 33 min). **Non fiables** : tout ce qui vient de l'essai 10 × 30. Le PC était saturé (un relevé `docker stats` sans réponse, `ReadTimeout` d'Egress, débit sortant *inférieur* à celui de 60 connexions : les 300 connexions n'ont pas toutes tenu). **Scénario 9 : non validé sur ce poste**, à refaire sur serveur.
225. **Non couvert par `lk`** : le partage d'écran (à faire à la main) et la transcription (le worker n'est pas dans la salle synthétique) : non mesurés. L'API de la plateforme (`POST /v1/join`) est mesurée à part par `uv run task load-api` (point 230) ; présence et chat ne le sont pas.
226. **Dimensionnement recommandé (provisoire).** Raisonnement : extrapolation linéaire des mesures fiables (60 → 300 connexions = ×5), marge ×1,5 du plan.

| Poste | Calcul | Recommandation |
|---|---|---|
| **LiveKit (CPU)** | 2,2 à 2,5 cœurs × 5 = 11 à 12 cœurs ; × 1,5 ≈ 18 | **16 à 24 vCPU** dédiés au média |
| **Bande passante** | 56 à 115 Mbit/s sortants pour 60 connexions (point 230) ; au plus haut : 115 × 5 ≈ 575 Mbit/s ; × 1,5 ≈ 860 Mbit/s | **1 Gbit/s garanti minimum** (2 Gbit/s conseillé : la marge est faible), trafic non plafonné ; cas défavorable (vraies classes : moins de vidéos, donc moins) |
| **Egress (enregistrement)** | ~4 vCPU / 4 Gio par enregistrement simultané (recommandation LiveKit, point 77, cohérente avec 1,6 à 3 cœurs mesurés) ; « quelques salles » = 3 | **12 vCPU, 12 Gio** pour 3 enregistrements simultanés (+4 vCPU / 4 Gio par enregistrement de plus) |
| **API, front, Redis, S3** | Redis 7 Mio, SeaweedFS 100 à 500 Mio mesurés ; **l'API a été mesurée sur les seules connexions** (point 230) : 100 connexions/s sur un seul processus, sans erreur | **2 vCPU, 4 Gio** (présence et chat non mesurés) |
| **Transcription** | CPU `large-v3` ≈ 0,73 × la durée de parole par piste (164) : trop lent en CPU pour 10 réunions | **1 GPU** (la VRAM nécessaire à `large-v3` en `float16` n'a pas été mesurée ici : à relever sur la machine cible). Le traitement se fait **après** la réunion (file de tâches) : le délai est acceptable, la capacité se règle sur le volume d'heures de parole par jour |
| **RAM totale (hors GPU)** | LiveKit < 1 Gio + Egress 12 Gio + 4 Gio de services | **32 Gio** |

**Total pour un serveur unique : ~32 à 40 vCPU, 32 Gio de RAM, 1 Gbit/s.** Variante conseillée : séparer un **nœud média** (LiveKit, 16 à 24 vCPU, 1 Gbit/s) d'un nœud **services** (Egress, API, S3, Redis, 16 vCPU), et un nœud GPU pour la transcription : l'enregistrement ne vole plus de CPU au média.
227. **Stockage par heure enregistrée (hypothèses à mesurer).** Préréglage Egress `H264_720P_30`, mise en page `speaker`. À un débit de 3 Mbit/s (valeur de `sim-long`) : **≈ 1,35 Go par heure et par salle enregistrée** ; fourchette à retenir 1 à 1,5 Go/h. **Mesuré ensuite (point 230) : 0,18 à 0,53 Go/h, mais avec de la vidéo synthétique : plancher, pas valeur de production ; garder 1 Go/h tant qu'un vrai MP4 d'une heure n'est pas pesé.** Pistes audio par participant (transcription) : ≈ 29 Mo/h par participant à 64 kbit/s (hypothèse, calcul). Transcriptions : négligeables (texte). **Le MP4 réel n'a pas été pesé** : les `____` de 14d restent à remplir avec un MP4 d'une heure d'Egress (`uv run task recordings-list`). Sauvegardes et durée de conservation : décision d'Eduvia (suppression par API, point 111).
230. **Mesures complémentaires du script `test-complet.ps1` (2026-11-07).**

| Essai | LiveKit CPU moy / max | LiveKit réseau ↓ / ↑ | LiveKit RAM max | Egress CPU moy / max | Egress RAM max |
|---|---|---|---|---|---|
| 2 × 30, 5 min, 1 salle enregistrée (2e essai) | 229 % / 286 % | 28 / 115 Mbit/s | 381 Mio | 125 % / 248 % | 770 Mio |
| 5 × 30 (150 connexions), 5 min, 2 salles enregistrées | 308 % / 425 % | 41 / 44 Mbit/s | 883 Mio | 349 % / 656 % | 1,55 Gio |

- **LiveKit CPU confirmé** : 220 %, 248 % et 229 % pour 60 connexions sur trois essais indépendants (≈ 2,3 cœurs), d'où le 16 à 24 vCPU du point 226.
- **Egress** : 125 à 164 % de CPU moyen par enregistrement (248 à 312 % en pic, 0,8 à 1,1 Gio) ; avec 2 enregistrements 349 % / 656 % : cohérent avec ~4 vCPU par enregistrement simultané.
- **Le PC (14 processeurs logiques = 1 400 % de CPU, Docker Desktop à ~7,5 Gio de RAM) ne tient pas 150 connexions + 2 enregistrements**, alors que les conteneurs n'utilisent qu'environ 660 % en moyenne (pic ~1 080 %) : le goulot est **probablement le générateur `lk` lui-même** (CPU de l'hôte non relevé), ou une limite de Docker Desktop ; cause non prouvée. Indice : le débit sortant (44 Mbit/s) est *inférieur* à celui de 60 connexions (115 Mbit/s). Dans les logs de `lk`, 3 × `caught panic in consumeTrack: nil pointer dereference`, vraisemblablement le **générateur de charge** (client) et non le serveur LiveKit (non vérifié, à relire sur serveur).
- **API : 600 `POST /v1/join` sur 600 réussis, 600 admis** (10 réunions × 30 personnes, 2 tours dont un de reconnexions, 100 requêtes simultanées) : latence p50 376 à 485 ms, p95 1,3 s, max 1,9 à 2,5 s ; `/health` reste 200. Seuil de réussite du script : p95 ≤ 2 s (choisi arbitrairement : un premier essai à 1 s a échoué avec p95 = 1,33 s). ≈ 100 connexions/s sur un seul processus ; 300 arrivées **simultanées** non testées.
- **Taille des MP4 : 30 Mo pour 204 s (0,53 Go/h) puis 10 et 11 Mo pour ~198 s (0,18 à 0,20 Go/h).** Vidéo synthétique de `lk`, mise en page `speaker` ; le 2e essai est ~3 fois plus petit : l'Egress saturé a probablement dégradé l'image (images perdues), **à vérifier en regardant les fichiers**. Ce n'est pas une valeur de production.

231. **Étape 21 : Valkey et livrables.** **Valkey 8** (BSD-3) remplace Redis dans les deux Compose (le service garde le nom `redis`) : décision de Walid, 2026-11-07, pour échapper à la licence RSALv2/SSPLv1 des Redis récents ; vérifié en dev (4 conteneurs `Healthy`, Egress OK, charge 2 × 10 identique). Inventaire de licences généré (`uv run task licences`) : seules `certifi` et `tqdm` (MPL-2.0) demandent une décision, acceptées. Webhooks sortants décrits dans l'OpenAPI (`webhook_docs.py`, documentation seule). Contrôle du dépôt `uv run task repo-check` (`.env.production` et `.env.development` vérifiés entièrement chiffrés).

228. **Marge ×1,5 et 8 h (20c) : outillage codé, mesures à faire.** `test-charge.ps1 -Rooms 15 -Recorded 4` (450 connexions) puis `-Rooms 1 -Minutes 480 -Recorded 1 -SampleSeconds 60`. `load-report` signale `FUITE POSSIBLE` si la RAM de LiveKit, Redis ou l'API dérive de plus de 10 % sur plus d'1 h (Egress et S3 sont exclus : leur RAM varie normalement). À faire **sur serveur**.
229. **À refaire sur serveur (liste de contrôle).** (a) 10 × 30 pendant 30 min, 0 déconnexion anormale ; (b) 15 × 30 ; (c) 8 h, 1 réunion ; (d) 3 enregistrements simultanés + partage d'écran à la main ; (e) transcription de plusieurs réunions en parallèle (`transcribe-file` ×N) ; (f) test de charge de l'API ; (g) peser un MP4 d'une heure. Générateur de charge **sur une autre machine** (`-Url ws://<ip>:7880`).

## 6 septdecies. Diagnostic de l'échec du test 10 × 30 et décision sur le scénario 9 (2026-11-08)

248. **Décision : le scénario 9 (10 réunions × 30 participants) est validé sur le serveur d'Eduvia, pas sur le PC de développement.** Le PC de test (Core Ultra 5 235U, puce portable basse consommation ; 15,4 Gio de RAM dont 8 Gio pour Docker Desktop ; générateur de charge `lk` et serveur sur la même machine) n'est pas représentatif d'un serveur. La réserve des points 224 et 229 est **maintenue** ; ce qui suit établit pourquoi et ce qui reste à prouver.
249. **Erreur de comptage corrigée.** Dans `lk perf load-test`, les `--video-publishers` publient **aussi** un micro : les publieurs sont les `max(vidéo, audio)` premiers testeurs. Avec 4 vidéos + 6 micros + 20 auditeurs, une salle compte **26 participants** (lu dans les logs de `lk` : `pub_0` à `pub_5` puis auditeurs, et 52 participants réellement connectés pour 2 salles). Les essais des points 223 et 230 étaient donc des **2 × 26 (52), 5 × 26 (130) et 10 × 26 (260 connexions)**. Recalcul du dimensionnement : LiveKit ≈ 2,3 cœurs pour 52 connexions ≈ **0,044 cœur par connexion** ; 300 connexions ≈ 13 cœurs ; × 1,5 ≈ 20 cœurs, **toujours dans la fourchette 16 à 24 vCPU du point 226** (qui ne change pas). Scripts corrigés : `test-charge.ps1` et `diag-charge.ps1` calculent désormais `auditeurs = participants - max(vidéo, audio)` (une salle = 30 participants exactement).
250. **Outil de diagnostic.** `deploy/dev/diag-charge.ps1` (+ `deploy/dev/diag_rooms.py`) : paliers de 2 à 10 salles, mesure **en même temps** le CPU de l'hôte, les cœurs par processus (`lk`, LiveKit, `vmmem`, `com.docker.backend`), la RAM libre, les participants **réellement connectés** (appel `ListRooms` de LiveKit), les pistes reçues, la perte de paquets (ligne « Total » de `lk`) et les erreurs ; verdict par palier. `-Cible Natif` pour LiveKit sans Docker, `-Url ws://<ip>:7880` pour un générateur sur une autre machine. Fichiers dans `data/diag/<date>-<cible>/`.
251. **Mesures, palier 2 salles (LiveKit Docker, sans enregistrement, 3 min, 2026-11-08).** 52 participants connectés sur 52 ; 200 pistes sur 200 reçues par salle ; 0 erreur ; CPU de l'hôte 78 % en moyenne (86 % au plus) ; LiveKit 2,2 cœurs (`docker stats`) ; `lk` ≈ 2 cœurs ; `vmmem` ≈ 4 cœurs ; **`com.docker.backend` ≈ 3 cœurs** (≈ 1 au repos) ; RAM libre minimale 4,1 Gio. **Perte de paquets 12,8 à 16 %** (lecture de `lk`) : 0 % en audio, 38 à 71 % sur 3 pistes vidéo sur 4 par salle, ≈ 0,5 % sur la 4ᵉ. Le « 13,3 % de pertes » affiché par la première version du script était un faux positif (point 249) ; la perte de paquets, elle, est mesurée mais **n'est pas encore attribuée** (générateur affamé de CPU, couche réseau de Docker Desktop ou serveur).
252. **Lecture et limites.** (a) Les connexions tiennent ; (b) le PC est déjà à ~80 % de CPU à 52 connexions, donc 10 salles sont hors de portée ; (c) Docker Desktop (proxy réseau et WSL2) coûte presque autant de CPU que LiveKit lui-même, ce qui n'existera pas sur un serveur Linux ; (d) l'application n'est pas en cause : l'API tient 600 `POST /v1/join` (point 230), LiveKit est linéaire et sans fuite (point 224), l'enregistrement n'était pas actif dans ce palier. **Cause non prouvée à 100 %** : il reste à comparer sans Docker et avec un générateur sur une autre machine. Limite de mesure : la première version du script lisait le CPU par processus par `TotalProcessorTime` (valeurs entières, trop grossières), corrigée par les compteurs de performance Windows ; les valeurs de `lk`, `vmmem` et `com.docker.backend` du palier 2 sont donc **approximatives**, seules celles de `docker stats` et du CPU hôte sont précises.
253. **Pour clore (sur serveur, avec le point 229).** (a) `diag-charge.ps1 -Cible Docker -Url ws://<ip-serveur>:7880` **depuis une autre machine**, paliers 2, 4, 6, 8, 10 (30 participants par salle) ; (b) si la perte de paquets persiste à faible charge, comparer avec un LiveKit natif et regarder le réseau du serveur ; (c) puis `test-charge.ps1 -Recorded 3` (30 min), et les points 229 (b) à (g). Optionnel sur le PC de dev : `diag-charge.ps1 -Cible Natif -Paliers 2` (retire Docker du chemin réseau, pour mesurer sa part).
254. **Optimisations média déjà en place, rien à changer avant les mesures serveur.** `adaptiveStream` et `dynacast` activés, capture limitée à 720p, simulcast (`web/src/useMeetingRoom.ts`) ; l'enregistreur (`recording/main.ts`) les désactive volontairement. Le dimensionnement reste celui du point 226.

## 6 sexdecies. Correction de conformité : exclusion sur lien générique (2026-11-07)

232. **Écart relevé à la revue de conformité** (besoin §3 et §6 : « un participant exclu ne peut pas revenir avec le même lien »). Avant : sur le lien générique, l'identité dépendait d'un `client_id` fourni par le navigateur ; l'omettre ou vider le stockage permettait de revenir (points 51 et 203 : « limite acceptée »). **Corrigé en deux couches**, sans migration :
    (a) **Cookie d'identité posé par le serveur** (`visio_cid`, `HttpOnly`, `SameSite=Lax`, `Secure` en HTTPS, 2 jours) : il fait foi sur le `client_id` du corps ; vider le stockage local ne change plus l'identité. (b) **Un nom exclu reste exclu** sur les liens génériques de la réunion (comparaison sans accents ni casse, espaces réduits) : fenêtre privée ou cookie effacé refusés tant que le nom est le même.
    **Limite résiduelle assumée** : sur un lien partagé et sans compte, quelqu'un qui efface ses cookies **et** change de nom peut encore revenir ; un lien nominatif reste le seul moyen étanche (recommandé quand l'exclusion compte). Un homonyme exclu avec lui sur le même lien générique est aussi refusé (compromis voulu).
233. **Tests** : 1 test ajouté (`test_an_excluded_person_cannot_return_on_the_generic_link_by_clearing_the_browser`) ; les helpers de test simulent un navigateur par appel. **949 tests verts**, `ruff` et `mypy` verts.
234. **Documents ajoutés** : chiffrage par lot, planning et coût d'infrastructure estimé (documents commerciaux retirés de cette version du dépôt).
235. **Trou trouvé au test manuel, corrigé (2026-11-07).** Le contrôle du nom exclu ne s'exécutait qu'au premier passage (`entry is None`) : un navigateur déjà connu par son cookie (entré sous « Walid ») qui revenait sous le nom exclu « Lina » entrait. Le contrôle se fait désormais à chaque passage sur lien générique (`access.request_access`). Test ajouté : `test_an_excluded_name_stays_excluded_even_from_a_browser_already_known` (il échoue sans le correctif). **Validé à la main** : « Lina » exclue reste bloquée, y compris après être passé par « Walid » dans la même fenêtre.
236. **Noms de fichiers du front neutres** (`web/vite.config.ts`) : `c-`, `e-`, `a-` + empreinte. Avant, `livekit-client.esm-….js` apparaissait dans le code source de la page et faisait échouer `test_nothing_visible_mentions_the_underlying_brick`. **`task e2e` : 28 passés, 1 ignoré (Egress)**.
237. **Transcription : une langue par phrase, sans aucun terme donné au moteur (2026-11-07).** Walid refuse tout glossaire ou vocabulaire pour guider le moteur (« c'est de la triche ») : on améliore le moteur seul. Corpus de test : `tests/corpus/reunion-01` (réunion de synthèse vocale, 154 s, français + 3 phrases anglaises, 35 termes techniques, texte de référence connu). Mesures **sans vocabulaire**, `large-v3-turbo` / `large-v3` : français forcé **WER 31,4 %** (3 phrases anglaises sur 4 supprimées, la 4ᵉ traduite en français) ; `--language auto --multilingual` et `--no-vad` : **identiques** (31,4 %), donc ni la langue détectée globalement ni le filtre de silence ne sont la cause ; **routage par phrase** (VAD, langue choisie entre `fr` et `en`, pause de coupe 0,7 s) : turbo 17,7 %, large-v3 15,3 % (la dernière phrase anglaise était collée à la phrase française précédente) ; **pause de coupe 0,3 s** : large-v3 **WER 6,0 %** (11 substitutions, 5 oubliés) ; **+ double décodage** (`--dual`, chaque phrase dans chaque langue, garde la plus sûre) : **5,7 %**. Les écarts restants sont surtout des écritures équivalentes (« 16h30 », « 30 » pour « trente », « 300 ») et des mots prononcés à l'anglaise par la voix française de synthèse (PostgreSQL, Airflow, chatbot, CI/CD).
238. **Réserves.** (a) Voix de synthèse, une seule réunion, une seule passe : à refaire avec de vraies voix lues par deux personnes. (b) Le seuil de pause (0,3 s) a été réglé sur ce corpus, dont les silences entre locuteurs font 0,6 s : à revérifier sur des voix réelles. (c) `--dual` coûte deux fois plus de calcul pour un gain faible (6,0 % → 5,7 %, et supprime un « Merci. » inventé en fin de fichier) : désactivé par défaut. (d) La chaîne navigateur (WER 65 % mesuré au point 162) reste à traiter séparément.
239. **Dans le code.** `WhisperTranscriber.transcribe_routed`, `merge_spans`, `pick_language`, `mean_logprob` (`whisper_engine.py`). Réglages : `TRANSCRIPTION_LANGUAGES` (défaut `fr,en` ; une seule langue = langue forcée comme avant) et `TRANSCRIPTION_DUAL` (défaut `false`). Le worker de production bénéficie du routage dès qu'il y a au moins deux langues. Outils : `transcribe-file --route fr,en [--gap 0.3] [--dual]`, `wer --show` (textes côte à côte, différences entre crochets).

240. **Lecture humaine de la transcription (2026-11-07).** Règle de Walid : on ne juge pas la qualité sur les chiffres (WER, termes comptés), qui se trompent sur la langue, les nombres et leur écriture ; **on lit le texte transcrit phrase par phrase** contre la référence. « 16h30 », « 30 », « 10 », « 300 » ne sont pas des erreurs ; la casse d'un sigle (« turn » pour TURN) est un défaut mineur. Essai : `uv run task transcribe-file tests\corpus\reunion-01.wav --model large-v3 --route fr,en --vocab=` (aucun terme donné au moteur), voix de synthèse, 155 s, calcul 318 s sur CPU (≈ 2,0 × la durée), chargement 7,7 s.
241. **Résultat de la lecture : 27 phrases transcrites.** 17 exactes (nombres et casse mis à part) ; 3 avec un défaut mineur de casse ou d'écriture (« PostgresQL » ; « turn » pour TURN, deux fois) ; 7 avec une vraie erreur : « ND » ajouté après « backend » ; « C-D » pour CI/CD ; « REE, Flow » pour Airflow ; « chatbatt » pour chatbot ; « Notez » pour « Noté » ; « post-JRSQL » pour PostgreSQL ; un « Merci. » inventé après « Bonne soirée » (n'est pas dans la référence). **Langue : chaque phrase est dans la langue où elle a été dite** (français en français, anglais en anglais), aucune phrase supprimée ni traduite, aucun sens perdu. Les termes qui avaient échoué aux mesures précédentes (RAG, retrieval augmented generation, embeddings, vector database, Kubernetes, Terraform, Grafana, WebRTC, rollback, backlog, sprint, load test) sont **bien transcrits**. Les erreurs restantes sont isolées et touchent des termes techniques anglais prononcés dans une phrase française (CI/CD, Airflow, chatbot, PostgreSQL), ce que les formateurs d'Eduvia signalent justement comme défaut.
242. **Jugement.** Sur ce corpus, **le texte est lisible et fidèle**, la qualité des phrases est bonne ; elle **n'est pas encore jugée suffisante** pour fermer la porte de l'étape 16 : (a) voix de synthèse, une seule réunion, une seule passe (réserve 238a) ; (b) la chaîne navigateur reste à 65 % de WER (point 162) *(levé en partie le 2026-11-08, voir point 245)* ; (c) le « Merci. » inventé en fin de fichier est une hallucination du moteur sur du silence, à surveiller sur de vraies réunions.
243. **Langue : décision à trancher.** Le besoin dit « Langue principale : Français, pour l'interface et la transcription » et « Transcription écrite, en français » (§1, §5) ; il ne dit rien des phrases prononcées dans une autre langue. Mesuré : forcer le français ne traduit pas l'anglais (Whisper ne traduit que **vers** l'anglais) ; il le garde, le supprime ou l'invente selon le modèle (`R-large.txt`, `R-turbo.txt`, point 237). Choix actuel : **routage `fr,en`**, le texte reste fidèle à ce qui est dit. À confirmer avec Eduvia.
244. **Évolution notée : traduction vers le français, activable (pas codée, pas planifiée avant la fin de la qualité).** Idée de Walid : si l'option est activée à la création de la réunion, les segments dans une autre langue que le français sont traduits en français par **un second modèle local**, après la transcription. Conditions pour rester conforme au besoin : (a) **auto-hébergé, aucun service externe** (§5, §6) ; (b) **le texte original est conservé** à côté de la traduction, avec la langue de chaque segment (le JSON gagne des champs, aucun n'est retiré) ; (c) **désactivée par défaut** ; (d) la licence du modèle choisi est listée (§7) et vérifiée pour un usage commercial ; (e) le calcul en plus entre dans le dimensionnement (§7). Whisper ne peut pas servir à cela (sa tâche « translate » ne produit que de l'anglais) : modèle de traduction à comparer (famille MarianMT / NLLB). **Non demandée par le besoin** : ajout optionnel, à proposer à Eduvia avant de coder.

245. **Essai en réunion réelle (2026-11-08) : qualité jugée bonne.** Même texte de référence que le point 161 (français et anglais mêlés), lu par une personne dans le navigateur, réunion créée avec `-Transcription`, `large-v3`, `TRANSCRIPTION_LANGUAGES=fr,en`, **aucun terme donné au moteur**. Jugé **par lecture du texte**, comme au point 240 ; aucun score chiffré n'est retenu (l'export texte mêle les étiquettes d'horodatage et de locuteur au contenu, ce qui fausse le WER). **Constats positifs** : les phrases françaises et anglaises sont fidèles et dans la langue où elles ont été dites ; les termes techniques (Machine Learning, Deep Learning, NLP, RAG, Retrieval-Augmented Generation, knowledge base, FastAPI, React, JavaScript, REST APIs) sont correctement transcrits ; le locuteur est correctement attribué (nom et `external_id`) avec horodatage ; aucun « Merci. » inventé. **Défauts constatés** : (a) une phrase anglaise, coupée par une pause après une virgule, a été transcrite en français (morceau court classé `fr`) ; (b) « embeddings » transcrit « Competing » ; (c) pluriels et casse (« Database », « Model », « LLMS ») ; (d) un petit mot omis (« a »). **Réserves** : une seule lecture, une seule personne, micro propre ; l'écart avec la mesure du point 162 (réunion à deux personnes) n'est pas expliqué (chaîne audio corrigée ou conditions meilleures ?) ; les fins de segment du JSON semblent calées sur des fenêtres fixes (à vérifier pour le SRT/VTT). Le résultat de `wer` sur l'export texte doit être ignoré tant que l'outil ne retire pas les étiquettes `[hh:mm:ss] Nom`.
246. **Écart « qualité de transcription » clos (2026-11-08).** La qualité est jugée **suffisante** pour la suite du projet : texte lisible, fidèle, bonne langue, bons locuteurs, termes techniques corrects dans l'ensemble. Les défauts du point 245 sont des **améliorations**, pas des bloquants. Un contre-essai à deux personnes sur deux appareils (avec `TRANSCRIPTION_KEEP_AUDIO=true`) reste à faire en recette ; il ne rouvre l'écart que s'il montre un net recul.
247. **Améliorations possibles, notées pour la suite (rien n'est codé ni planifié).**
    1. **Routage par langue** : ne pas couper un morceau sur une pause après une virgule quand le morceau précédent est dans la même langue ; essayer `--gap` plus long ou `--dual` (point 239) ; mesurer en lisant le texte. Cause du défaut (a) du point 245.
    2. **Traduction vers le français (point 244)** : second modèle local, après la transcription, uniquement sur les segments non français, texte original conservé, `language` et texte traduit ajoutés au JSON, désactivée par défaut. Whisper ne peut pas le faire (sa tâche « translate » ne produit que de l'anglais), et `whisper-large-v3-french` est un modèle de transcription en français, pas de traduction. Pistes : modèle de traduction léger et rapide sur CPU (famille MarianMT, `opus-mt-en-fr`, convertible CTranslate2) ; un LLM local donne de meilleurs termes techniques mais coûte plus de calcul. Éviter les modèles à licence non commerciale (NLLB). **Licence de tout modèle choisi à vérifier et à ajouter dans `LICENCES.md`.**
    3. **Correction par un LLM local (point 159c)** : relire seulement les segments douteux (confiance faible, `mean_logprob`), corriger les termes mal reconnus (« Competing » pour « embeddings »), la casse et les pluriels. Conditions : (a) **auto-hébergé, aucun service externe** ; (b) **aucun glossaire ni liste de termes donnée** (règle du point 160 : le LLM juge d'après le contexte) ; (c) **le texte brut du moteur est conservé** à côté du texte corrigé, la version corrigée est signalée ; (d) le LLM ne doit ni reformuler, ni traduire, ni ajouter de contenu : il corrige des mots ; (e) désactivée par défaut ; (f) licence et calcul en plus (GPU ou CPU) ajoutés au dimensionnement (§7) ; (g) jugée **par lecture** du texte, brut contre corrigé, sur plusieurs réunions réelles.
    4. **Outil `wer`** : ignorer les étiquettes `[hh:mm:ss] Nom` de l'export texte, pour avoir un score propre (le point 240 reste la règle de jugement).
    5. **Horodatage** : vérifier que les fins de segment correspondent à la fin réelle de la parole (SRT/VTT).
    6. **Hallucination sur silence** (« Merci. ») : à surveiller sur de vraies réunions, filtre à prévoir si elle revient.

## 7. Journal

| Date | Décision | Auteur |
|---|---|---|
| 2026-11-02 | Backend en Python (FastAPI) pour simplifier transcription et modèles IA | Walid |
| 2026-11-02 | Plan en 22 étapes, validation par toi à chaque étape | Walid |
| 2026-11-02 | Docker repoussé à l'étape 13 | Walid |
| 2026-11-02 | Étape 0 validée : les 8 points ouverts (§6) sont acceptés tels que proposés, y compris SQLite et le préfixe `/v1` | Walid |
| 2026-11-02 | Étape 1 validée : squelette FastAPI, `check` vert (4 tests), `/health` et `/docs` constatés | Walid |
| 2026-11-02 | Ancien contenu d'un essai précédent (`backend/`, `infra/`, `docker-compose.dev.yml`) supprimé du dépôt | Walid |
| 2026-11-02 | Étape 2 validée : configuration chiffrée dotenvx, `check` vert (32 tests), `env-check` OK, démarrage refusé sans clé, clé de production régénérée (commit `9d215d4`) | Walid |
| 2026-11-02 | Étape 3 validée : règle du créneau et base SQLite, `check` vert (117 tests), migration `0001` appliquée. Points 9 (marge ≤ 60 min), 10 (« commencée » = première connexion ou `starts_at` passé) et 11 (date sans décalage refusée) acceptés tels que proposés (commit `ed3a89a`) | Walid |
| 2026-11-02 | Étape 4 validée : API réunions `/v1`, `check` vert (221 tests), appels de `docs/exemples.http` constatés (201, 409, 200, 401, 422, 404). Points 12 à 20 de §6 quater acceptés tels que proposés | Walid |
| 2026-11-02 | Étape 5 validée : liens et jetons JWT HS256, `POST /v1/meetings/{id}/links`, `check` vert (285 tests), appels manuels constatés (201 participant nominatif, 201 organisateur sans `external_id`, 422 `validation_error`, 401, 404, 409 `meeting_cancelled`). Points 21 à 29 de §6 quinquies acceptés tels que proposés : pas d'`exp` dans le jeton (22) et pas de route de révocation par API (27) | Walid |
| 2026-11-03 | Étape 6 validée : `POST /v1/join`, jetons LiveKit (droits par rôle, 10 min), page d'essai locale, LiveKit Server 1.13.11 en local, `check` vert (326 tests), deux onglets dans la même salle et refus hors créneau constatés. Points 30 à 37 de §6 sexies acceptés tels que proposés, dont le 31 (jeton de 10 minutes au lieu de « expire à `ends_at` ») | Walid |
| 2026-11-03 | Étape 7 validée : interface de visio React (`web/`) servie par l'API sur `/j/<jeton>`, `POST /v1/join/preview`, marque blanche par `BRAND_*`, textes en français, client embarqué ; `check` vert (353 tests), 14 tests E2E verts (Edge / Chrome déjà installés), checklist manuelle passée. Points 38 à 47 de §6 septies acceptés tels que proposés. Salle complète sur mobile (HTTPS) à reprendre aux étapes 18 et 19 | Walid |
| 2026-11-04 | Étapes 8 à 12 codées d'un seul bloc à la demande de Walid (modération, salle d'attente, fin de créneau, présence et chat, webhooks signés) ; `check` vert (453 tests), 25 E2E verts. Points 48 à 67 de §6 octies à confirmer | Walid |
| 2026-11-04 | Étapes 8 à 12 validées après la recette manuelle (T1 à T8 : modération, salle d'attente, fin de créneau, présence, chat, webhooks, régressions, robustesse). Points 48 à 67 de §6 octies acceptés tels que proposés, dont la limite du lien générique (51) et « refusé = reste refusé » (55). Point 9 de T3 reformulé : le `PATCH` d'un créneau se teste avant le début (point 10). **Fin de la Phase A** : démo complète sans Docker | Walid |
| 2026-11-04 | Étape 13a validée : LiveKit + Redis dans Docker (`docker-dev.ps1`), webhook LiveKit → backend constaté, présence enregistrée | Walid |
| 2026-11-04 | Étape 13b validée : S3 local SeaweedFS à la place de MinIO (point 68, écart au plan accepté), `services/storage.py`, `s3-init` / `s3-check` ; points 68 à 72 de §6 nonies acceptés tels que proposés | Walid |
| 2026-11-04 | Étape 13c validée : Egress dans le compose de dev, `egress.yaml` généré, `services/egress.py`, `egress-check` ; `check` vert (488 tests), constaté sur Docker : 4 services sains, MP4 sur le S3. Points 73 à 78 de §6 nonies acceptés tels que proposés (version d'Egress à figer) | Walid |
| 2026-11-04 | **Étape 13 validée** : Docker de dev complet (LiveKit 1.13.11, Redis, SeaweedFS 4.06, Egress 1.15.0 figée), `egress-check` OK, non-régression : 25 E2E verts contre LiveKit Docker (`e2e-docker`), `check` vert (488 tests). Points 73 à 79 de §6 nonies acceptés tels que proposés | Walid |
| 2026-11-04 | Étape 14 découpée en 14a à 14d (dépend de Docker, quatre couches, test de 60 min) ; points 80 à 89 de §6 decies à confirmer | Walid |
| 2026-11-04 | Étape 14a codée (démarrage et arrêt de l'enregistrement) : `check` vert (505 tests), à valider sur Docker. Points 90 à 96 de §6 decies à confirmer, dont le point 95 (corrigé : pas de dépendance externe) | Walid |
| 2026-11-04 | Étape 14a validée (démarrage et arrêt de l'enregistrement, recette réelle sur Docker) | Walid |
| 2026-11-05 | Étape 14b validée après la recette réelle (`recording.ready` livré et signé, « aucune piste » → `recording.failed`, sans `recording` → aucun événement) ; points 97 à 104 de §6 decies | Walid |
| 2026-11-05 | Étape 14c codée (indicateur prouvé par E2E, scénario 6 réel automatisé, mode `Recette`) ; défaut de verrou SQLite pendant les appels à Egress corrigé (point 106) : `check` vert (532 tests), E2E natifs verts, E2E Egress à lancer sur Docker. Points 105 à 110 de §6 decies à confirmer | Walid |
| 2026-11-05 | Étape 15 codée (API artefacts : liste + URL de 15 min, téléchargement par proxy avec `Range`, suppression qui purge le S3 avant la base) : `check` vert (698 tests), `artifacts-check` à lancer sur Docker. Points 111 à 126 de §6 undecies à confirmer | Walid |
| 2026-11-05 | Étapes 14 (14a à 14d) et 15 validées ; `check` rejoué : 701 tests verts, lint et typage propres | Walid |
| 2026-11-05 | Étape 16a codée (fusion des segments, formats JSON/txt/SRT/VTT, stockage S3 en quatre fichiers, `GET`/`DELETE /v1/meetings/{id}/transcript`) : `check` vert (743 tests). Choix de §6 duodecies à confirmer | Walid |
| 2026-11-05 | Étape 16a validée | Walid |
| 2026-11-05 | Étape 16b codée (une piste audio par micro, `audio_tracks`, migration 0004, `tracks-list`) : `check` vert (771 tests), recette réelle à faire sur Docker. Points 134 à 141 de §6 duodecies à confirmer | Walid |
| 2026-11-05 | Étape 16b validée | Walid |
| 2026-11-05 | Étape 16c codée (worker faster-whisper local, tâche `transcribe`, fusion par nom du lien, `transcript.ready`/`transcript.failed`, `transcriber` et `transcriber-check`) : `ruff` propre, 786 tests verts. Points 142 à 154 de §6 duodecies à confirmer | Walid |

## 14d : enregistrement en segments, fusionnés en un seul MP4 (2026-11-05)

- **Décision :** avec `RECORDING_SEGMENT_SECONDS` > 0, Egress écrit des segments HLS (`.ts`) sur le S3
  au fil de la réunion ; à la fin, une tâche `merge_recording` les recolle **sans réencodage**
  (`ffmpeg -c copy`) en un MP4 unique à la clé annoncée, puis supprime les segments. Le webhook
  `recording.ready` n'est émis qu'après la fusion. Valeur 0 (défaut) : un seul fichier, comme avant.
- **Pourquoi :** un fichier unique de plusieurs heures est perdu en entier si Egress tombe ; en segments,
  seul le segment en cours est perdu. Si Egress s'arrête anormalement mais que des segments existent,
  ils sont fusionnés (enregistrement partiel) au lieu d'être déclarés en échec.
- **Fusion :** boucle de planificateur à part (peut durer des minutes), tâche relancée selon
  `JOB_RETRY_DELAYS`, idempotente (reprise après redémarrage de l'API). Prérequis : `ffmpeg` sur le serveur.
- **Limite connue :** Egress ne reprend pas tout seul l'enregistrement après son redémarrage : la
  partie suivant le redémarrage manque. À chiffrer avec `test-long.ps1 -Reprise` (taille/heure, CPU, RAM).
- **Chiffres à verser ici après le test de 60 min :** taille par heure ____ Mo, CPU moyen/max ____ %, RAM ____ Mo.
| 2026-11-06 | Étape 16c validée (`large-v3` retenu, choix définitif en fin de projet). Étape 16d : mesures versées (points 160 à 167) ; fonctionnement validé, **qualité insuffisante (19 % des termes en réunion réelle contre 75 % sur fichier)**, porte qualité non cochée | Walid |
| 2026-11-06 | Étape 16 validée par Walid (qualité : point ouvert, voir 160-166). Étape 17 découpée en 17a à 17d ; 17a codée (logs JSON, `request_id`, `meeting_id`, journal d'accès sans jeton) : 829 tests verts. Points 168 à 175 de §6 terdecies à confirmer | Walid |
| 2026-11-06 | Étape 17a validée (logs structurés JSON, `request_id`, journal d'accès sans jeton). Points 168 à 175 de §6 terdecies acceptés tels que proposés | Walid |
| 2026-11-06 | Étape 17b codée (`GET /health` minimal, `GET /v1/health` détaillé : base, LiveKit, S3, file de tâches ; 200 / 503). Points 176 à 185 de §6 terdecies à confirmer, dont les seuils de retard (181) | Walid |
| 2026-11-06 | Étape 17b validée (`GET /health` minimal, `GET /v1/health` détaillé : base, LiveKit, S3, file de tâches). Points 176 à 185 de §6 terdecies acceptés tels que proposés, dont les seuils de retard (181) | Walid |
| 2026-11-06 | Étape 17c codée (`jobs-list` / `jobs-retry`, `services/replay.py`, `meeting_id` dans les logs d'abandon) : `check` vert (906 tests). Points 186 à 196 de §6 terdecies à confirmer, dont la purge des audios après échec définitif (193, à trancher avec Eduvia) | Walid |
| 2026-11-07 | Étape 17c validée (`jobs-list` / `jobs-retry`, `services/replay.py`, `meeting_id` dans les logs d'abandon). Points 186 à 196 de §6 terdecies acceptés tels que proposés ; purge des audios après échec définitif (193) reste à trancher avec Eduvia | Walid |
| 2026-11-07 | Étape 17d codée (`tests/integration/test_restart.py`, démo `deploy/dev/test-reprise.ps1`, aucun changement du code applicatif) : 911 tests verts. Points 198 à 205 de §6 terdecies à confirmer, dont la limite 203 (a) : personne ne coupe la réunion pendant l'arrêt de l'API | Walid |
| 2026-11-07 | Étape 17d validée (démo `test-reprise.ps1` : 11 OK ; le script compare les échecs avant et après la coupure). Étape 17 validée. Points 198 à 205 acceptés tels que proposés, dont la limite 203 (a), à traiter à l'étape 19 | Walid |
| 2026-11-07 | Étape 18 découpée en 18a (config TURN, doc des ports), 18b (relais forcé `?relais=1`), 18c (`test-reseau.ps1`) ; codées, 6 tests unitaires verts. Points 206 à 216 de §6 quaterdecies à confirmer, dont le montage du 443 avec Coolify (208) et la validation réelle sur le serveur | Walid |
| 2026-11-07 | Étape 18 validée (recette locale Docker Desktop, points 217 à 221) : TURN/UDP et TURN/TLS démarrent et le relais est alloué ; la visio sans `?relais=1` fonctionne ; le relais de bout en bout est impossible à valider sous Docker Desktop (probable limite de Docker, point 218) et est reporté au serveur réel (étape 19). 917 tests et lint verts. Points 206 à 216 acceptés tels que proposés | Walid |
| 2026-11-07 | Étape 19 démarrée (code seul, rien construit ni lancé sur serveur). Points 222 à 229 à confirmer : (222) deux images (`api` avec interface, `transcriber`) depuis un seul `Dockerfile`, dotenvx installé dans l'image, `.env.production` chiffré copié dans l'image, clé injectée à l'exécution ; (223) service `config-init` qui génère `livekit.yaml` et `egress.yaml` dans un volume (les images LiveKit/Egress n'ont pas de shell) ; (224) signalisation sur un **sous-domaine dédié** `rtc.eduvia.app` (au lieu de `visio.eduvia.app`, chemin partagé difficile à router sur Coolify) : `LIVEKIT_URL` = `wss://rtc.eduvia.app` ; (225) S3 externe (Eduvia), pas de SeaweedFS en production ; (226) TURN sur une 2e IP publique (`TURN_BIND_IP`, option A de RESEAU.md), certificat monté depuis `/data/eduvia/certs` ; (227) variables Coolify : la clé maître + `TURN_DOMAIN`, `TURN_BIND_IP`, `WITH_CUDA` (non secrets, hors `.env`) ; (228) API et worker partagent un volume SQLite (WAL), une seule instance d'API ; (229) `smoke-prod` automatise le parcours API ; la visio réelle, l'enregistrement et la transcription restent des tests manuels. |
| 2026-11-07 | Étape 19 validée **avec réserves** (recette locale Docker Desktop). Faits : `check` vert, compose valide, images `api` et `transcriber` construites, aucun secret dans les images, `config-init` déchiffre `.env.production` dans l'image avec la seule clé maître. Reportés à la recette finale (pas de serveur) : déploiement à blanc avec `INSTALL.md`, `smoke-prod`, pile complète avec de vraies valeurs, TURN en 443 seul, enregistrement vers l'IP publique. Non rejoués à la main : `db-backup` (couvert par 4 tests unitaires) et le nettoyage de la 2e clé de production de `.env.keys`. Mémo : `dotenvx run -- python -m ...` ne se tape pas directement dans PowerShell, passer par `uv run task` (tâche `db-backup`). Points 222 à 229 acceptés tels que proposés. |
| 2026-11-07 | Étape 20a validée par Walid (`test-charge.ps1`, `load-report`) ; clés LiveKit lues dans la config du conteneur (le `.env.development` donne `APItestkey`, le serveur `devkey` : écart à éclaircir). Étape 20b : outillage codé (`-Recorded`, `load-record`), mesures à faire | Walid |
| 2026-11-07 | Étape 20b validée **avec réserve** par Walid (test 300 connexions non tenu sur le PC de dev, à refaire sur serveur). Étapes 20c (outillage : `-SampleSeconds`, `FUITE POSSIBLE`) et 20d (recommandation provisoire, points 222 à 229 de §6 quindecies) codées ; mesures 20c et confirmation sur serveur à faire | Walid |
| 2026-11-07 | Étape 20 : `test-complet.ps1` exécuté (API 600/600 joins, MP4 0,18 à 0,53 Go/h en vidéo synthétique, PC saturé à 150 connexions + 2 enregistrements). Point 230 de §6 quindecies ajouté ; `test-charge.ps1` réécrit (version précédente corrompue par une retouche) | Walid |
| 2026-11-07 | Étape 21a validée (licences, Valkey). Étape 21b codée : OpenAPI webhooks, CI, `repo-check` | Walid |
| 2026-11-07 | Transcription : qualité jugée **par lecture du texte** (pas par les chiffres) sur `reunion-01.wav` (`large-v3`, `--route fr,en`, sans terme) : 17 phrases exactes sur 27, 3 défauts mineurs, 7 erreurs réelles (termes CI/CD, Airflow, chatbot, PostgreSQL, « ND », « Notez », « Merci. » inventé) ; langue respectée. Traduction vers le français notée comme évolution optionnelle. Points 240 à 244 | Walid |
| 2026-11-08 | Transcription en réunion réelle (une personne, navigateur, `large-v3`, `fr,en`, sans terme donné) : résultats jugés **bons** par lecture du texte ; écart « qualité de transcription » **clos**. Défauts mineurs notés (une phrase anglaise passée en français, « embeddings » mal transcrit). Améliorations possibles notées pour la suite : routage, traduction vers le français, correction par LLM local. Points 245 à 247 | Walid |
| 2026-11-08 | Scénario 9 : cause de l'échec du 10 × 30 diagnostiquée côté banc d'essai (PC de dev, Docker Desktop, générateur sur la même machine) ; **comptage corrigé : 26 participants par salle, pas 30** ; scénario 9 à valider sur serveur. Points 248 à 254 | Walid |
| 2026-11-08 | Dimensionnement sur le pire cas : réunion de 40 tout activé, chiffrée par unité puis jusqu'à 15 en simultané (`DIMENSIONNEMENT.md`, points 262 à 266). Point 260 mis à jour | Walid |

## 6 octodecies. Revue finale : corrections de robustesse (2026-11-08)

255. **Revue du 2026-11-08.** Relecture du code contre le besoin : 2 bloquants de déploiement (7a, 7b), 1 risque sur le scénario 1 (7c), 3 protections (7d, 7e, 7f). Le suivi détaillé n'est pas inclus dans cette version du dépôt. 962 tests verts.
256. **Modèle Whisper.** Le worker reste **hors ligne** (`local_files_only`). Le modèle est installé une fois par `docker compose run --rm transcriber model-init` (seule sortie réseau, à l'installation). Compatible avec « aucun appel à un service externe » pendant l'exploitation.
257. **Webhooks hors du tour principal.** Le tour du planificateur (fin de créneau, fermeture de salle, enregistrements) ne fait plus d'appel vers Eduvia : un backend lent (10 s de délai par appel, 50 par lot) ne peut plus retarder la coupure. Le comportement des tests (`run_once` par défaut) est inchangé.
258. **Limitation de débit.** Par IP, en mémoire (une seule instance de l'API). Valeur **large** (1500/min) car un établissement partage une IP et la salle d'attente relit toutes les 2 s. Faux positifs possibles derrière une IP très partagée : régler `JOIN_RATE_LIMIT_PER_MINUTE`. Pendant `load-api` (600 joins depuis une IP) : mettre 0 le temps du test.
259. **Plafond de participants.** `MAX_PARTICIPANTS_PER_MEETING` (40 par défaut, soit 30 + marge) compte les présences ouvertes de rôle participant ; les organisateurs ne sont jamais refusés. Une personne qui se reconnecte dans un autre navigateur compte comme une nouvelle identité tant que l'ancienne présence n'est pas fermée.
260. **Enregistrements simultanés.** Le dimensionnement du point 226 suppose 3 enregistrements. Le cas où toutes les réunions sont enregistrées (15 × 40, tout activé, transcription comprise) est chiffré au point 262 et dans `DIMENSIONNEMENT.md`.
261. **Ouverts après la revue.** Rotation des clés de production (7i), sauvegardes hors serveur (7j), épinglage des images (7k), validation sur serveur : TURN 443, scénarios 1 à 8 à la main, scénario 9, MP4 d'1 h pesé.

## 6 novodecies. Dimensionnement : une réunion de 40, puis jusqu'à 15 en simultané (2026-11-08)

262. **Scénario de dimensionnement.** Décision de Walid : dimensionner sur le **pire cas**, soit des réunions de **40 participants, tout activé** (40 caméras, 40 micros, partage d'écran, enregistrement, transcription de 40 pistes), chiffrées **par réunion**, puis multipliées jusqu'à **15 en simultané** (600 connexions). Le détail est dans `DIMENSIONNEMENT.md`.
263. **Résultat par réunion de 40 (marge ×1,5).** ≈ 10 vCPU média, 0,5 Gbit/s sortants, 6 vCPU / 6 Gio d'enregistrement, 1 GPU de transcription, 12 Go de MP4 et 9 Go de pistes temporaires pour 8 h. La charge est linéaire : **15 réunions = 156 vCPU média, 7,5 Gbit/s (carte 10 Gbit/s), 90 vCPU / 96 Gio d'Egress, 6 GPU pour finir la transcription en 6 h, 180 Go de MP4 par jour.**
264. **Le point 226 reste valable pour le scénario du PDF** (10 × 30, environ 4 caméras par salle, 3 enregistrements). Il ne couvrait pas un pire cas : avec 40 caméras, chaque participant reçoit 39 vidéos (75 % du trafic sortant).
265. **Statut des chiffres.** Calibrés sur les mesures du point 249 (1,15 vCPU par salle de 26, sous Docker Desktop, donc pessimistes) ; la part des pistes Egress (0,05 vCPU et 60 Mio par piste), la vitesse GPU (5 × temps réel) et la part de clients passant par le TURN sont des **hypothèses**. À confirmer sur serveur (lot R3).
266. **Deux limites du code relevées pendant le calcul.** (a) `egress.yaml` ne règle pas `cpu_cost` : si Egress refuse des pistes faute de capacité, la transcription se fait sans ces locuteurs, sans erreur visible (une piste en échec n'est jamais fatale, `services/tracks.py`). (b) Le worker de transcription choisit les tâches sans verrou : une seule instance à la fois, donc le débit est celui d'un GPU (`services/transcription.py`).
