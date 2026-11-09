# ruff: noqa: E501
"""Fusion des segments en un MP4 (étape 14d) : vrai ffmpeg, faux S3 dans un dossier."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from visio.services import segments
from visio.services.egress import EgressGateway

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg absent")


class DiskStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _p(self, key: str) -> Path:
        return self.root / key

    def list_objects(self, prefix: str = "") -> list[tuple[str, int]]:
        return [
            (p.relative_to(self.root).as_posix(), p.stat().st_size)
            for p in self.root.rglob("*")
            if p.is_file() and p.relative_to(self.root).as_posix().startswith(prefix)
        ]

    def size(self, key: str) -> int | None:
        return self._p(key).stat().st_size if self._p(key).is_file() else None

    def download_to(self, key: str, path: Path) -> None:
        shutil.copy(self._p(key), path)

    def upload_from(self, key: str, path: Path, *, content_type: str = "") -> None:
        self._p(key).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, self._p(key))

    def delete(self, key: str) -> None:
        self._p(key).unlink()

    def copy(self, source: str, destination: str) -> None:
        self._p(destination).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self._p(source), self._p(destination))


def _make_segments(folder: Path, count: int) -> None:
    folder.mkdir(parents=True)
    for i in range(count):
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=2:s=320x180:r=15",
             "-f", "lavfi", "-i", "sine=d=2", "-c:v", "libx264", "-c:a", "aac", "-shortest",
             "-f", "mpegts", str(folder / f"seg_{i:05d}.ts")],
            check=True,
        )  # fmt: skip


def test_merge_gives_one_mp4_and_removes_segments(tmp_path: Path) -> None:
    key = "meetings/mtg_1/recordings/abc.mp4"
    _make_segments(tmp_path / "meetings/mtg_1/recordings/abc", 3)
    storage = DiskStorage(tmp_path)
    size = segments.merge(storage, key)  # type: ignore[arg-type]
    assert size > 0 and storage.size(key) == size
    assert storage.list_objects("meetings/mtg_1/recordings/abc/") == []
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(tmp_path / key)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert 5.0 < float(probe.stdout) < 7.5  # 3 segments de 2 s


def test_merge_without_segments_fails(tmp_path: Path) -> None:
    with pytest.raises(segments.MergeError):
        segments.merge(DiskStorage(tmp_path), "meetings/m/recordings/x.mp4")  # type: ignore[arg-type]


def test_segment_mode_sends_segment_outputs() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"egressId": "EG_1", "status": "EGRESS_STARTING"})

    gateway = EgressGateway(
        "http://livekit:7880", "k", "s" * 32,
        client=httpx.Client(transport=httpx.MockTransport(handler)), segment_seconds=600,
        template_url="http://api:8000/recording-template",
    )  # fmt: skip
    gateway.start_room_composite("rm", "meetings/m/recordings/x.mp4")
    import json

    body = json.loads(seen[0].content)
    assert "file_outputs" not in body
    assert body["segment_outputs"][0]["filename_prefix"] == "meetings/m/recordings/x/seg"
    assert body["segment_outputs"][0]["segment_duration"] == 600


def test_simulated_long_meeting_merges_to_the_expected_duration(tmp_path: Path) -> None:
    from visio.tools.simulate_long import simulate

    result = simulate(
        DiskStorage(tmp_path), hours=8 / 3600, segment_seconds=2, bitrate_kbps=300,
        log=lambda _m: None,
    )  # fmt: skip
    assert result["segments"] == 4
    assert abs(result["duration_seconds"] - 8) < 3
    assert result["leftover_objects"] == 0


def test_simulated_crash_keeps_what_was_recorded(tmp_path: Path) -> None:
    from visio.tools.simulate_long import simulate

    result = simulate(
        DiskStorage(tmp_path), hours=8 / 3600, segment_seconds=2, bitrate_kbps=300,
        drop_last=2, log=lambda _m: None,
    )  # fmt: skip
    assert result["segments"] == 2
    assert abs(result["duration_seconds"] - 4) < 3
