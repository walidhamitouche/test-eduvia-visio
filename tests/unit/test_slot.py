"""Règle du créneau (besoin §2), horloge simulée. Scénario 1 de la recette côté logique."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from visio.domain.clock import FixedClock, SystemClock
from visio.domain.errors import InvalidSlotError
from visio.domain.meeting import Meeting, MeetingState, SlotStatus
from visio.domain.slot import (
    MAX_DURATION,
    MAX_EARLY_JOIN_MINUTES,
    can_join,
    can_modify,
    closes_at,
    has_started,
    lifecycle_status,
    opens_at,
    parse_iso8601,
    seconds_until_end,
    status_at,
    to_paris,
    validate_slot,
)

PLUS_1 = timezone(timedelta(hours=1))
PLUS_2 = timezone(timedelta(hours=2))


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Un instant du 2 janvier 2027, heure de Paris (+01:00)."""
    return datetime(2027, 1, 2, hour, minute, second, tzinfo=PLUS_1)


def make_meeting(
    *,
    early: int = 0,
    state: MeetingState = MeetingState.SCHEDULED,
    ended_at: datetime | None = None,
) -> Meeting:
    """Le créneau de l'exemple du besoin : 16 h → 18 h."""
    return Meeting(
        id="mtg_test",
        starts_at=at(16),
        ends_at=at(18),
        early_join_minutes=early,
        state=state,
        ended_at=ended_at,
    )


# --- Scénario 1 : créneau strict --------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "expected_status", "joinable"),
    [
        (at(15, 55), SlotStatus.NOT_OPEN_YET, False),
        (at(15, 59, 59), SlotStatus.NOT_OPEN_YET, False),
        (at(16, 0, 0), SlotStatus.OPEN, True),
        (at(17, 0, 0), SlotStatus.OPEN, True),
        (at(17, 59, 59), SlotStatus.OPEN, True),
        (at(18, 0, 0), SlotStatus.ENDED, False),
        (at(18, 1), SlotStatus.ENDED, False),
    ],
)
def test_strict_slot_without_early_join(
    now: datetime, expected_status: SlotStatus, joinable: bool
) -> None:
    meeting = make_meeting()
    assert status_at(meeting, now) is expected_status
    assert can_join(meeting, now) is joinable


@pytest.mark.parametrize(
    ("now", "joinable"),
    [
        (at(15, 49), False),
        (at(15, 49, 59), False),
        (at(15, 50), True),
        (at(16, 0), True),
    ],
)
def test_early_join_margin_of_10_minutes(now: datetime, joinable: bool) -> None:
    assert can_join(make_meeting(early=10), now) is joinable


def test_early_join_does_not_extend_the_end() -> None:
    meeting = make_meeting(early=10)
    assert can_join(meeting, at(17, 59, 59))
    assert not can_join(meeting, at(18, 0, 0))


def test_opening_and_closing_instants() -> None:
    meeting = make_meeting(early=10)
    assert opens_at(meeting) == at(15, 50)
    assert closes_at(meeting) == at(18)


# --- Annulation, clôture anticipée -----------------------------------------------------


@pytest.mark.parametrize("now", [at(10), at(15, 55), at(16), at(17), at(18, 1)])
def test_cancelled_meeting_is_always_refused(now: datetime) -> None:
    meeting = make_meeting(state=MeetingState.CANCELLED)
    assert status_at(meeting, now) is SlotStatus.CANCELLED
    assert not can_join(meeting, now)


def test_meeting_ended_by_organizer_at_17h_is_refused_at_17h01() -> None:
    meeting = make_meeting(state=MeetingState.ENDED, ended_at=at(17))
    assert status_at(meeting, at(17, 1)) is SlotStatus.ENDED
    assert not can_join(meeting, at(17, 1))


def test_ended_state_is_refused_even_if_clock_is_inside_the_slot() -> None:
    meeting = make_meeting(state=MeetingState.ENDED, ended_at=at(17))
    assert not can_join(meeting, at(16, 30))


