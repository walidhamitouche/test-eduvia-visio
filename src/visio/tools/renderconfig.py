"""Génère les configurations LiveKit et Egress de production (étape 19).

    dotenvx run -f .env.production -- python -m visio.tools.renderconfig /generated

Les images LiveKit et Egress n'ont pas de shell et ne lisent pas le `.env` chiffré : ce programme
(lancé par le service `config-init` du compose) déchiffre la configuration, écrit les deux fichiers
YAML dans un volume partagé, puis s'arrête. Aucun secret n'est stocké dans une image ni dans le
dépôt : les fichiers n'existent que dans le volume du serveur.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from visio.config import ConfigurationError, Settings, get_settings

LIVEKIT_INTERNAL_WS = "ws://livekit:7880"
API_WEBHOOK_URL = "http://api:8000/v1/livekit/webhook"


def _q(value: str) -> str:
    """Chaîne YAML entre guillemets (JSON est du YAML valide : ':' ou '#' ne cassent rien)."""
    return json.dumps(value)


def render_livekit(settings: Settings, turn_domain: str, turn_udp_port: int = 443) -> str:
    key = settings.livekit_api_key
    secret = settings.livekit_api_secret.get_secret_value()
    return f"""port: 7880
rtc:
  tcp_port: 7881
  udp_port: 7882
  use_external_ip: true
redis:
  address: redis:6379
keys:
  {_q(key)}: {_q(secret)}
webhook:
  api_key: {_q(key)}
  urls:
    - {_q(API_WEBHOOK_URL)}
turn:
  enabled: true
  domain: {_q(turn_domain)}
  tls_port: 443
  udp_port: {turn_udp_port}
  cert_file: /certs/turn.crt
  key_file: /certs/turn.key
  external_tls: false
"""


def render_egress(settings: Settings) -> str:
    key = settings.livekit_api_key
    secret = settings.livekit_api_secret.get_secret_value()
    s3 = f"""    access_key: {_q(settings.s3_access_key.get_secret_value())}
    secret: {_q(settings.s3_secret_key.get_secret_value())}
    region: {_q(settings.s3_region)}
    endpoint: {_q(settings.s3_endpoint)}
    bucket: {_q(settings.s3_bucket)}
    force_path_style: true
"""
    return f"""logging:
  level: info
api_key: {_q(key)}
api_secret: {_q(secret)}
ws_url: {LIVEKIT_INTERNAL_WS}
insecure: true
redis:
  address: redis:6379
health_port: 8080
s3:
{s3}storage:
  s3:
{s3}"""


def write_all(settings: Settings, out_dir: Path, turn_domain: str, turn_udp_port: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in (
        ("livekit.yaml", render_livekit(settings, turn_domain, turn_udp_port)),
        ("egress.yaml", render_egress(settings)),
    ):
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o644)  # lu par LiveKit / Egress (autres utilisateurs du conteneur)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    out_dir = Path(args[0] if args else "/generated")
    turn_domain = os.environ.get("TURN_DOMAIN", "").strip()
    if not turn_domain:
        print("TURN_DOMAIN manquant (ex. turn.eduvia.app) : voir docs/INSTALL.md", file=sys.stderr)
        return 1
    try:
        settings = get_settings()
    except ConfigurationError as error:
        print(error, file=sys.stderr)
        return 1
    turn_udp_port = int(os.environ.get("TURN_UDP_PORT", "443"))
    write_all(settings, out_dir, turn_domain, turn_udp_port)
    print(f"Configuration LiveKit et Egress écrite dans {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
