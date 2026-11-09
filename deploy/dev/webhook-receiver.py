"""Faux récepteur Eduvia pour le développement : vérifie la signature et affiche l'événement.

$env:WEBHOOK_SECRET = dotenvx get WEBHOOK_SECRET -f .env.development
uv run python deploy/dev/webhook-receiver.py [port]      # défaut : 9000
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

SECRET = os.environ.get("WEBHOOK_SECRET", "")
TOLERANCE = 300


def valid(body: bytes, timestamp: str, signature: str) -> bool:
    try:
        if abs(time.time() - int(timestamp)) > TOLERANCE:
            return False
    except ValueError:
        return False
    mac = hmac.new(SECRET.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return hmac.compare_digest("v1=" + mac.hexdigest(), signature)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        ok = valid(
            body,
            self.headers.get("X-Visio-Timestamp", ""),
            self.headers.get("X-Visio-Signature", ""),
        )
        event = json.loads(body or b"{}")
        marque = "OK     " if ok else "REFUSÉ "
        print(f"[{marque}] {event.get('type')} {event.get('id')}")
        print(json.dumps(event.get("data"), ensure_ascii=False, indent=2))
        self.send_response(204 if ok else 401)
        self.end_headers()

    def log_message(self, *_: object) -> None:
        pass


if __name__ == "__main__":
    if not SECRET:
        sys.exit("WEBHOOK_SECRET manquant dans l'environnement")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    print(f"En écoute sur http://localhost:{port}/hooks/visio")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()  # noqa: S104
