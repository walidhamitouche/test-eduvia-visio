import sqlite3
from pathlib import Path

import pytest

from tests.conftest import valid_env
from visio.config import get_settings
from visio.tools import dbbackup, renderconfig


def test_rendered_configs_are_valid_yaml_and_consistent(set_env, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    yaml = pytest.importorskip("yaml")
    env = valid_env("production")
    env["LIVEKIT_API_SECRET"] = 'se:cr#et"x'  # caractères spéciaux YAML
    set_env(env)
    settings = get_settings()
    renderconfig.write_all(settings, tmp_path, "turn.example.org", 443)
    lk = yaml.safe_load((tmp_path / "livekit.yaml").read_text(encoding="utf-8"))
    eg = yaml.safe_load((tmp_path / "egress.yaml").read_text(encoding="utf-8"))
    assert lk["keys"][settings.livekit_api_key] == 'se:cr#et"x'
    assert lk["turn"]["domain"] == "turn.example.org"
    assert lk["rtc"]["use_external_ip"] is True
    assert eg["api_secret"] == 'se:cr#et"x'
    assert eg["ws_url"] == "ws://livekit:7880"
    assert eg["storage"]["s3"]["bucket"] == settings.s3_bucket
    assert eg["storage"]["s3"]["endpoint"] == settings.s3_endpoint


def test_render_requires_turn_domain(set_env, monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    set_env(valid_env("production"))
    monkeypatch.delenv("TURN_DOMAIN", raising=False)
    assert renderconfig.main([str(tmp_path)]) == 1


def _make_db(path: Path, value: str) -> None:
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
        con.execute("DELETE FROM t")
        con.execute("INSERT INTO t VALUES (?)", (value,))


def test_backup_then_restore_gives_identical_data(tmp_path: Path) -> None:
    live, saved = tmp_path / "visio.db", tmp_path / "bk" / "saved.db"
    _make_db(live, "avant")
    dbbackup.backup(live, saved)
    _make_db(live, "apres")  # la base évolue après la sauvegarde
    dbbackup.restore(saved, live)
    with sqlite3.connect(live) as con:
        assert con.execute("SELECT v FROM t").fetchall() == [("avant",)]


def test_backup_refuses_to_overwrite_and_restore_refuses_corrupt(tmp_path: Path) -> None:
    live = tmp_path / "visio.db"
    _make_db(live, "x")
    saved = tmp_path / "s.db"
    dbbackup.backup(live, saved)
    with pytest.raises(FileExistsError):
        dbbackup.backup(live, saved)
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"pas une base sqlite" * 100)
    with pytest.raises((ValueError, sqlite3.Error)):
        dbbackup.restore(bad, live)
    with sqlite3.connect(live) as con:  # la base vivante n'a pas été touchée
        assert con.execute("SELECT v FROM t").fetchall() == [("x",)]