def test_early_closing_cuts_at_that_instant_even_if_state_is_not_updated_yet() -> None:
    """Le planificateur peut avoir du retard : l'heure de clôture suffit à refuser."""
    meeting = make_meeting(state=MeetingState.LIVE, ended_at=at(17))
    assert can_join(meeting, at(16, 59, 59))
    assert not can_join(meeting, at(17, 0, 0))


def test_closing_date_after_ends_at_does_not_extend_the_slot() -> None:
    meeting = make_meeting(state=MeetingState.LIVE, ended_at=at(19))
    assert closes_at(meeting) == at(18)
    assert not can_join(meeting, at(18, 0, 0))


def test_live_meeting_follows_the_clock() -> None:
    meeting = make_meeting(state=MeetingState.LIVE)
    assert can_join(meeting, at(17))
    assert not can_join(meeting, at(18, 1))


def test_scheduled_state_past_the_end_is_ended() -> None:
    """Personne n'est venu : le statut est « terminé » dès l'heure de fin."""
    assert status_at(make_meeting(), at(20)) is SlotStatus.ENDED


# --- Décalages horaires ----------------------------------------------------------------


def test_plus_one_and_plus_two_offsets_give_the_same_instant() -> None:
    """16:00+01:00 et 17:00+02:00 sont le même instant (15:00 UTC)."""
    winter = parse_iso8601("2027-01-02T16:00:00+01:00")
    other = parse_iso8601("2027-01-02T17:00:00+02:00")
    assert winter == other == datetime(2027, 1, 2, 15, 0, tzinfo=UTC)


def test_parse_accepts_z_and_returns_utc() -> None:
    parsed = parse_iso8601("2027-01-02T15:00:00Z")
    assert parsed == datetime(2027, 1, 2, 15, 0, tzinfo=UTC)
    assert parsed.utcoffset() == timedelta(0)


def test_paris_summer_and_winter_times() -> None:
    """Heure de Paris : +01:00 en hiver, +02:00 en été (changement d'heure le 28 mars 2027)."""
    winter = to_paris(datetime(2027, 1, 2, 15, 0, tzinfo=UTC))
    summer = to_paris(datetime(2027, 7, 2, 14, 0, tzinfo=UTC))
    assert (winter.hour, winter.utcoffset()) == (16, timedelta(hours=1))
    assert (summer.hour, summer.utcoffset()) == (16, timedelta(hours=2))


def test_slot_across_daylight_saving_change_keeps_its_real_duration() -> None:
    """Le 28 mars 2027 à 2 h, Paris passe à +02:00 : 01:00+01:00 → 04:00+02:00 dure 2 h."""
    starts = parse_iso8601("2027-03-28T01:00:00+01:00")
    ends = parse_iso8601("2027-03-28T04:00:00+02:00")
    assert ends - starts == timedelta(hours=2)
    validate_slot(starts, ends)
    meeting = Meeting(id="m", starts_at=starts, ends_at=ends)
    # 03:59:59+02:00 est encore dans le créneau, 04:00:00+02:00 non.
    assert can_join(meeting, datetime(2027, 3, 28, 3, 59, 59, tzinfo=PLUS_2))
    assert not can_join(meeting, datetime(2027, 3, 28, 4, 0, 0, tzinfo=PLUS_2))


def test_comparison_works_across_different_offsets() -> None:
    meeting = make_meeting()  # 16:00 → 18:00 à +01:00
    assert can_join(meeting, datetime(2027, 1, 2, 17, 0, tzinfo=PLUS_2))  # 16:00 Paris
    assert not can_join(meeting, datetime(2027, 1, 2, 16, 59, tzinfo=PLUS_2))  # 15:59 Paris


@pytest.mark.parametrize(
    "value",
    ["2027-01-02T16:00:00", "2027-01-02", "pas une date", "", "2027-13-45T99:00:00+01:00"],
)
def test_parse_refuses_dates_without_offset_or_malformed(value: str) -> None:
    with pytest.raises(InvalidSlotError):
        parse_iso8601(value)


