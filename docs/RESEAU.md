# Réseau : TURN, pare-feu, TLS 443 (étape 18)

Objectif (besoin §7) : la visio fonctionne derrière un pare-feu d'entreprise ou d'établissement
qui ne laisse sortir que le **443**. La config est dans `deploy/network/livekit.turn.yaml`.

## Comment un client se connecte

1. **Signalisation** : `wss://visio.eduvia.app` (443, passe par le proxy Coolify). Toujours du TLS.
2. **Média** : UDP direct (`7882/udp`), sinon TCP direct (`7881/tcp`), sinon **TURN/TLS sur 443**
   (domaine `turn.eduvia.app`). Le TURN de LiveKit n'accepte que les clients déjà authentifiés
   par la signalisation (jeton de la réunion) : ce n'est pas un relais ouvert.

Un client qui ne sort que par le 443 passe donc par : signalisation (443) + TURN/TLS (443).

## Ports à ouvrir sur le serveur (entrée)

| Port | Protocole | Rôle | Obligatoire |
|---|---|---|---|
| 443 | TCP | HTTPS (visio, API) et TURN/TLS | oui |
| 443 | UDP | TURN/UDP (le 3478 si le 443 UDP est pris) | non, mais recommandé |
| 7881 | TCP | WebRTC en TCP | oui |
| 7882 | UDP | WebRTC en UDP | oui |
| 80 | TCP | émission et renouvellement des certificats | oui |

**Plage de relais du TURN** (`turn.relay_range_*`, 30000-40000/UDP par défaut) : le relais ne sert qu'entre le client et LiveKit sur la même machine, donc elle n'a normalement pas à être publique ; **à confirmer sur le serveur** (si le relais échoue alors que 443 répond, ouvrir cette plage).

Tout le reste reste fermé : Redis, S3, Egress, l'API de LiveKit (7880) ne sont **pas** publics.

## Où mettre le TURN/TLS, puisque Coolify (Traefik) occupe déjà le 443 ?

- **Option A, recommandée : une seconde adresse IP** pour `turn.eduvia.app`. LiveKit écoute le 443
  de cette adresse et termine lui-même le TLS (`tls_port: 443`, certificat monté).
- **Option B : un seul IP, routage par SNI.** Traefik envoie le trafic TCP du domaine TURN vers
  LiveKit **sans le déchiffrer** (`tls.passthrough`, règle `HostSNI(...)`), LiveKit écoute en interne
  sur `5349` (`tls_port: 5349`). À valider sur le vrai serveur.
- **Option C : TLS terminé par un équilibreur** devant LiveKit (`external_tls: true`, pas de
  certificat dans la config de LiveKit).

## Certificat

Le domaine TURN a son **propre certificat**, signé par une autorité reconnue (un certificat
auto-signé est refusé par les navigateurs). Il doit correspondre à `turn.domain`. Il se renouvelle
(Let's Encrypt, tous les 60 jours environ) : prévoir un rechargement de LiveKit après renouvellement.

## Alternatives si le 443 ne suffit pas

- Ouvrir `3478/udp` (TURN/UDP classique) pour les réseaux qui le tolèrent.
- Passer par un proxy HTTP d'entreprise : le TURN/TLS ne passe pas toujours un proxy qui inspecte
  le TLS ; le client doit alors être autorisé vers `turn.eduvia.app`.

## Vérifier

- `uv run pytest tests/unit/test_network_config.py` : la config TURN et cette page concordent.
- Côté navigateur, `https://visio.../j/<lien>?relais=1` force le **relais TURN** (diagnostic) :
  si la visio marche ainsi, le TURN marche. Les statistiques se lisent dans `chrome://webrtc-internals`
  (paire de candidats retenue : type `relay`).
- `.\deploy\dev\test-reseau.ps1 -Domaine turn.eduvia.app` : ports ouverts et certificat.

## Recette locale (Docker Desktop) et ce qu'elle ne prouve pas

Le TURN de dev s'active avec `$env:VISIO_DEV_TURN = "udp"` ou `"tls"` avant `.\deploy\dev\docker-dev.ps1 up` (TLS : certificat `mkcert` pour `turn.localtest.me` dans `deploy\dev\.generated\certs`). Revenir au mode normal : `Remove-Item Env:VISIO_DEV_TURN`, puis `down` et `up`.

- **Ce qui se vérifie en local :** le TURN démarre (`Starting TURN server` dans `docker-dev.ps1 logs`), les ports de relais sont publiés, et avec `?relais=1` Chrome obtient un candidat `relay` (ligne `udp relay` dans les logs de LiveKit).
- **Ce qui ne se vérifie pas en local :** la visio de bout en bout via le relais. Sous Docker Desktop, LiveKit et son TURN sont dans le même conteneur derrière le NAT de Docker et le trafic relayé n'aboutit pas (probable limite de Docker, voir `DECISIONS.md` point 218). Ce test, avec le test « 443 seul » du pare-feu, se fait sur le serveur réel (étape 19).
