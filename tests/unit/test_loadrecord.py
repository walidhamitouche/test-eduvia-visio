"""Enregistrement des salles de charge (étape 20b), avec un enregistreur factice."""

from __future__ import annotations

from visio.services.egress import EgressInfo
from visio.tools.loadrecord import record_rooms


class _Recorder:
    def __init__(self, fail_room: str = "") -> None:
        self.fail_room = fail_room
        self.started: list[tuple[str, str]] = []
        self.stopped: list[str] = []

    def start_room_composite(self, room: str, key: str) -> EgressInfo:
        if room == self.fail_room:
            msg = "pas de place"
            raise RuntimeError(msg)
        self.started.append((room, key))
        return EgressInfo(f"EG_{room}", "active", "", ())

    def start_track_audio(self, room: str, track_sid: str, key: str) -> EgressInfo:
        raise NotImplementedError

    def stop(self, egress_id: str) -> EgressInfo:
        self.stopped.append(egress_id)
        return EgressInfo(egress_id, "complete", "", (f"{egress_id}.mp4",))

    dead: set[str] = set()

    def list(
        self, egress_id: str | None = None, *, room_name: str | None = None, active: bool = False
    ) -> list[EgressInfo]:
        if egress_id in self.dead:
            return [EgressInfo(egress_id, "failed", "page de gabarit injoignable", ())]
        return [EgressInfo(egress_id, "active", "", ())] if egress_id else []


def test_records_then_stops_every_room() -> None:
    recorder = _Recorder()
    waits: list[float] = []
    result = record_rooms(
        recorder, 3, 90, prefix="charge/t", sleep=waits.append, log=lambda _: None
    )
    assert [r for r, _ in recorder.started] == ["charge-1", "charge-2", "charge-3"]
    assert recorder.started[0][1] == "charge/t/charge-1.mp4"
    assert recorder.stopped == ["EG_charge-1", "EG_charge-2", "EG_charge-3"]
    assert waits == [90]
    assert [r.status for r in result] == ["complete"] * 3


def test_a_failed_start_is_counted_not_hidden() -> None:
    recorder = _Recorder(fail_room="charge-2")
    result = record_rooms(recorder, 3, 1, sleep=lambda _: None, log=lambda _: None)
    assert sorted(r.status for r in result) == ["complete", "complete", "failed"]
    assert recorder.stopped == ["EG_charge-1", "EG_charge-3"]


def test_already_failed_recording_keeps_its_cause_and_is_not_stopped() -> None:
    recorder = _Recorder()
    recorder.dead = {"EG_charge-1"}
    lines: list[str] = []
    result = record_rooms(recorder, 1, 1, sleep=lambda _: None, log=lines.append)
    assert result[0].status == "failed"
    assert result[0].error == "page de gabarit injoignable"
    assert recorder.stopped == []
    assert any("page de gabarit injoignable" in line for line in lines)
