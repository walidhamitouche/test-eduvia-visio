"""Les deux rôles de la visio : le rôle vient du lien utilisé, jamais d'un compte."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ORGANIZER = "organizer"
    PARTICIPANT = "participant"