def test_parse_refuses_non_string() -> None:
    with pytest.raises(InvalidSlotError):
        parse_iso8601(1234)  # type: ignore[arg-type]


# --- Validation du créneau ---------------------------------------------------------------


def test_valid_slot_is_accepted() -> None:
    validate_slot(at(16), at(18))
    validate_slot(at(16), at(18), early_join_minutes=10)


@pytest.mark.parametrize("ends", [at(16), at(15, 59, 59), at(10)])
def test_ends_at_not_after_starts_at_is_refused(ends: datetime) -> None:
    with pytest.raises(InvalidSlotError) as error:
        validate_slot(at(16), ends)
    assert error.value.code == "invalid_slot"


def test_naive_datetimes_are_refused() -> None:
    naive = datetime(2027, 1, 2, 16, 0)
    with pytest.raises(InvalidSlotError):
        validate_slot(naive, at(18))
    with pytest.raises(InvalidSlotError):
        validate_slot(at(16), datetime(2027, 1, 2, 18, 0))


def test_duration_cap_is_12_hours() -> None:
    validate_slot(at(6), at(6) + MAX_DURATION)  # exactement 12 h : accepté
    with pytest.raises(InvalidSlotError):
        validate_slot(at(6), at(6) + MAX_DURATION + timedelta(seconds=1))


def test_a_day_of_training_of_8_hours_is_accepted() -> None:
    validate_slot(at(9), at(17))


@pytest.mark.parametrize("early", [-1, MAX_EARLY_JOIN_MINUTES + 1, 10_000])
def test_early_join_out_of_range_is_refused(early: int) -> None:
    with pytest.raises(InvalidSlotError):
        validate_slot(at(16), at(18), early_join_minutes=early)


@pytest.mark.parametrize("early", [0, 10, MAX_EARLY_JOIN_MINUTES])
def test_early_join_in_range_is_accepted(early: int) -> None:
    validate_slot(at(16), at(18), early_join_minutes=early)


@pytest.mark.parametrize("early", [True, 10.5, "10"])
def test_early_join_must_be_an_integer(early: object) -> None:
    with pytest.raises(InvalidSlotError):
        validate_slot(at(16), at(18), early_join_minutes=early)  # type: ignore[arg-type]


def test_early_join_defaults_to_zero() -> None:
    assert make_meeting().early_join_minutes == 0


# --- Avertissement de fin ----------------------------------------------------------------


def test_seconds_until_end() -> None:
    meeting = make_meeting()
    assert seconds_until_end(meeting, at(17, 55)) == 300
    assert seconds_until_end(meeting, at(17, 59)) == 60
    assert seconds_until_end(meeting, at(17, 59, 59)) == 1


def test_seconds_until_end_rounds_up_so_it_is_never_zero_while_open() -> None:
    meeting = make_meeting()
    now = at(17, 59, 59) + timedelta(milliseconds=500)
    assert can_join(meeting, now)
    assert seconds_until_end(meeting, now) == 1


def test_seconds_until_end_is_zero_when_over_or_cancelled() -> None:
    assert seconds_until_end(make_meeting(), at(18)) == 0
    assert seconds_until_end(make_meeting(), at(19)) == 0
    assert seconds_until_end(make_meeting(state=MeetingState.CANCELLED), at(17)) == 0
    assert seconds_until_end(make_meeting(state=MeetingState.ENDED, ended_at=at(17)), at(16)) == 0


def test_seconds_until_end_follows_early_closing() -> None:
    meeting = make_meeting(state=MeetingState.LIVE, ended_at=at(17))
    assert seconds_until_end(meeting, at(16, 59)) == 60


def test_seconds_until_end_before_opening_counts_to_the_end() -> None:
    assert seconds_until_end(make_meeting(), at(15)) == 3 * 3600


def test_seconds_until_end_is_zero_exactly_when_not_joinable_after_opening() -> None:
    """Invariant : une fois ouverte, `can_join` ⇔ il reste au moins 1 seconde."""
    meeting = make_meeting(early=10)
    start = at(15, 50)
    for offset in range(0, 2 * 3600 + 15 * 60, 7):
        now = start + timedelta(seconds=offset)
        assert can_join(meeting, now) == (seconds_until_end(meeting, now) > 0)


