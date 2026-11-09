"""Contrôle du dépôt (étape 21b) : motifs interdits et valeurs `.env` en clair."""

from __future__ import annotations

from visio.tools import repocheck as rc


def test_forbidden_patterns() -> None:
    files = [
        "src/visio/main.py",
        ".env.keys",
        "data/visio.db",
        "deploy/dev/.generated/livekit.yaml",
        "web/node_modules/a/index.js",
        "src/visio/__pycache__/x.pyc",
        ".env.production",
        "docs/DECISIONS.md",
    ]
    assert rc.forbidden(files) == [
        ".env.keys",
        "data/visio.db",
        "deploy/dev/.generated/livekit.yaml",
        "web/node_modules/a/index.js",
        "src/visio/__pycache__/x.pyc",
    ]


def test_encrypted_env_is_accepted_and_plaintext_is_flagged() -> None:
    text = (
        '#/---[public key]---/\nDOTENV_PUBLIC_KEY_PRODUCTION="02abc"\n'
        'API_KEY="encrypted:BPdf"\nWEBHOOK_SECRET=hunter2\nEMPTY=\n'
    )
    assert rc.plaintext_env_values(".env.production", text) == [
        ".env.production : WEBHOOK_SECRET en clair"
    ]
