"""Archetype interface."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from v2.pipeline.quality_gate import FieldCheck


@dataclass
class ArchetypeMatch:
    """Score + reason waarom een archetype past bij een prospect."""
    score: float
    reasons: list[str]

    @property
    def matched(self) -> bool:
        return self.score >= 1.0


class Archetype(Protocol):
    """Protocol voor archetype-implementaties."""

    name: str

    def detect(self, briefing: str, structured: dict[str, Any], meta: dict[str, Any]) -> ArchetypeMatch:
        """Bereken hoe goed deze archetype past bij de input."""
        ...

    def get_contract(self) -> list[FieldCheck]:
        """Geef de quality-contract checks terug voor dit archetype.

        Het contract bepaalt wat er minimaal in de inventory moet zitten voordat
        we de site mogen bouwen. Geen LLM-calls; deterministische checks op
        velden uit ContentInventory.
        """
        ...

    def build_plan(
        self,
        slug: str,
        collected_path: Path,
        briefing: str,
        structured: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        """Bouw een rijk site_plan dict dat de Astro templates kunnen renderen."""
        ...

    def render(self, plan: dict[str, Any], collected_path: Path, out_dir: Path, force: bool = False) -> None:
        """Render het Astro project naar out_dir (templates copy + assets copy + sitePlan.json)."""
        ...
