"""Rapport de charge (étape 20a) : lecture des relevés `docker stats` et dérive de la RAM."""

from __future__ import annotations

from pathlib import Path

import pytest

from visio.tools import loadreport as lr


def test_parse_size_units() -> None:
    assert lr.parse_size("812MiB") == 812 * 2**20
    assert lr.parse_size("1.5GB") == 1.5e9
    assert lr.parse_size("0B") == 0


def test_parse_size_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="illisible"):
        lr.parse_size("beaucoup")
    with pytest.raises(ValueError, match="illisible"):
        lr.parse_size("12XB")


def test_parse_line_and_header() -> None:
    assert lr.parse_line("epoch;name;cpu;ram;net;disk") is None
    assert lr.parse_line("\ufeffepoch;name;cpu;ram;net;disk") is None  # BOM de PowerShell 5
    assert lr.parse_line("") is None
    parsed = lr.parse_line("100;livekit;153.2%;812MiB / 7.6GiB;1.2GB / 3.4GB;0B / 5MB")
    assert parsed is not None
    name, sample = parsed
    assert name == "livekit"
    assert sample.cpu == 153.2
    assert sample.net_tx == 3.4e9
    assert sample.disk_write == 5e6


def test_parse_line_wrong_field_count() -> None:
    with pytest.raises(ValueError, match="6 champs"):
        lr.parse_line("1;a;b")


def _series(rams: list[float]) -> list[lr.Sample]:
    return [lr.Sample(float(i), 50.0, r, 0, 0, 0, 0) for i, r in enumerate(rams)]


def test_ram_drift_flat_and_leak() -> None:
    assert lr.ram_drift_percent(_series([100.0] * 16)) == 0.0
    leak = _series([100.0] * 4 + [100.0] * 4 + [110.0] * 4 + [150.0] * 4)
    assert lr.ram_drift_percent(leak) == pytest.approx(50.0)
    assert lr.ram_drift_percent(_series([1.0, 2.0])) == 0.0


def test_main_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    csv = tmp_path / "stats.csv"
    csv.write_text(
        "epoch;name;cpu;ram;net;disk\n"
        "0;livekit;100%;1GiB / 8GiB;0B / 0B;0B / 0B\n"
        "60;livekit;300%;2GiB / 8GiB;75MB / 150MB;0B / 0B\n",
        encoding="utf-8",
    )
    assert lr.main([str(csv), "--participants", "300"]) == 0
    out = capsys.readouterr().out
    assert "livekit" in out
    assert "300.0 %" in out
    empty = tmp_path / "vide.csv"
    empty.write_text("", encoding="utf-8")
    assert lr.main([str(empty)]) == 1


def test_docker_stats_gaps_are_counted_not_fatal(tmp_path: Path) -> None:
    csv = tmp_path / "stats.csv"
    csv.write_text(
        "epoch;name;cpu;ram;net;disk\n"
        "0;egress;10%;1GiB / 8GiB;0B / 0B;0B / 0B\n"
        "10;egress;--;--;--;--\n"
        "20;egress;20%;1GiB / 8GiB;1MB / 1MB;0B / 0B\n",
        encoding="utf-8",
    )
    data, gaps = lr.load_with_gaps(csv)
    assert len(data["egress"]) == 2
    assert gaps == {"egress": 1}
    assert lr.main([str(csv)]) == 0


def test_leak_suspects_needs_a_long_test_and_watches_the_right_containers() -> None:
    leaking = [
        lr.Sample(i * 600.0, 10.0, 100.0 if i < 12 else 150.0, 0, 0, 0, 0) for i in range(24)
    ]
    short = [lr.Sample(float(i), 10.0, 100.0 if i < 12 else 150.0, 0, 0, 0, 0) for i in range(24)]
    data = {"x-livekit-1": leaking, "x-s3-1": leaking, "x-redis-1": short}
    assert lr.leak_suspects(data) == ["x-livekit-1"]
