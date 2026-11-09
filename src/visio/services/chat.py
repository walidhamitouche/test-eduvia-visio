"""Discussion conservée côté serveur (besoin §5, étape 11).

Les messages **ne passent pas par le navigateur des autres** : l'expéditeur les envoie à
`POST /v1/room/chat`, le backend vérifie qui il est (lien, admission, exclusion), les enregistre
avec **son** nom (pas celui que le message prétend), puis les diffuse à la salle. Personne ne
peut donc usurper un auteur, et ce qui est exporté est exactement ce qui a été vu.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from visio.config import Settings
from visio.db.models import AccessStatus, ChatMessageRow
from visio.domain.errors import DomainError, NotAdmittedError
from visio.services import access
from visio.services.room_admin import authorize
from visio.services.rooms import RoomGateway

logger = logging.getLogger("visio.chat")

CHAT_TOPIC = "chat"


def post_message(
    db: Session,
    settings: Settings,
    gateway: RoomGateway,
    *,
    token: str,
    client_id: str | None,
    text: str,
    now: datetime,
) -> ChatMessageRow:
    caller = authorize(db, settings, token, now=now, organizer_only=False)
    who = access.resolve_identity(
        settings,
        caller.link,
        display_name=caller.link.display_name or "",
        client_id=client_id,
        create_client_id=False,
    )
    entry = access.get_entry(db, caller.meeting.id, who.identity)
    if entry is None or entry.status is not AccessStatus.ADMITTED:
        msg = "vous n'avez pas accès à la discussion de cette réunion"
        raise NotAdmittedError(msg)

    message = ChatMessageRow(
        meeting_id=caller.meeting.id,
        participant_identity=who.identity,
        display_name=entry.display_name,
        external_id=entry.external_id,
        role=entry.role,
        text=text,
        sent_at=now,
    )
    db.add(message)
    db.commit()

    payload = json.dumps(
        {
            "id": message.id,
            "from": message.display_name,
            "role": message.role.value,
            "text": message.text,
            "at": message.sent_at.isoformat(),
        },
        ensure_ascii=False,
    ).encode()
    try:
        gateway.send_data(caller.room, payload, topic=CHAT_TOPIC)
    except DomainError:
        # Le message est conservé (export) ; seule la diffusion en direct a échoué.
        logger.warning("diffusion du message impossible", extra={"meeting_id": caller.meeting.id})
    return message


def list_messages(db: Session, meeting_id: str) -> Sequence[ChatMessageRow]:
    return db.scalars(
        select(ChatMessageRow)
        .where(ChatMessageRow.meeting_id == meeting_id)
        .order_by(ChatMessageRow.sent_at, ChatMessageRow.id)
    ).all()
