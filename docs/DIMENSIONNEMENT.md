# Dimensionnement : une réunion de 40 personnes, tout activé, puis jusqu'à 15 en simultané

> Réponse à « Dimensionnement » (besoin §7 et §8). Calculs reproductibles : `python docs/dimensionnement.py`.
> **Statut : calcul sur les mesures du PC de dev (`DECISIONS.md` §6 quindecies) et sur des hypothèses marquées (H).** Rien n'a été mesuré sur un serveur ; les points à confirmer sont au §7.

## 1. Le scénario

Une **réunion** est dimensionnée avec **tout activé** ; le pire cas est cette réunion multipliée par 15.

| | Besoin du PDF | **Réunion retenue** |
|---|---|---|
| Participants | jusqu'à 30 | **40** (+ organisateurs, exemptés du plafond) |
| Caméras | non précisé | **40 allumées** |
| Micros | non précisé | **40 ouverts**, 25 % émettent en même temps (le silence n'émet rien) |
| Partage d'écran | oui | **1**, vu par 39 personnes |
| Enregistrement | oui | **oui** (MP4 composite) |
| Transcription | oui | **oui**, 40 pistes audio |
| Durée | 30 min à 8 h | **8 h** (le plafond du créneau est 12 h : × 1,5 sur stockage et transcription) |
| Marge | ×1,5 | **×1,5** sur le CPU et le réseau |

## 2. Une réunion de 40, tout activé (l'unité)

| Poste | Calculé | Avec marge ×1,5 |
|---|---|---|
| LiveKit (CPU) | 6,9 vCPU | **≈ 10,4 vCPU** |
| Réseau sortant | 334 Mbit/s | **≈ 0,5 Gbit/s** |
| Réseau entrant | 95 Mbit/s | ≈ 0,14 Gbit/s |
| Enregistrement (Egress) | 1 composite (4 vCPU / 4 Gio) + 40 pistes (H : 2 vCPU / 2,4 Gio) | **6 vCPU / 6,4 Gio** |
| API, Valkey, front | partagés | négligeable (voir §4.4) |
| Transcription (GPU) | 12 h de parole pour 8 h de réunion ≈ 2,4 GPU-heures | **1 GPU** (≈ 18 min de calcul par heure de réunion) |
| Stockage MP4 conservé | 1,5 Go/h | **12 Go** pour une réunion de 8 h |
| Pistes audio temporaires | 29 Mo/h par participant | **9,3 Go** pour 8 h, supprimées après transcription |

## 3. Montée en charge : la même réunion, N fois en simultané

| Réunions simultanées | Connexions | LiveKit CPU (marge) | Sortie (marge) | Egress | MP4 par jour | Pistes par jour | GPU (fin en 6 h) |
|---|---|---|---|---|---|---|---|
| **1** | 40 | 10 vCPU | 0,5 Gbit/s | 6 vCPU / 6 Gio | 12 Go | 9 Go | 1 |
| **5** | 200 | 52 vCPU | 2,5 Gbit/s | 30 vCPU / 32 Gio | 60 Go | 46 Go | 2 |
| **10** | 400 | 104 vCPU | 5,0 Gbit/s | 60 vCPU / 64 Gio | 120 Go | 93 Go | 4 |
| **15 (pire cas)** | 600 | **156 vCPU** | **7,5 Gbit/s** | **90 vCPU / 96 Gio** | **180 Go** | **139 Go** | **6** |

La charge est **linéaire** : chaque réunion de 40 ajoute ≈ 10 vCPU média, 0,5 Gbit/s, 6 vCPU d'enregistrement et ≈ 12 Go de MP4 par jour. Le stockage MP4 est conservé sans expiration (suppression par API) : **15 réunions de 8 h par jour ≈ 4 To/mois ouvré, ≈ 47 To/an**.

**Comparaison avec le besoin du PDF** (10 × 30, 4 caméras par salle, comme dans les essais) : 20 vCPU média, 0,9 Gbit/s. Les essais comptaient 4 caméras par salle ; avec 40 caméras, chaque participant reçoit 39 vidéos, soit **75 % du trafic sortant**.

## 4. Détail des calculs

### 4.1 LiveKit (CPU et réseau)
- **Calibrage mesuré** (points 223, 230, 249) : 1,15 vCPU par salle de 26 (4 caméras, 6 micros) ⇒ **≈ 11 900 paquets/s par vCPU**. Mesuré sous Docker Desktop, donc **pessimiste** : Linux natif devrait faire mieux (non prouvé). Le calibrage redonne 13,6 vCPU et 0,61 Gbit/s pour 10 × 30 avec 4 caméras, proche des 11 à 12 vCPU et 575 Mbit/s mesurés.
- **Par salle de 40** : 1 560 abonnements vidéo (40 × 39, couche 180p : l'interface affiche les 40 tuiles et `adaptiveStream` prend la couche la plus basse), 390 abonnements audio actifs, partage d'écran, montée des 40 caméras en simulcast, enregistreur.
- **Sensibilité** : si les 40 micros émettaient en continu (limite physique, les voix se couvriraient) : 12 vCPU calculés par salle (**181 vCPU, 272 avec marge pour 15 salles**) et 5,7 Gbit/s (8,6 avec marge, sous 10 Gbit/s).
- RAM LiveKit : 883 Mio mesurés pour 130 connexions ⇒ **8 Gio par nœud** (large).

### 4.2 Egress : enregistrement et pistes
- **Composite** : 4 vCPU / 4 Gio par enregistrement (recommandation LiveKit ; mesuré 1,6 vCPU en moyenne, 3 en pic, ~1 Gio).
- **Pistes de transcription (H)** : Opus → OGG, 0,05 vCPU et 60 Mio par piste. **Non mesuré** (§7).
- Un conteneur Egress par enregistrement (`shm_size` de 1 Gio chacun, exigé par Chrome) : **≈ 1,3 réplica par réunion**, soit ≈ 20 pour 15 réunions, au lieu du service unique du compose actuel.

### 4.3 Transcription
- **Moteur mesuré** : `large-v3` int8, routage `fr,en` = **2,0 × la durée de parole sur CPU** (point 240, PC portable) : 1 h de réunion ≈ 3 h de calcul, impraticable.
- **GPU (H)** : ≈ 5 × temps réel avec le routage par phrase. **À mesurer sur la carte cible.**
- **Volume** : somme des paroles ≈ 1,5 × la durée de réunion (H : chevauchements) ⇒ 12 h de parole pour une réunion de 8 h, 180 h par jour pour 15.

| Délai accepté après la fin | GPU pour 1 réunion | GPU pour 15 réunions |
|---|---|---|
| 3 h | 0,8 (donc 1) | 12 |
| 4 h | 0,6 (donc 1) | 9 |
| **6 h** | **0,4 (donc 1)** | **6** |
| 12 h | 0,2 (donc 1) | 3 |
| 24 h | 0,1 (donc 1) | 1,5 (donc 2) |

Le besoin dit « quelques heures » : **6 h** retenu. Un GPU à la demande est possible (traitement différé).

### 4.4 API, stockage, S3
- API : 100 `POST /v1/join`/s mesurés sur un processus ; **4 vCPU / 8 Gio** couvrent le pire cas. SQLite impose **une seule instance**.
- MP4 : 720p30 ≈ 3,1 Mbit/s ≈ 1,4 Go/h, **1,5 Go/h retenu** (le 0,18 à 0,53 Go/h mesuré vient de vidéo synthétique : plancher). Pendant l'enregistrement, les segments HLS s'ajoutent avant la fusion : jusqu'à ×2 en transitoire.
- Disque local du transcriber : ≈ 10 Go par réunion de 8 h ; prévoir 100 Go.
- Envoi vers le S3 : ≈ 6 Mbit/s par réunion, **≈ 90 Mbit/s en continu pour 15**.

## 5. Architecture recommandée pour le pire cas (15 × 40)

| Nœud | Contenu | CPU | RAM | Réseau |
|---|---|---|---|---|
| **Média ×3** | LiveKit multi-nœud via Valkey (déjà présent) | 64 vCPU chacun | 16 Gio | 10 Gbit/s au total, IP publique par nœud |
| **Enregistrement** | ≈ 20 réplicas Egress | 90 à 100 vCPU | 128 Gio | 1 Gbit/s |
| **Services** | API, Valkey, front | 4 vCPU | 8 Gio | 1 Gbit/s |
| **GPU ×6** | transcription | selon carte | ≥ 8 Gio de VRAM (24 Gio conseillé) | 1 Gbit/s |
| **TURN** | 2e IP publique (443) | inclus au nœud média | | voir §7 |

Pour **une seule réunion de 40**, un serveur unique suffit : ≈ 16 vCPU et 16 Gio de RAM pour le média et l'enregistrement, 1 Gbit/s, 1 GPU. Pour **5 réunions** : ≈ 90 vCPU, 48 Gio, 5 Gbit/s, 2 GPU (un serveur média de 64 vCPU et un nœud enregistrement de 32 vCPU).


## 6. Ce que le pire cas demande dans le code

1. **LiveKit multi-nœud** : le compose lance un seul service avec des ports fixes (7881, 7882) ; à éclater sur plusieurs hôtes.
2. **Egress en réplicas** (`deploy.replicas` ou `--scale`).
3. **Plusieurs workers de transcription** : `services/transcription.py` choisit les tâches `PENDING` sans verrou, donc **deux workers pourraient prendre la même tâche**. Avec un seul worker, le débit est celui d'un GPU.
4. **Plafond de tuiles** : `Room.tsx` affiche tous les participants (levier du §8).

## 7. Points non mesurés, par ordre de risque

1. **Pistes Egress en nombre.** `egress.yaml` (généré par `renderconfig.py`) ne règle ni `cpu_cost` ni `max_cpu_utilization`. Egress accepte ou refuse une demande selon un coût CPU réservé par type (valeurs par défaut variables selon la version ; la doc de la v1.8.4 donne 1,0 par piste et 3,0 par composite). Si le coût réservé dépasse la capacité, des pistes sont refusées. Comme « une piste en échec n'est jamais fatale », la transcription se fait **sans ces locuteurs, sans erreur visible**. À vérifier sur la v1.15.0 (essai : 40 pistes dans une salle).
2. **Coût réel d'une piste** (0,05 vCPU, 60 Mio : hypothèses).
3. **Vitesse GPU** avec le routage `fr,en` (5 × temps réel : hypothèse).
4. **Pic d'arrivée** : 300 arrivées simultanées non testées ; présence et webhooks non mesurés.
5. **TURN/TLS sur 443** : part des clients qui passent par le TURN inconnue ; le chiffrement TLS de plusieurs Gbit/s coûte du CPU.
6. **Calibrage** sur Docker Desktop, 26 participants par salle, 4 caméras : ne couvre ni 40 participants, ni la grille complète, ni le partage d'écran (point 225).
7. **Plafond de 40** : les organisateurs en sont exemptés ; prévoir 5 % de marge.

## 8. Leviers pour réduire sans changer le besoin

| Levier | Effet sur le pire cas (15 × 40) |
|---|---|
| **Grille limitée à 16 tuiles vidéo** (pagination ; le front rend aujourd'hui tout le monde) | LiveKit **104 vCPU** (au lieu de 156), sortie **4,2 Gbit/s** (au lieu de 7,5) |
| Enregistrement à 15 à 25 images/s au lieu de `H264_720P_30` (`services/egress.py`) | moins de CPU Egress et de stockage ; à mesurer |
| Transcription acceptée en 12 h au lieu de 6 h | 3 GPU au lieu de 6 |

Ces leviers sont optionnels : le dimensionnement ci-dessus est valable sans eux.
