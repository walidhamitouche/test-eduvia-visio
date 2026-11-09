"""Inventaire des licences (étape 21a) : lecture des métadonnées et détection du copyleft."""

from __future__ import annotations

from email.message import Message

from visio.tools import licences as lic


def _meta(**fields: str) -> Message:
    message = Message()
    for key, value in fields.items():
        message[key.replace("_", "-")] = value
    return message


def test_license_prefers_spdx_then_short_field_then_classifiers() -> None:
    assert lic.license_of(_meta(License_Expression="Apache-2.0")) == "Apache-2.0"  # type: ignore[arg-type]
    assert lic.license_of(_meta(License="MIT")) == "MIT"  # type: ignore[arg-type]
    long_text = _meta(License="Permission is hereby granted\n" * 20)
    long_text["Classifier"] = "License :: OSI Approved :: BSD License"
    assert lic.license_of(long_text) == "BSD License"  # type: ignore[arg-type]
    assert lic.license_of(_meta(Name="x")) == lic.UNKNOWN  # type: ignore[arg-type]


def test_copyleft_detection() -> None:
    for text in ("GPL-3.0-only", "LGPL-2.1-or-later", "AGPL-3.0", "MPL-2.0", "SSPL-1.0"):
        assert lic.is_copyleft(text), text
    for text in ("MIT", "Apache-2.0", "BSD-3-Clause", "ISC", "(MIT OR Apache-2.0)"):
        assert not lic.is_copyleft(text), text


def test_npm_runtime_skips_dev_and_root() -> None:
    lock = {
        "packages": {
            "": {"name": "web"},
            "node_modules/a": {"version": "1.0.0", "license": "MIT"},
            "node_modules/b": {"version": "2.0.0", "license": "MIT", "dev": True},
            "node_modules/@s/c": {"version": "3.0.0"},
        }
    }
    assert lic.npm_runtime(lock) == {"a": ("1.0.0", "MIT"), "@s/c": ("3.0.0", lic.UNKNOWN)}


def test_table_flags_alerts() -> None:
    lines = lic.table("T", {"x": ("1", "GPL-3.0"), "y": ("1", lic.UNKNOWN), "z": ("1", "MIT")})
    assert lines[4].endswith("| copyleft |")
    assert lines[5].endswith("| à vérifier |")
    assert lines[6].endswith("|  |")
