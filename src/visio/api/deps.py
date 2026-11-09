"""Dépendances FastAPI : session de base de données, horloge. Tout vient de `app.state`,
rempli au démarrage (`create_app`), ce qui permet d'injecter une horloge simulée en test."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from visio.config import Settings
from visio.domain.clock import Clock
from visio.services.egress import RecorderGateway
from visio.services.rooms import RoomGateway
from visio.services.storage import ObjectStore


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def get_clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_rooms(request: Request) -> RoomGateway:
    rooms: RoomGateway = request.app.state.rooms
    return rooms


def get_recorder(request: Request) -> RecorderGateway:
    recorder: RecorderGateway = request.app.state.recorder
    return recorder


def get_storage(request: Request) -> ObjectStore:
    storage: ObjectStore = request.app.state.storage
    return storage


DbSession = Annotated[Session, Depends(get_db)]
AppClock = Annotated[Clock, Depends(get_clock)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]
Rooms = Annotated[RoomGateway, Depends(get_rooms)]
Recorder = Annotated[RecorderGateway, Depends(get_recorder)]
Objects = Annotated[ObjectStore, Depends(get_storage)]
