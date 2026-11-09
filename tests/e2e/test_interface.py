"""Interface de visio (étape 7), dans un vrai navigateur.

Les cinq parcours du plan : lien valide → entrée en salle ; lien invalide → page d'erreur ; lien
nominatif → nom verrouillé, lien générique → nom obligatoire ; avant le créneau → attente en heure
de Paris ; après → « réunion terminée ». Plus : marque blanche, mobile, aucune mention de la brique.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

import pytest

from tests.e2e.conftest import PARIS, Backend, NewPage

JOIN = re.compile("Rejoindre la réunion")


def token_of(url: str) -> str:
    return url.rsplit("/j/", 1)[1]


def open_link(page: Any, backend: Backend, url: str) -> None:
    page.goto(f"{backend.base_url}/j/{token_of(url)}")


def enter_room(page: Any, name: str | None = None) -> None:
    """Pré-jointure → salle. `name` seulement sur un lien générique."""
    page.get_by_role("button", name=JOIN).wait_for()
    if name is not None:
        page.get_by_label("Votre nom").fill(name)
    page.get_by_role("button", name=JOIN).click()
    page.get_by_test_id("room").wait_for()


def visible_text_and_markup(page: Any) -> str:
    """Tout ce qu'un visiteur ou son inspecteur de page peut voir : texte, attributs, titre."""
    return str(page.evaluate("document.title + ' ' + document.documentElement.outerHTML"))


# --- lien valide : entrée en salle, deux personnes se voient et discutent ----------------------


def test_two_people_meet_in_the_room(
    backend: Backend, needs_livekit: None, new_page: NewPage
) -> None:
    meeting = backend.create_meeting(recording=False)
    marie, lina = new_page(), new_page()

    open_link(marie, backend, meeting["organizer_urls"][0])
    enter_room(marie)
    open_link(lina, backend, meeting["participant_url"])
    enter_room(lina, name="Lina Benali")

    for page in (marie, lina):
        page.wait_for_function("document.querySelectorAll('[data-testid=tile]').length === 2")
    names = {tile.get_attribute("data-name") for tile in lina.get_by_test_id("tile").all()}
    assert names == {"Marie Durand", "Lina Benali"}

    # La vidéo de l'autre arrive vraiment (caméra simulée par le navigateur de test).
    lina.wait_for_function(
        """() => [...document.querySelectorAll('[data-name="Marie Durand"] video')]
              .some(v => v.videoWidth > 0)"""
    )

    # Discussion dans les deux sens.
    lina.get_by_test_id("toggle-chat").click()
    lina.get_by_label("Écrire un message").fill("Bonjour à tous")
    lina.get_by_role("button", name="Envoyer").click()
    marie.get_by_test_id("toggle-chat").click()
    marie.get_by_test_id("chat-panel").get_by_text("Bonjour à tous").wait_for()
    marie.get_by_label("Écrire un message").fill("Bonjour Lina")
    marie.get_by_role("button", name="Envoyer").click()
    lina.get_by_test_id("chat-panel").get_by_text("Bonjour Lina").wait_for()

    # Liste des participants avec le rôle.
    marie.get_by_test_id("toggle-people").click()
    people = marie.get_by_test_id("people-panel")
    people.get_by_text("Participants (2)").wait_for()
    assert "Organisateur" in people.inner_text()

    # Couper le micro se voit chez l'autre.
    lina.get_by_test_id("toggle-mic").click()
    lina.get_by_role("button", name="Activer le micro").wait_for()
    marie.locator('[data-name="Lina Benali"]').get_by_text("Micro coupé").wait_for(state="attached")

    # Quitter ramène à un écran clair, et la personne reste visible tant qu'elle est là.
    lina.get_by_test_id("leave").click()
    lina.get_by_role("heading", name="Vous avez quitté la réunion").wait_for()
    marie.wait_for_function("document.querySelectorAll('[data-testid=tile]').length === 1")


