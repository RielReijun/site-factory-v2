"""Personality + micro-signalen: kies sub-stijl op basis van inventory.

Drie signalen tegelijk bepalen het 'aanvoel-pakket' van een prospect:
  Visual DNA (chroma + tone)        — fel of gedempt, donker of licht
  Voice profile (formality + uitroep) — formeel/informeel, energiek/rustig
  Signature type                     — identity/mission/invitation

Output is een Personality met concrete CSS-vars. De render gebruikt deze
om border-radius, schaduw, zinslengte, button-vorm en sectie-padding aan
te passen — vier zelfde-archetype-prospects krijgen daarmee zichtbaar
verschillende 'shapes', niet alleen verschillende kleuren.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Personality:
    name: str = "soft"          # 'soft' | 'sharp' | 'luxe' | 'playful'
    border_radius: str = "10px"  # generic radius voor cards
    button_radius: str = "4px"   # button corner radius
    section_padding: str = "96px"  # vertical section padding
    shadow: str = "soft"         # 'none' | 'soft' | 'dramatic'
    heading_weight: int = 500
    rationale: str = ""          # waarom deze keuze (debug)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_PRESETS: dict[str, dict[str, Any]] = {
    "soft": {
        "border_radius": "10px",
        "button_radius": "4px",
        "section_padding": "96px",
        "shadow": "soft",
        "heading_weight": 500,
    },
    "sharp": {
        "border_radius": "0",
        "button_radius": "0",
        "section_padding": "80px",
        "shadow": "none",
        "heading_weight": 500,
    },
    "luxe": {
        "border_radius": "2px",
        "button_radius": "0",
        "section_padding": "120px",
        "shadow": "dramatic",
        "heading_weight": 400,
    },
    "playful": {
        "border_radius": "20px",
        "button_radius": "999px",
        "section_padding": "80px",
        "shadow": "soft",
        "heading_weight": 600,
    },
}


def pick_personality(inventory: Any) -> Personality:
    """Selecteer een personality-preset op basis van inventory-signalen."""
    dna = getattr(inventory, "visual_dna", None)
    voice = getattr(inventory, "voice_profile", None)

    chroma = getattr(dna, "seed_chroma", 0) or 0
    tone = getattr(dna, "seed_tone", 0) or 0
    formality = getattr(voice, "formality", "gemengd")
    excl_per_1k = getattr(voice, "exclamations_per_1k_words", 0.0) or 0.0

    name = "soft"
    why = []

    # Luxe-dark: donkere, gekleurde seed (warme rood/blauw bij donkere tone)
    if 0 < tone < 40 and chroma > 25:
        name = "luxe"
        why.append(f"tone {tone:.0f}<40 & chroma {chroma:.0f}>25 → luxe-dark")

    # Sharp / clinical: grijzige seed of formele voice
    elif chroma < 18 or formality == "formeel":
        name = "sharp"
        if chroma < 18:
            why.append(f"chroma {chroma:.0f}<18 → sharp/clinical")
        if formality == "formeel":
            why.append("voice formeel → sharp")

    # Playful: hoge chroma + energieke voice (uitroeptekens of invitation)
    elif chroma > 45 and excl_per_1k > 1.5:
        name = "playful"
        why.append(f"chroma {chroma:.0f}>45 + excl/1k {excl_per_1k:.1f}>1.5 → playful")

    else:
        why.append(f"default soft (chroma={chroma:.0f}, tone={tone:.0f}, formality={formality})")

    preset = _PRESETS[name]
    return Personality(
        name=name,
        rationale="; ".join(why),
        **preset,
    )
