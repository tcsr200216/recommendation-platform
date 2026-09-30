from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.recommender import Interaction


class InteractionRepository(Protocol):
    """Persistence boundary for user-item interactions."""

    def add(self, interaction: Interaction) -> None:
        """Persist one interaction."""
        ...

    def list_all(self) -> Sequence[Interaction]:
        """Return a stable snapshot of all recorded interactions."""
        ...


class InMemoryInteractionRepository:
    """Process-local repository used by the MVP and tests."""

    def __init__(self) -> None:
        self._interactions: list[Interaction] = []

    def add(self, interaction: Interaction) -> None:
        self._interactions.append(interaction)

    def list_all(self) -> tuple[Interaction, ...]:
        return tuple(self._interactions)
