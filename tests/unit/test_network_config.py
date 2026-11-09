"""Configuration réseau de production (étape 18) : TURN, ports, secrets, cohérence avec la doc."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "deploy" / "network" / "livekit.turn.yaml"
DOC = ROOT / "docs" / "RESEAU.md"


@pytest.fixture(scope="module")
def config() -> dict[str, Any]:
    loaded = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_turn_is_enabled_on_tls_443_with_its_own_domain(config: dict[str, Any]) -> None:
    turn = config["turn"]
    assert turn["enabled"] is True
    assert turn["tls_port"] == 443  # le 443 sortant est le seul port que certains pare-feu laissent
    assert re.fullmatch(r"turn\.[a-z0-9.-]+", turn["domain"])  # distinct du domaine de l'API


def test_the_tls_certificate_is_given_unless_a_balancer_terminates_it(
    config: dict[str, Any],
) -> None:
    turn = config["turn"]
    if not turn["external_tls"]:
        assert turn["cert_file"].endswith(".crt")
        assert turn["key_file"].endswith(".key")


def test_udp_is_kept_and_the_server_advertises_its_public_address(config: dict[str, Any]) -> None:
    assert config["rtc"]["use_external_ip"] is True
    assert isinstance(config["rtc"]["udp_port"], int)
    assert isinstance(config["rtc"]["tcp_port"], int)


def test_the_file_holds_no_secret() -> None:
    text = CONFIG.read_text(encoding="utf-8").lower()
    for forbidden in ("keys:", "secret", "api_key", "password", "redis"):
        assert forbidden not in text.replace("aucun secret", "")


def test_every_configured_port_is_documented(config: dict[str, Any]) -> None:
    doc = DOC.read_text(encoding="utf-8")
    ports = {
        config["rtc"]["tcp_port"],
        config["rtc"]["udp_port"],
        config["turn"]["tls_port"],
        config["turn"]["udp_port"],
    }
    for port in ports:
        assert f"| {port} |" in doc, f"le port {port} n'est pas dans le tableau de docs/RESEAU.md"


def test_internal_services_are_not_listed_as_public_ports() -> None:
    table = DOC.read_text(encoding="utf-8").split("## Ports à ouvrir")[1].split("##")[0]
    for internal in ("6379", "9000", "7880"):
        assert f"| {internal} |" not in table
