from __future__ import annotations

from visio.tools.tracksget import file_name


def test_the_file_name_is_safe_and_keeps_the_track_id() -> None:
    key = "meetings/mtg_1/audio/a4c614fc-b100-4780-9f9b-244697e5c1dd.ogg"
    assert file_name("Marie Durand (staff_42)", key) == "Marie_Durand_staff_42-a4c614fc.ogg"
    assert file_name("???", key) == "inconnu-a4c614fc.ogg"
