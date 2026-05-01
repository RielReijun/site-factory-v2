"""Archetype registry voor v2.

Een archetype combineert:
- detectie (matcht een collected prospect een specifieke business-vorm?)
- dataverrijking (welke secties bouwt deze archetype op uit de briefing?)
- rendering (welke Astro template-set wordt gebruikt?)

Archetypes zijn deterministisch: ze leiden alle content af uit de bestaande
collected data, nooit via een runtime LLM-call.
"""
from __future__ import annotations

from .base import Archetype, ArchetypeMatch
from .beauty_wellness import BeautyWellnessArchetype
from .local_service import LocalServiceArchetype
from .detect import detect_archetype, registered_archetypes

__all__ = [
    "Archetype",
    "ArchetypeMatch",
    "BeautyWellnessArchetype",
    "LocalServiceArchetype",
    "detect_archetype",
    "registered_archetypes",
]