def test_nothing_visible_mentions_the_underlying_brick(
    backend: Backend, needs_livekit: None, new_page: NewPage
) -> None:
    meeting = backend.create_meeting()
    page = new_page()
    open_link(page, backend, meeting["organizer_urls"][0])
    page.get_by_role("button", name=JOIN).wait_for()
    assert "livekit" not in visible_text_and_markup(page).lower()
    enter_room(page)
    page.get_by_test_id("tile").wait_for()
    page.get_by_test_id("toggle-chat").click()
    page.get_by_test_id("toggle-people").click()
    assert "livekit" not in visible_text_and_markup(page).lower()


# --- lien invalide : une erreur, jamais une salle vide -----------------------------------------


def test_invented_and_modified_links_show_an_error_page(
    backend: Backend, new_page: NewPage
) -> None:
    meeting = backend.create_meeting()
    real = token_of(meeting["participant_url"])
    flipped = real[:-2] + ("AB" if not real.endswith("AB") else "CD")
    page = new_page()
    for bad in ("n-importe-quoi", flipped, real + "x"):
        page.goto(f"{backend.base_url}/j/{bad}")
        page.get_by_role("heading", name="Lien invalide").wait_for()
        assert page.get_by_test_id("room").count() == 0
        assert page.get_by_role("button", name=JOIN).count() == 0


def test_excluded_link_is_refused(backend: Backend, new_page: NewPage) -> None:
    from visio.db import create_db_engine, create_session_factory
    from visio.db.models import LinkRow
    from visio.domain.slot import parse_iso8601
    from visio.services.links import revoke_link

    meeting = backend.create_meeting()
    url = backend.create_link(meeting["id"], role="participant", display_name="Karim Z.")
    engine = create_db_engine(backend.db_url)
    try:
        with create_session_factory(engine)() as db:
            link = db.query(LinkRow).filter(LinkRow.display_name == "Karim Z.").one()
            revoke_link(db, link.id, now=parse_iso8601(datetime.now(PARIS).isoformat()))
    finally:
        engine.dispose()
    page = new_page()
    open_link(page, backend, url)
    page.get_by_role("heading", name="Accès retiré").wait_for()


# --- nom : verrouillé sur un lien nominatif, obligatoire sur le lien générique -----------------


def test_named_link_locks_the_name(backend: Backend, new_page: NewPage) -> None:
    meeting = backend.create_meeting()
    url = backend.create_link(
        meeting["id"], role="participant", display_name="Lina Benali", external_id="stu_981"
    )
    page = new_page()
    open_link(page, backend, url)
    field = page.get_by_label("Votre nom")
    field.wait_for()
    assert field.input_value() == "Lina Benali"
    assert field.get_attribute("readonly") is not None
    page.get_by_text("Ce nom est celui de votre invitation").wait_for()


def test_generic_link_requires_a_name(
    backend: Backend, needs_livekit: None, new_page: NewPage
) -> None:
    meeting = backend.create_meeting()
    page = new_page()
    open_link(page, backend, meeting["participant_url"])
    field = page.get_by_label("Votre nom")
    field.wait_for()
    assert field.input_value() == ""
    assert field.get_attribute("readonly") is None

    page.get_by_role("button", name=JOIN).click()
    page.get_by_text("Saisissez votre nom pour entrer.").wait_for()
    assert page.get_by_test_id("room").count() == 0

    field.fill("Camille Roy")
    page.get_by_role("button", name=JOIN).click()
    page.get_by_test_id("room").wait_for()
    page.locator('[data-name="Camille Roy"]').wait_for()


# --- créneau : attente avant, « terminée » après -----------------------------------------------


