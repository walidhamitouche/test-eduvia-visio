"""Compte les salles et participants reellement connectes dans LiveKit (diagnostic de charge).

Usage :
        uv run python deploy/dev/diag_rooms.py --url ws://localhost:7880 \
        --key K --secret S --prefix diag-p10-

Affiche une ligne JSON : {"rooms": 10, "participants": 300}
Meme principe que src/visio/services/rooms.py : appel Twirp ListRooms, jeton HS256 (PyJWT).
Aucune dependance de plus (httpx et pyjwt sont deja dans le projet).
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx
import jwt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--secret", required=True)
    parser.add_argument("--prefix", default="")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    for websocket, plain in (("wss://", "https://"), ("ws://", "http://")):
        if base.startswith(websocket):
            base = plain + base.removeprefix(websocket)

    now = int(time.time())
    claims = {"iss": args.key, "nbf": now - 5, "exp": now + 60, "video": {"roomList": True}}
    token = jwt.encode(claims, args.secret, algorithm="HS256")
    try:
        response = httpx.post(
            f"{base}/twirp/livekit.RoomService/ListRooms",
            json={},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        print(json.dumps({"error": type(error).__name__}))
        return 1

    rooms = [
        r
        for r in response.json().get("rooms", [])
        if str(r.get("name", "")).startswith(args.prefix)
    ]
    total = sum(int(r.get("numParticipants", r.get("num_participants", 0)) or 0) for r in rooms)
    print(json.dumps({"rooms": len(rooms), "participants": total}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
