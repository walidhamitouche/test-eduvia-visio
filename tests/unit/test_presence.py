"""Temps de présence (étape 11) : l'union des connexions, sans double comptage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from visio.domain.presence import merge_intervals, presence_seconds

T0 = datetime(2027, 1, 2, 15, 0, tzinfo=UTC)
END = T0 + timedelta(hours=2)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_one_connection_counts_its_duration() -> None:
    assert presence_seconds([(at(0), at(30))], until=END) == 30 * 60


def test_leaving_and_coming_back_gives_two_intervals_and_the_sum() -> None:
    intervals = [(at(0), at(20)), (at(40), at(55))]
    assert merge_intervals(intervals, until=END) == [(at(0), at(20)), (at(40), at(55))]
    assert presence_seconds(intervals, until=END) == (20 + 15) * 60


def test_overlapping_connections_are_counted_once() -> None:
    # deux onglets, ou reconnexion avant que la sortie précédente soit détectée
    intervals = [(at(0), at(30)), (at(20), at(50))]
    assert merge_intervals(intervals, until=END) == [(at(0), at(50))]
    assert presence_seconds(intervals, until=END) == 50 * 60


def test_a_connection_inside_another_adds_nothing() -> None:
    assert presence_seconds([(at(0), at(60)), (at(10), at(20))], until=END) == 60 * 60


def test_touching_connections_merge() -> None:
    assert merge_intervals([(at(0), at(10)), (at(10), at(20))], until=END) == [(at(0), at(20))]


def test_missing_exit_runs_until_the_reference_time() -> None:
    assert presence_seconds([(at(0), None)], until=at(45)) == 45 * 60


def test_everything_is_cut_at_the_reference_time() -> None:
    # sortie après la fin de la réunion (webhook tardif) : on ne compte pas au-delà
    assert presence_seconds([(at(100), at(200))], until=END) == 20 * 60
    assert presence_seconds([(at(0), None)], until=END) == 120 * 60


def test_empty_and_negative_intervals_are_ignored() -> None:
    assert presence_seconds([(at(10), at(10)), (at(30), at(20)), (at(130), None)], until=END) == 0


def test_order_does_not_matter() -> None:
    shuffled = [(at(40), at(55)), (at(0), at(20)), (at(10), at(30))]
    assert presence_seconds(shuffled, until=END) == (30 + 15) * 60


def test_no_connection_means_zero() -> None:
    assert presence_seconds([], until=END) == 0