def test_before_the_slot_the_waiting_page_shows_paris_time(
    backend: Backend, new_page: NewPage
) -> None:
    meeting = backend.create_meeting(starts_in=2 * 3600 + 30, early_join_minutes=10)
    opens = datetime.fromisoformat(meeting["starts_at"]) - timedelta(minutes=10)
    expected = opens.astimezone(PARIS).strftime("%H:%M")
    # Appareil réglé sur un autre fuseau : l'affichage reste en heure de Paris.
    page = new_page(timezone_id="America/New_York")
    open_link(page, backend, meeting["participant_url"])
    page.get_by_test_id("opens-at").wait_for()
    assert page.get_by_test_id("opens-at").inner_text().strip() == expected
    assert "heure de Paris" in page.get_by_test_id("opens-date").inner_text()
    assert re.search(r"dans 1 h \d\d min \d\d s", page.get_by_test_id("countdown").inner_text())
    assert page.get_by_label("Votre nom").count() == 0  # pas d'entrée possible
    assert page.get_by_role("button", name=JOIN).count() == 0


def test_waiting_page_opens_by_itself(backend: Backend, new_page: NewPage) -> None:
    meeting = backend.create_meeting(starts_in=5)
    page = new_page()
    open_link(page, backend, meeting["participant_url"])
    page.get_by_test_id("countdown").wait_for()
    page.get_by_role("button", name=JOIN).wait_for(timeout=25_000)


def test_after_the_slot_the_meeting_is_over(backend: Backend, new_page: NewPage) -> None:
    meeting = backend.create_meeting()
    backend.end_meeting(meeting["id"])
    page = new_page()
    open_link(page, backend, meeting["participant_url"])
    page.get_by_role("heading", name="Réunion terminée").wait_for()
    assert page.get_by_role("button", name=JOIN).count() == 0


def test_cancelled_meeting_says_so(backend: Backend, new_page: NewPage) -> None:
    import httpx

    meeting = backend.create_meeting(starts_in=3600)
    httpx.delete(f"{backend.base_url}/v1/meetings/{meeting['id']}", headers=backend.headers())
    page = new_page()
    open_link(page, backend, meeting["participant_url"])
    page.get_by_role("heading", name="Réunion annulée").wait_for()


# --- marque blanche et mobile ------------------------------------------------------------------


def test_brand_comes_from_the_configuration(backend: Backend, new_page: NewPage) -> None:
    meeting = backend.create_meeting()
    page = new_page()
    open_link(page, backend, meeting["participant_url"])
    page.get_by_role("button", name=JOIN).wait_for()
    assert page.title().endswith("Académie Test")
    assert page.get_by_text("Académie Test").first.is_visible()
    primary = page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('--brand-primary')"
    )
    assert primary.strip() == "#2A9D8F"
    background = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert background == "rgb(20, 33, 61)"  # #14213D


@pytest.mark.parametrize("viewport", [{"width": 390, "height": 780}, {"width": 360, "height": 640}])
def test_pages_fit_a_phone_screen(
    backend: Backend, new_page: NewPage, viewport: dict[str, int]
) -> None:
    meeting = backend.create_meeting()
    waiting = backend.create_meeting(starts_in=3600)
    page = new_page(viewport=viewport, has_touch=True, is_mobile=True)
    for url in (meeting["participant_url"], waiting["participant_url"]):
        open_link(page, backend, url)
        page.wait_for_selector("button.primary, [data-testid=countdown]")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    open_link(page, backend, meeting["participant_url"])
    button = page.get_by_role("button", name=JOIN)
    button.wait_for()
    box = button.bounding_box()
    assert box is not None
    assert box["width"] >= 0.8 * viewport["width"]


def test_room_controls_fit_a_phone_screen(
    backend: Backend, needs_livekit: None, new_page: NewPage
) -> None:
    meeting = backend.create_meeting()
    page = new_page(viewport={"width": 390, "height": 780}, has_touch=True, is_mobile=True)
    open_link(page, backend, meeting["organizer_urls"][0])
    enter_room(page)
    for test_id in ("toggle-mic", "toggle-camera", "toggle-chat", "toggle-people", "leave"):
        box = page.get_by_test_id(test_id).bounding_box()
        assert box is not None, test_id
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390, test_id
        assert box["y"] + box["height"] <= 780, test_id
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