# --- « Commencé » : modification et annulation ----------------------------------------------


def test_meeting_can_be_modified_before_it_starts() -> None:
    meeting = make_meeting()
    assert not has_started(meeting, at(15, 59, 59))
    assert can_modify(meeting, at(15, 59, 59))


def test_meeting_cannot_be_modified_once_the_start_time_has_passed() -> None:
    meeting = make_meeting()
    assert has_started(meeting, at(16))
    assert not can_modify(meeting, at(16))


def test_meeting_cannot_be_modified_once_somebody_joined_early() -> None:
    meeting = make_meeting(early=10, state=MeetingState.LIVE)
    assert has_started(meeting, at(15, 55))
    assert not can_modify(meeting, at(15, 55))


@pytest.mark.parametrize("state", [MeetingState.ENDED, MeetingState.CANCELLED])
def test_ended_or_cancelled_meeting_cannot_be_modified(state: MeetingState) -> None:
    assert not can_modify(make_meeting(state=state), at(10))


# --- Horloge ------------------------------------------------------------------------------


def test_fixed_clock_only_moves_when_told() -> None:
    clock = FixedClock(at(15, 55))
    assert clock.now() == at(15, 55)
    clock.set(at(16))
    assert clock.now() == at(16)


def test_fixed_clock_refuses_naive_time() -> None:
    with pytest.raises(ValueError, match="décalage"):
        FixedClock(datetime(2027, 1, 2, 16, 0))
    clock = FixedClock(at(16))
    with pytest.raises(ValueError, match="décalage"):
        clock.set(datetime(2027, 1, 2, 16, 0))


def test_system_clock_returns_an_aware_utc_time() -> None:
    now = SystemClock().now()
    assert now.utcoffset() == timedelta(0)
    assert abs(datetime.now(UTC) - now) < timedelta(seconds=5)


# --- lifecycle_status (étape 4) : l'état que l'API montre ---------------------------------


def _lifecycle_meeting(**overrides: object) -> Meeting:
    values: dict[str, object] = {
        "id": "mtg_x",
        "starts_at": parse_iso8601("2027-01-02T16:00:00+01:00"),
        "ends_at": parse_iso8601("2027-01-02T18:00:00+01:00"),
    }
    values.update(overrides)
    return Meeting(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("state", "now", "expected"),
    [
        (MeetingState.SCHEDULED, "2027-01-02T10:00:00+01:00", MeetingState.SCHEDULED),
        (MeetingState.SCHEDULED, "2027-01-02T17:59:59+01:00", MeetingState.SCHEDULED),
        (MeetingState.SCHEDULED, "2027-01-02T18:00:00+01:00", MeetingState.ENDED),
        (MeetingState.LIVE, "2027-01-02T17:59:59+01:00", MeetingState.LIVE),
        (MeetingState.LIVE, "2027-01-02T18:00:00+01:00", MeetingState.ENDED),
        (MeetingState.ENDED, "2027-01-02T10:00:00+01:00", MeetingState.ENDED),
        (MeetingState.CANCELLED, "2027-01-02T19:00:00+01:00", MeetingState.CANCELLED),
    ],
)
def test_lifecycle_status(state: MeetingState, now: str, expected: MeetingState) -> None:
    assert lifecycle_status(_lifecycle_meeting(state=state), parse_iso8601(now)) == expected


def test_lifecycle_status_follows_early_closing() -> None:
    meeting = _lifecycle_meeting(
        state=MeetingState.LIVE, ended_at=parse_iso8601("2027-01-02T17:00:00+01:00")
    )
    assert (
        lifecycle_status(meeting, parse_iso8601("2027-01-02T16:59:59+01:00")) == MeetingState.LIVE
    )
    assert (
        lifecycle_status(meeting, parse_iso8601("2027-01-02T17:00:00+01:00")) == MeetingState.ENDED
    )
