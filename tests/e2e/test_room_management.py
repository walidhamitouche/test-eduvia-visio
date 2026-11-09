"""Étapes 8 à 12 dans un vrai navigateur, avec un vrai serveur de médias et le vrai backend.

- Scénario 3 : l'organisateur coupe un micro et exclut un participant, qui ne peut pas revenir
  (trois navigateurs, puis un co-animateur) ;
- Scénario 4 : salle d'attente, « tout admettre », refus, personne n'entre sans organisateur ;
- Scénario 1 en réel : avertissement avant la fin, coupure à l'heure, réunion terminée ;
- Scénarios 5, 6 (webhooks signés) et 7 : chat et présence exportables, cohérents avec la séance.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from tests.e2e.conftest import Backend, NewPage
from tests.e2e.test_interface import JOIN, enter_room, open_link, token_of

TWO_ORGANIZERS = [
    {"display_name": "Marie Durand", "external_id": "staff_42"},
    {"display_name": "Paul Martin", "external_id": "staff_43"},
]


def wait_until(check: Callable[[], bool], *, seconds: float = 20, what: str = "condition") -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.25)
    msg = f"{what} : non atteinte après {seconds} s"
    raise AssertionError(msg)


def tiles(page: Any, count: int) -> None:
    page.wait_for_function(f"document.querySelectorAll('[data-testid=tile]').length === {count}")


def person(page: Any, name: str) -> Any:
    return page.locator(f'[data-testid=people-panel] [data-person="{name}"]')


def open_people(page: Any) -> None:
    if page.get_by_test_id("people-panel").count() == 0:
        page.get_by_test_id("toggle-people").click()
    page.get_by_test_id("people-panel").wait_for()


def join_generic(page: Any, backend: Backend, url: str, name: str) -> None:
    open_link(page, backend, url)
    enter_room(page, name=name)


# --- scénario 3 : modération --------------------------------------------------------------------


def test_scenario_3_organizer_mutes_then_excludes_and_the_person_cannot_return(
    backend: Backend, needs_livekit: None, new_page: NewPage
) -> None:
    meeting = backend.create_meeting(recording=False, organizers=TWO_ORGANIZERS)
    marie, lina, karim = new_page(), new_page(), new_page()
    open_link(marie, backend, meeting["organizer_urls"][0])
    enter_room(marie)
    join_generic(lina, backend, meeting["participant_url"], "Lina Benali")
    join_generic(karim, backend, meeting["participant_url"], "Karim Zidane")
    for page in (marie, lina, karim):
        tiles(page, 3)

    # Couper le micro de Lina : son bouton passe en « Activer le micro ».
    open_people(marie)
    person(marie, "Lina Benali").get_by_test_id("mute-mic").click()
    lina.get_by_role("button", name="Activer le micro").wait_for()
    marie.locator('[data-name="Lina Benali"]').get_by_text("Micro coupé").first.wait_for(
        state="attached"
    )
    # … et sa caméra.
    person(marie, "Lina Benali").get_by_test_id("mute-camera").click()
    lina.get_by_role("button", name="Activer la caméra").wait_for()

    # Exclure Karim : confirmation, puis il est retiré.
    person(marie, "Karim Zidane").get_by_test_id("remove").click()
    marie.get_by_test_id("remove-confirm").click()
    karim.get_by_role("heading", name="Vous avez été retiré de la réunion").wait_for()
    tiles(marie, 2)
    tiles(lina, 2)

    # Il ne peut pas revenir avec le même lien, même en rechargeant la page.
    karim.reload()
    karim.get_by_label("Votre nom").fill("Karim Zidane")
    karim.get_by_role("button", name=JOIN).click()
    karim.get_by_role("heading", name="Accès retiré").wait_for()
    assert karim.get_by_test_id("room").count() == 0
    # Lina, elle, n'est pas touchée et reste dans la salle.
    assert lina.get_by_test_id("room").is_visible()


def test_a_participant_has_no_moderation_and_the_server_refuses_him_too(
    backend: Backend, needs_livekit: None, new_page: NewPage
) -> None:
    meeting = backend.create_meeting(recording=False)
    marie, lina = new_page(), new_page()
    open_link(marie, backend, meeting["organizer_urls"][0])
    enter_room(marie)
    join_generic(lina, backend, meeting["participant_url"], "Lina Benali")
    tiles(lina, 2)

    open_people(lina)
    assert lina.get_by_test_id("remove").count() == 0
    assert lina.get_by_test_id("mute-mic").count() == 0
    assert lina.get_by_test_id("end-meeting").count() == 0

    # Même en parlant directement à l'API avec son propre lien : 403.
    for route, body in (
        ("remove", {"identity": "pg_" + "0" * 32}),
        ("mute", {"identity": "pg_" + "0" * 32, "source": "microphone"}),
        ("end", {}),
    ):
        response = httpx.post(
            f"{backend.base_url}/v1/room/{route}",
            json={"token": token_of(meeting["participant_url"]), **body},
        )
        assert response.status_code == 403, route
        assert response.json()["error"]["code"] == "organizer_required"
    assert lina.get_by_test_id("room").is_visible()  # personne n'a été touché


def test_co_organizer_moderates_and_organizers_are_not_moderable(
    backend: Backend, needs_livekit: None, new_page: NewPage
) -> None:
    meeting = backend.create_meeting(recording=False, organizers=TWO_ORGANIZERS)
    marie, paul, lina = new_page(), new_page(), new_page()
    open_link(marie, backend, meeting["organizer_urls"][0])
    enter_room(marie)
    open_link(paul, backend, meeting["organizer_urls"][1])
    enter_room(paul)
    join_generic(lina, backend, meeting["participant_url"], "Lina Benali")
    for page in (marie, paul, lina):
        tiles(page, 3)

    open_people(paul)
    # Pas de boutons de modération sur un autre organisateur, ni sur soi-même.
    assert person(paul, "Marie Durand").get_by_test_id("remove").count() == 0
    assert person(paul, "Paul Martin").get_by_test_id("remove").count() == 0
    # Le co-animateur exclut Lina.
    person(paul, "Lina Benali").get_by_test_id("remove").click()
    paul.get_by_test_id("remove-confirm").click()
    lina.get_by_role("heading", name="Vous avez été retiré de la réunion").wait_for()
    tiles(marie, 2)


def test_ending_the_meeting_early_disconnects_everyone(
    backend: Backend, needs_livekit: None, new_page: NewPage
) -> None:
    meeting = backend.create_meeting(recording=False)
    marie, lina = new_page(), new_page()
    open_link(marie, backend, meeting["organizer_urls"][0])
    enter_room(marie)
    join_generic(lina, backend, meeting["participant_url"], "Lina Benali")
    tiles(lina, 2)

    marie.get_by_test_id("end-meeting").click()
    marie.get_by_test_id("end-dialog").wait_for()
    marie.get_by_test_id("end-confirm").click()

    for page in (lina, marie):
        page.get_by_role("heading", name="Réunion terminée").wait_for(timeout=20_000)
    assert backend.get(f"/v1/meetings/{meeting['id']}")["status"] == "ended"
    late = new_page()
    open_link(late, backend, meeting["participant_url"])
    late.get_by_role("heading", name="Réunion terminée").wait_for()


# --- scénario 4 : salle d'attente ---------------------------------------------------------------


def test_scenario_4_the_participant_enters_only_after_admission(
    backend: Backend, needs_livekit: None, new_page: NewPage
) -> None:
    meeting = backend.create_meeting(recording=False, waiting_room=True)
    marie, lina = new_page(), new_page()
    open_link(marie, backend, meeting["organizer_urls"][0])
    enter_room(marie)  # l'organisateur n'attend pas

    open_link(lina, backend, meeting["participant_url"])
    lina.get_by_text("L'organisateur devra vous admettre").wait_for()  # annoncé dès la pré-jointure
    lina.get_by_label("Votre nom").fill("Lina Benali")
    lina.get_by_role("button", name=JOIN).click()
    lina.get_by_role("heading", name="En attente d'admission").wait_for()
    assert lina.get_by_test_id("room").count() == 0

    # L'organisateur voit la demande (avec le nom) et le compteur.
    marie.get_by_test_id("toggle-lobby").click()
    panel = marie.get_by_test_id("lobby-panel")
    panel.locator('[data-person="Lina Benali"]').wait_for()
    marie.get_by_label("Admettre Lina Benali").click()

    lina.get_by_test_id("room").wait_for(timeout=15_000)  # entre toute seule
    tiles(marie, 2)
    tiles(lina, 2)
    assert panel.locator('[data-person="Lina Benali"]').count() == 0


def test_admit_all_lets_several_people_in_with_one_click(
    backend: Backend, needs_livekit: None, new_page: NewPage
) -> None:
    meeting = backend.create_meeting(recording=False, waiting_room=True)
    marie = new_page()
    open_link(marie, backend, meeting["organizer_urls"][0])
    enter_room(marie)
    waiting = []
    for name in ("Lina Benali", "Karim Zidane", "Sonia Roy"):
        page = new_page()
        open_link(page, backend, meeting["participant_url"])
        page.get_by_label("Votre nom").fill(name)
        page.get_by_role("button", name=JOIN).click()
        page.get_by_role("heading", name="En attente d'admission").wait_for()
        waiting.append(page)

    marie.get_by_test_id("toggle-lobby").click()
    marie.get_by_test_id("admit-all").wait_for()
    marie.get_by_test_id("admit-all").click()

    for page in waiting:
        page.get_by_test_id("room").wait_for(timeout=15_000)
    tiles(marie, 4)


def test_a_refused_participant_stays_out(
    backend: Backend, needs_livekit: None, new_page: NewPage
) -> None:
    meeting = backend.create_meeting(recording=False, waiting_room=True)
    marie, karim = new_page(), new_page()
    open_link(marie, backend, meeting["organizer_urls"][0])
    enter_room(marie)
    open_link(karim, backend, meeting["participant_url"])
    karim.get_by_label("Votre nom").fill("Karim Zidane")
    karim.get_by_role("button", name=JOIN).click()
    karim.get_by_role("heading", name="En attente d'admission").wait_for()

    marie.get_by_test_id("toggle-lobby").click()
    marie.get_by_label("Refuser Karim Zidane").click()
    karim.get_by_role("heading", name="Accès refusé").wait_for(timeout=15_000)

    # Recharger ne contourne pas le refus : il n'a pas de nouvelle décision en sa faveur.
    karim.reload()
    karim.get_by_label("Votre nom").fill("Karim Zidane")
    karim.get_by_role("button", name=JOIN).click()
    karim.get_by_role("heading", name="Accès refusé").wait_for(timeout=15_000)
    assert karim.get_by_test_id("room").count() == 0


def test_without_an_organizer_nobody_enters_the_waiting_room(
    backend: Backend, needs_livekit: None, new_page: NewPage
) -> None:
    meeting = backend.create_meeting(recording=False, waiting_room=True)
    pages = []
    for name in ("Lina Benali", "Karim Zidane"):
        page = new_page()
        open_link(page, backend, meeting["participant_url"])
        page.get_by_label("Votre nom").fill(name)
        page.get_by_role("button", name=JOIN).click()
        page.get_by_role("heading", name="En attente d'admission").wait_for()
        pages.append(page)
    time.sleep(6)  # plusieurs relectures de la demande : rien ne change
    for page in pages:
        assert page.get_by_role("heading", name="En attente d'admission").is_visible()
        assert page.get_by_test_id("room").count() == 0


def test_without_a_waiting_room_the_participant_enters_directly(
    backend: Backend, needs_livekit: None, new_page: NewPage
) -> None:
    meeting = backend.create_meeting(recording=False, waiting_room=False)
    lina = new_page()
    join_generic(lina, backend, meeting["participant_url"], "Lina Benali")
    lina.get_by_test_id("room").wait_for()


# --- scénario 1 en réel : avertissement et coupure ----------------------------------------------


def test_scenario_1_warning_then_cut_off_at_the_end_of_the_slot(
    backend: Backend, needs_livekit: None, new_page: NewPage
) -> None:
    meeting = backend.create_meeting(starts_in=-5, lasts=50, recording=False)
    marie, lina = new_page(), new_page()
    open_link(marie, backend, meeting["organizer_urls"][0])
    enter_room(marie)
    join_generic(lina, backend, meeting["participant_url"], "Lina Benali")
    tiles(lina, 2)

    # À moins de 5 minutes de la fin, l'avertissement est visible, avec un compte à rebours.
    for page in (marie, lina):
        warning = page.get_by_test_id("end-warning")
        warning.wait_for()
        assert "La réunion" in warning.inner_text()
    # À moins d'une minute : le texte passe à l'urgence.
    lina.get_by_test_id("end-warning").get_by_text("va se terminer").wait_for()

    # À l'heure, tout le monde est déconnecté, sans rien faire.
    for page in (marie, lina):
        page.get_by_role("heading", name="Réunion terminée").wait_for(timeout=45_000)
    assert backend.get(f"/v1/meetings/{meeting['id']}")["status"] == "ended"

    # Et on ne peut plus rejoindre.
    late = new_page()
    open_link(late, backend, meeting["participant_url"])
    late.get_by_role("heading", name="Réunion terminée").wait_for()
    assert late.get_by_role("button", name=JOIN).count() == 0

    # L'événement de fin arrive chez Eduvia, avec la raison.
    def ended() -> bool:
        return any(
            e["type"] == "meeting.ended" and e["meeting"]["id"] == meeting["id"]
            for e in backend.receiver.events()
        )

    wait_until(ended, what="webhook meeting.ended")
    event = next(
        e
        for e in backend.receiver.events()
        if e["type"] == "meeting.ended" and e["meeting"]["id"] == meeting["id"]
    )
    assert event["data"]["reason"] == "slot_end"


# --- scénarios 5, 6 (webhooks) et 7 : présence, chat, événements --------------------------------


def test_scenarios_5_6_7_presence_chat_and_signed_webhooks_match_the_session(
    backend: Backend, needs_livekit: None, new_page: NewPage
) -> None:
    metadata = {"eduvia_session_id": 4242}
    meeting = backend.create_meeting(recording=False, metadata=metadata)
    lina_url = backend.create_link(
        meeting["id"], role="participant", display_name="Lina Benali", external_id="stu_981"
    )
    marie, lina = new_page(), new_page()
    open_link(marie, backend, meeting["organizer_urls"][0])
    enter_room(marie)
    open_link(lina, backend, lina_url)
    enter_room(lina)
    tiles(marie, 2)

    # Discussion dans les deux sens.
    for page, text in ((marie, "Bonjour, on commence"), (lina, "Bonjour Marie")):
        page.get_by_test_id("toggle-chat").click()
        page.get_by_label("Écrire un message").fill(text)
        page.get_by_role("button", name="Envoyer").click()
    marie.get_by_test_id("chat-panel").get_by_text("Bonjour Marie").wait_for()
    lina.get_by_test_id("chat-panel").get_by_text("Bonjour, on commence").wait_for()
    time.sleep(1.2)  # deux horodatages distincts, dans l'ordre

    # Lina sort puis revient (deux intervalles de présence).
    lina.get_by_test_id("leave").click()
    lina.get_by_role("heading", name="Vous avez quitté la réunion").wait_for()
    tiles(marie, 1)
    time.sleep(1.5)
    lina.get_by_role("button", name="Revenir dans la réunion").click()
    enter_room(lina)
    tiles(marie, 2)
    time.sleep(1.5)

    marie.get_by_test_id("end-meeting").click()
    marie.get_by_test_id("end-confirm").click()
    marie.get_by_role("heading", name="Réunion terminée").wait_for(timeout=20_000)

    meeting_id = meeting["id"]

    def mine() -> list[dict[str, Any]]:
        return [e for e in backend.receiver.events() if e["meeting"]["id"] == meeting_id]

    wait_until(lambda: any(e["type"] == "meeting.ended" for e in mine()), what="meeting.ended")

    # Présence : deux entrées pour Lina (nom et identifiant du lien nominatif), durée = somme.
    report = backend.get(f"/v1/meetings/{meeting_id}/attendance")
    by_name = {p["display_name"]: p for p in report["participants"]}
    lina_report = by_name["Lina Benali"]
    assert lina_report["external_id"] == "stu_981"  # scénario 5
    assert len(lina_report["sessions"]) == 2
    assert all(s["left_at"] for s in lina_report["sessions"])
    assert (
        abs(lina_report["total_seconds"] - sum(s["seconds"] for s in lina_report["sessions"])) <= 2
    )
    assert lina_report["total_seconds"] >= 2
    assert by_name["Marie Durand"]["role"] == "organizer"
    assert len(by_name["Marie Durand"]["sessions"]) == 1

    # Chat : exactement ce qui a été écrit, dans l'ordre, avec les auteurs vérifiés.
    chat = backend.get(f"/v1/meetings/{meeting_id}/chat")["messages"]
    assert {(m["author"]["display_name"], m["text"]) for m in chat} == {
        ("Marie Durand", "Bonjour, on commence"),
        ("Lina Benali", "Bonjour Marie"),
    }
    assert [m["sent_at"] for m in chat] == sorted(m["sent_at"] for m in chat)

    # Webhooks : l'ordre de la séance, les metadata, la signature.
    types = [e["type"] for e in mine()]
    assert types[0] == "meeting.started"
    assert types.count("participant.joined") == 3  # Marie, Lina, Lina de retour
    assert types.count("participant.left") == 3  # Lina, puis Marie et Lina à la clôture
    assert types[-1] == "meeting.ended"
    assert all(e["meeting"]["metadata"] == metadata for e in mine())
    assert backend.receiver.valid_signatures()
    first_left = next(e for e in mine() if e["type"] == "participant.left")
    assert first_left["data"]["participant"]["external_id"] == "stu_981"
    assert len({e["id"] for e in mine()}) == len(mine())  # identifiants d'événement uniques
