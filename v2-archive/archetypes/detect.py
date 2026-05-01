"""Archetype detectie. Iterate over geregistreerde archetypes, kies hoogst scorende match."""
from __future__ import annotations

from typing import Any

from .base import Archetype
from .beauty_wellness import BeautyWellnessArchetype
from .local_service import LocalServiceArchetype


def registered_archetypes() -> list[Archetype]:
    """Volgorde bepaalt tie-break: eerste komt voor bij gelijke score."""
    return [LocalServiceArchetype(), BeautyWellnessArchetype()]


def detect_archetype(
    briefing: str,
    structured: dict[str, Any],
    meta: dict[str, Any],
) -> Archetype | None:
    """Geef beste passende archetype terug, of None als niets matcht."""
    best: tuple[float, Archetype] | None = None
    for archetype in registered_archetypes():
        result = archetype.detect(briefing, structured, meta)
        if not result.matched:
            continue
        if best is None or result.score > best[0]:
            best = (result.score, archetype)
    return best[1] if best else None
