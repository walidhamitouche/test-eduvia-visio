# Licences, briques et contraintes

> **Inventaire des licences des briques utilisées et de leurs contraintes (AGPL, usage commercial, marque), en réponse au §7 « Licences » du besoin.**
>
> Vérifié le **2026-11-08** sur les sources citées en §7. Chaque affirmation renvoie à une source ou à un fichier du projet.
> Ce document décrit les licences logicielles ; il n'est pas un avis juridique.

## 0. Réponse courte

| Question du besoin | Réponse |
|---|---|
| **AGPL ?** | **Aucune brique déployée et aucune dépendance n'est sous AGPL.** LiveKit Server et Egress : Apache-2.0. Valkey : BSD-3 (Redis a été écarté : ses versions récentes sont en RSALv2 / SSPLv1, AGPLv3 en option). L'inventaire des 72 dépendances (`THIRD-PARTY-LICENSES.txt`) ne contient aucune AGPL. |
| **Usage commercial ?** | **Autorisé** pour tout ce que nous livrons : Apache-2.0, MIT, BSD et PSF permettent l'usage commercial et l'hébergement pour des tiers, sans licence par utilisateur. Deux composants **propriétaires** existent dans les images tierces ou optionnelles : Google Chrome (dans l'image Egress) et les bibliothèques NVIDIA (image GPU, optionnelle). Leurs contraintes sont en §1. |
| **Marque ?** | **Aucune licence utilisée n'oblige à afficher le nom d'une brique dans l'interface** : la marque blanche est possible. Apache-2.0 (art. 6) ne donne aucun droit sur les noms et logos : l'interface n'en affiche aucun. La page de marque de LiveKit ne dit rien sur le fait de masquer son nom et autorise de citer « LiveKit » pour en parler : la documentation technique peut donc le nommer. |

## 1. Services déployés (images Docker)

| Brique | Rôle | Licence | Contrainte pour Eduvia |
|---|---|---|---|
| **LiveKit Server** `v1.13.11` | Média WebRTC, TURN | Apache-2.0 | Aucune. Conserver licence et notices en cas de redistribution. |
| **LiveKit Egress** `v1.15.0` | Enregistrement | Code : Apache-2.0 (fichier `LICENSE` et `NOTICE`, « Copyright 2023 LiveKit, Inc. »). **L'image contient d'autres composants, listés ci-dessous.** | Utilisée **telle que publiée par LiveKit, dans un conteneur séparé**, tirée du registre public. Ne pas la republier (voir « Composants de l'image Egress »). |
| **Valkey** `8-alpine` | File de travail LiveKit et Egress | BSD-3-Clause | Aucune. |
| **dotenvx** `2.38.2` (binaire dans nos images) | Déchiffrement du `.env` | BSD-3-Clause (« Copyright (c) 2024, Scott Motte ») | Aucune. |
| **tini** (dans l'image Egress) | Processus init | MIT | Aucune. |
| Python `3.12-slim`, Node `22-slim` (images de base), Ubuntu 24.04 (base Egress) | Exécution, build | Distributions Linux : ensemble de logiciels libres, chacun sous sa licence | Aucune pour un usage en conteneur. |
| **FFmpeg** (paquet de la distribution, dans nos images `api` et `transcriber`) | Fusion des segments, décodage audio | **LGPL 2.1+, avec des parties GPL 2+** ; si ces parties sont activées, la GPL s'applique à tout FFmpeg (page officielle FFmpeg, « legal ») | Nous l'appelons **comme un programme séparé** (ligne de commande). La GPL ne restreint pas l'exécution (voir §2). |
| **Bibliothèques NVIDIA** `nvidia-cublas-cu12`, `nvidia-cudnn-cu12` (**image GPU uniquement**, optionnelle) | Accélération GPU de Whisper | **Propriétaire NVIDIA** : cuBLAS sous le CUDA EULA ; cuDNN sous le Software License Agreement cuDNN | Voir « Bibliothèques NVIDIA » ci-dessous. L'image CPU n'en contient pas. |
| SeaweedFS | S3 **de développement seulement**, non déployé en production | Apache-2.0 | Aucune. |

### Composants de l'image Egress (lus dans les fichiers de build de LiveKit)

D'après `build/egress/Dockerfile` et `build/gstreamer/*` du dépôt `livekit/egress` :

| Composant | Licence | Observation |
|---|---|---|
| **GStreamer** (compilé depuis les sources, avec `gst-plugins-base`, `good`, `bad`, **`ugly`** et **`gst-libav`**) | LGPL 2.1+ pour le cœur ; les plugins `ugly` et certains éléments `bad` sont **GPL** | Version `1.28.7-2` sur la branche principale du dépôt (`.gst-version`) ; `1.24.13` au tag `v1.15.0`. |
| **x264** (`libx264-164`) et **x265** (`libx265-199`) | **GPL 2+** | Encodage H.264 / H.265, installés dans l'image de production GStreamer. |
| **FFmpeg** (`libavcodec`, `libavformat`…, paquets Ubuntu) | LGPL / GPL selon les modules | Idem. |
| **Google Chrome** (installé depuis `google-chrome-stable.deb`, **pas Chromium**) | **Propriétaire** (Conditions d'utilisation de Google Chrome ; une grande partie du code source de Chrome est publiée sous licences open source, `chrome://credits`) | Il sert à afficher notre page `/recording-template` et à la filmer. |
| Polices Noto, PulseAudio, Xvfb | Libres (OFL, LGPL, MIT) | |

**Conséquences pour Eduvia :**
1. **Usage normal sans obligation** : la GPL ne restreint pas l'exécution d'un programme. Texte de la GPL v2, §0 : « The act of running the Program is not restricted ». Ses obligations (fournir les sources, la licence) naissent à la **copie et à la distribution**.
2. **Nous ne distribuons aucun binaire** : le dépôt livré contient du code, des `Dockerfile` et un `docker-compose.yml`. Les images Egress, Valkey et LiveKit sont tirées par Eduvia **directement du registre de leur éditeur** ; les images `api` et `transcriber` sont **construites par Eduvia sur son serveur**.
3. **Ne pas republier** une image contenant GStreamer/x264/x265/FFmpeg ou Chrome (registre public, client, tiers) sans respecter leurs licences (sources correspondantes pour la GPL ; conditions de Google pour Chrome). Un registre privé interne d'Eduvia reste un usage interne.
4. Chrome n'est pas modifié et ne sert qu'à rendre notre page dans l'image fournie par LiveKit. Aucun produit Chrome n'est livré par nous.

### Bibliothèques NVIDIA (image GPU, optionnelle)

| Point | Constat | Source |
|---|---|---|
| Licence | cuBLAS : « LicenseRef-NVIDIA-Proprietary » ; cuDNN : pas de métadonnée de licence sur PyPI, couvert par le Software License Agreement cuDNN | métadonnées PyPI ; documentation NVIDIA |
| Usage | Droit d'installer et d'utiliser, pour des systèmes **avec GPU NVIDIA** uniquement | CUDA EULA §2.1 ; cuDNN SLA |
| Redistribution | Seuls certains fichiers d'exécution (`libcublas.so`, `libcublasLt.so`, fichiers `.so` de cuDNN) sont redistribuables, à condition d'être intégrés à une application qui apporte une « fonctionnalité supplémentaire significative » et d'être inchangés | CUDA EULA §2.6 et Annexe A ; cuDNN SLA, supplément §2 |
| Conséquence | Eduvia construit l'image GPU **sur son serveur** et l'utilise : usage conforme. **Ne pas publier l'image GPU** dans un registre public ni la remettre à un tiers. | |
| Alternative sans cette contrainte | L'image **CPU** (`WITH_CUDA=0`, défaut) ne contient aucune bibliothèque NVIDIA. | `Dockerfile` |

## 2. Transcription (hors ligne)

| Brique | Licence | Contrainte | Source |
|---|---|---|---|
| faster-whisper `1.2.1`, CTranslate2 `4.8.5` | MIT | Aucune | `THIRD-PARTY-LICENSES.txt` |
| Modèle Whisper `large-v3` d'origine (OpenAI) | **Apache-2.0** | Aucune ; usage commercial permis | fiche officielle `openai/whisper-large-v3` |
| Modèle converti **`Systran/faster-whisper-large-v3`** (celui que télécharge `model-init`, via `faster_whisper.utils.download_model`) | **MIT** | Aucune | fiche officielle `Systran/faster-whisper-large-v3` : « License: mit », convertie depuis `openai/whisper-large-v3` |

Le worker de transcription n'a aucun accès réseau : le modèle est copié une fois à l'installation (`model-init`). **Les textes MIT et Apache-2.0 correspondants sont fournis** dans `THIRD-PARTY-LICENSES.txt` (annexe).

## 3. Dépendances Python et npm (inventaire du 2026-11-08)

Source : `uv.lock` (dépendances de production + extra `transcription`) et `web/package-lock.json` (production). **72 paquets** (54 Python, 18 npm). Détail et texte de chaque licence : `THIRD-PARTY-LICENSES.txt`.

| Licence déclarée | Paquets (ordre de grandeur) |
|---|---|
| MIT, MIT License | 36 |
| BSD-3-Clause (et variantes) | 15 |
| Apache-2.0 (et variantes) | 14 |
| MPL-2.0 (`certifi`), MPL-2.0 AND MIT (`tqdm`) | 2 |
| PSF-2.0, 0BSD | 2 |
| Doubles licences (`packaging` : Apache-2.0 OU BSD-2-Clause ; `python-dateutil` : BSD-3 ou Apache-2.0) | 2 |
| Mélange documenté (`numpy` : BSD-3, 0BSD, MIT, Zlib, CC0) | 1 |

**Points précis, relevés dans les fichiers de licence eux-mêmes :**

| Paquet | Constat | Décision |
|---|---|---|
| `numpy` (dépendance de `ctranslate2` et `onnxruntime`) | Sa roue embarque `libgfortran` (**GPL-3.0-or-later avec GCC Runtime Library Exception 3.1**) et `libquadmath` (**LGPL-2.1+**), plus OpenBLAS (BSD-3). | **Accepté.** L'exception GCC permet de lier ces bibliothèques d'exécution à n'importe quel programme sans que la GPL s'étende à lui ; la LGPL s'applique à une bibliothèque dynamique non modifiée. Aucune distribution de notre part. |
| `machina` (npm) | Le fichier de licence précise « Also available under GPL 2.0 » : **double licence MIT / GPL-2.0**. | **Utilisé sous MIT** (choix de l'utilisateur de la double licence). |
| `certifi`, `tqdm` | MPL-2.0 : copyleft faible au niveau du fichier. | Accepté : usage sans modification. |
| `ctranslate2`, `onnxruntime`, `flatbuffers`, `tokenizers`, `@bufbuild/protobuf` | Ne distribuent pas de fichier de licence. | Licence déclarée dans leurs métadonnées (MIT, MIT, Apache-2.0, Apache-2.0, Apache-2.0 AND BSD-3) ; texte officiel fourni en annexe de `THIRD-PARTY-LICENSES.txt`. |
| `av` (PyAV) | BSD-3 ; ses roues embarquent des bibliothèques FFmpeg. | Même analyse que FFmpeg (§1) : aucune distribution. |

> **Correction de l'inventaire précédent** : il concluait « aucune GPL ni AGPL ». C'était vrai des licences **déclarées** des paquets, mais pas du contenu de la roue `numpy` (GCC runtime, ci-dessus) ni de la double licence de `machina`. L'analyse ci-dessus la remplace.

**Règle de projet :** aucune dépendance de production à copyleft fort (GPL, AGPL) sans décision écrite dans ce document. Outils de développement et de test (pytest, ruff, mypy, playwright, moto) : **non livrés** dans les images.

## 4. Obligations à respecter et état

| Obligation | Origine | État |
|---|---|---|
| Conserver les textes de licence et notices des bibliothèques embarquées | Apache-2.0, MIT, BSD | ✅ **`THIRD-PARTY-LICENSES.txt` généré (72 paquets, textes intégraux)**, à placer à la racine du dépôt et à copier dans l'image `api` (`COPY THIRD-PARTY-LICENSES.txt /app/`). Il n'est pas affiché dans l'interface de visio : la marque blanche est intacte. |
| Ne pas utiliser le nom ni le logo des briques | Apache-2.0 art. 6 ; page de marque LiveKit | ✅ aucun texte, titre ni nom de fichier de l'interface ne cite LiveKit. Les noms de champs JSON internes `livekit_url` et `livekit_token` ne sont pas affichés. |
| Citer les briques dans la documentation technique | usage factuel | ✅ autorisé par la page de marque (« talking about or representing LiveKit »). |
| Ne pas republier les images tierces (Egress, FFmpeg, NVIDIA) | GPL, CUDA EULA, Chrome | ✅ le compose les tire du registre de l'éditeur ; rien à republier. |
| Régénérer l'inventaire à chaque changement de dépendance | bonne pratique | `uv run task licences` et le script `collect.py` (voir §6). |

## 5. Licence du code d'Eduvia Visio

**Statut : projet de démonstration, tous droits réservés à son auteur (Walid Hamitouche).** Le code est communiqué pour consultation et évaluation. Aucune licence d'exploitation, aucune cession et aucune exclusivité ne sont accordées à ce stade ; elles feraient l'objet d'un accord écrit distinct.

- Aucune brique tierce n'impose de licence sur ce code : aucune dépendance AGPL ou GPL n'est liée à lui (§3).
- Les composants tiers restent sous leurs licences propres (§1 à §3).

## 6. Reproduire l'inventaire

```powershell
uv sync --frozen --no-dev --extra transcription --no-install-project
npm --prefix web ci --omit=dev --ignore-scripts
python -I tools/collect.py .venv/Lib/site-packages web/node_modules THIRD-PARTY-LICENSES.txt
```

Le script `collect.py` est fourni avec ce document ; il lit les métadonnées et fichiers de licence de chaque paquet.

## 7. Sources consultées le 2026-11-08

- LiveKit Server : <https://github.com/livekit/livekit> (Apache-2.0)
- LiveKit Egress : <https://github.com/livekit/egress> (Apache-2.0) ; `NOTICE` ; `build/egress/Dockerfile` ; `build/gstreamer/Dockerfile-base` et `Dockerfile-prod` ; `build/chrome/install-chrome` ; `.gst-version`
- Marque LiveKit : <https://livekit.com/brand>
- Valkey : <https://github.com/valkey-io/valkey/blob/unstable/COPYING> (BSD 3-Clause)
- dotenvx : `LICENSE` du dépôt <https://github.com/dotenvx/dotenvx> (BSD 3-Clause) et registre npm (`"license":"BSD-3-Clause"`)
- tini : `LICENSE` du dépôt <https://github.com/krallin/tini> (MIT)
- Whisper `large-v3` : <https://huggingface.co/openai/whisper-large-v3> (Apache-2.0)
- Conversion CTranslate2 : <https://huggingface.co/Systran/faster-whisper-large-v3> (MIT)
- FFmpeg : <https://www.ffmpeg.org/legal.html>
- GPL v2, §0 : texte de la licence, liste SPDX <https://github.com/spdx/license-list-data>
- CUDA EULA : <https://docs.nvidia.com/cuda/eula/index.html>
- cuDNN SLA : <https://docs.nvidia.com/deeplearning/cudnn/backend/latest/reference/eula.html>
- Métadonnées PyPI : `nvidia-cublas-cu12`, `nvidia-cudnn-cu12`
- Conditions supplémentaires Google Chrome : <https://www.google.com/chrome/terms/>

## 8. Périmètre

Ce document couvre les **licences logicielles** demandées par le besoin : AGPL, usage commercial, marque. Les brevets sur les codecs vidéo (Chrome mentionne la licence du portefeuille de brevets AVC) sont un sujet distinct des licences logicielles et ne sont pas traités ici ; ils concernent l'éditeur du codec, qui est LiveKit dans l'image fournie.
