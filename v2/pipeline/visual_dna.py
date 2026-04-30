"""Visual DNA extractie: leid een cohesief kleurpalet af uit het beeldmateriaal
van een prospect, zonder LLM en zonder 'disco circus' uitkomst.

Strategie:
  1. Pool kleuren uit logo + hero + portrait + 1-2 sfeerfoto's via ColorThief.
  2. Kies de meest chromatische kleur in een redelijk tone-range als brand-seed.
     (Voorkomt dat we de witte logo-achtergrond als 'merkkleur' nemen.)
  3. Cap de chroma als die extreem is (anti-knal-paletten).
  4. Genereer een volledig palette via Material You's tonal-spot scheme:
     primary, primaryContainer, secondary, tertiary, surface, background,
     onSurface, outline. Allemaal met WCAG-correcte contrasten by design.

Falls back gracefully wanneer dependencies ontbreken: dan retourneert
extract_visual_dna() een VisualDNA met method="unavailable" zodat de caller
op de oude regex-extractie kan terugvallen.
"""
from __future__ import annotations

import io
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# ── Lazy / optional imports ──────────────────────────────────────────────────
try:
    from PIL import Image  # type: ignore
    from colorthief import ColorThief  # type: ignore
    from materialyoucolor.hct import Hct  # type: ignore
    from materialyoucolor.scheme.scheme_tonal_spot import SchemeTonalSpot  # type: ignore
    from materialyoucolor.dynamiccolor.material_dynamic_colors import (  # type: ignore
        MaterialDynamicColors,
    )
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


# ── Data shapes ──────────────────────────────────────────────────────────────
@dataclass
class VisualDNA:
    seed_color: str = ""           # "#RRGGBB"
    seed_hue: float = 0.0
    seed_chroma: float = 0.0
    seed_tone: float = 0.0
    seed_source: str = ""          # bestandsnaam waar de seed uit kwam
    seed_mode: str = ""            # "chromatic" | "low_chroma_fallback" | "unavailable"
    palette: dict[str, str] = field(default_factory=dict)
    pool_size: int = 0             # hoeveel kandidaat-kleuren we hebben gepoold
    method: str = "unavailable"    # "colorthief+materialyoucolor" | "unavailable"
    sources_inspected: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Helpers ──────────────────────────────────────────────────────────────────
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _rgb_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _argb_hex(int_argb: int) -> str:
    return "#{:06X}".format(int_argb & 0xFFFFFF)


def _safe_color_thief(path: Path):
    """ColorThief stikt op alpha-channel images (RGBA -> 'Empty pixels when
    quantize'). Forceer RGB op witte achtergrond."""
    img = Image.open(path)
    if img.mode != "RGB":
        if img.mode in ("RGBA", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            mask = img.split()[-1] if img.mode == "RGBA" else None
            background.paste(img.convert("RGB"), mask=mask)
            img = background
        else:
            img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    buf.seek(0)
    return ColorThief(buf)


def _collect_pool(paths: Iterable[Path], count_per: int = 6) -> list[tuple[tuple[int, int, int], Path]]:
    """Pool kleur-kandidaten uit meerdere afbeeldingen.

    Geeft list van (rgb-tuple, source-path) zodat we provenance kunnen tracken.
    """
    pool: list[tuple[tuple[int, int, int], Path]] = []
    for path in paths:
        if not path.exists() or path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        try:
            ct = _safe_color_thief(path)
            for rgb in ct.get_palette(color_count=count_per, quality=1):
                pool.append((rgb, path))
        except Exception as exc:
            logger.debug("ColorThief failed for %s: %s", path.name, exc)
            continue
    return pool


def _pick_brand_seed(pool, min_chroma: float = 12.0, tone_range: tuple[float, float] = (25.0, 80.0)):
    """Kies de meest chromatische kleur uit de pool met een redelijke tone.

    Returns (rgb, hct, source-path, mode-string).
    Als geen enkele kleur chromatisch genoeg is, valt terug op de minst-grijze.
    """
    chromatic = []
    for rgb, source in pool:
        argb = 0xFF000000 | (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]
        h = Hct.from_int(argb)
        if h.chroma >= min_chroma and tone_range[0] <= h.tone <= tone_range[1]:
            chromatic.append((h.chroma, rgb, h, source))
    if chromatic:
        chromatic.sort(key=lambda x: x[0], reverse=True)
        c = chromatic[0]
        return c[1], c[2], c[3], "chromatic"

    # Fallback: hoogste chroma in de hele pool, ongeacht tone
    fallback = []
    for rgb, source in pool:
        argb = 0xFF000000 | (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]
        h = Hct.from_int(argb)
        fallback.append((h.chroma, rgb, h, source))
    fallback.sort(key=lambda x: x[0], reverse=True)
    f = fallback[0]
    return f[1], f[2], f[3], "low_chroma_fallback"


def _cap_chroma(hct, max_chroma: float = 60.0):
    """Voorkom knal-paletten: cap chroma op een redelijke max."""
    if hct.chroma > max_chroma:
        return Hct.from_hct(hct.hue, max_chroma, hct.tone)
    return hct


def _generate_palette(seed_hct) -> dict[str, str]:
    """Genereer een volledig design-system palette uit een seed via Material You."""
    scheme = SchemeTonalSpot(seed_hct, False, 0.0)  # is_dark=False, contrast=0
    md = MaterialDynamicColors()
    return {
        "primary":          _argb_hex(md.primary.get_argb(scheme)),
        "primaryContainer": _argb_hex(md.primaryContainer.get_argb(scheme)),
        "onPrimary":        _argb_hex(md.onPrimary.get_argb(scheme)),
        "secondary":        _argb_hex(md.secondary.get_argb(scheme)),
        "tertiary":         _argb_hex(md.tertiary.get_argb(scheme)),
        "surface":          _argb_hex(md.surface.get_argb(scheme)),
        "surfaceDim":       _argb_hex(md.surfaceDim.get_argb(scheme)),
        "background":       _argb_hex(md.background.get_argb(scheme)),
        "onSurface":        _argb_hex(md.onSurface.get_argb(scheme)),
        "onSurfaceVariant": _argb_hex(md.onSurfaceVariant.get_argb(scheme)),
        "outline":          _argb_hex(md.outline.get_argb(scheme)),
    }


def _candidate_paths(collected_path: Path, max_extras: int = 4) -> list[Path]:
    """Verzamel beeld-bestanden in voorkeursvolgorde:
    1. logo.* in collected root
    2. logo*-foto's in assets
    3. banner-/hero-achtige assets
    4. eerste paar JPEGs uit assets/uploads of vergelijkbare 'echte' content-mappen
    """
    candidates: list[Path] = []
    seen: set[Path] = set()

    def _add(path: Path) -> None:
        if path in seen or not path.exists():
            return
        candidates.append(path)
        seen.add(path)

    # 1) logo.* in root
    for p in sorted(collected_path.glob("logo.*")):
        if p.suffix.lower() in _IMAGE_SUFFIXES:
            _add(p)

    assets_dir = collected_path / "assets"
    if assets_dir.exists():
        # 2) logo-bestanden ergens in assets
        for p in sorted(assets_dir.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in _IMAGE_SUFFIXES:
                continue
            name = p.name.lower()
            if "logo" in name or "favicon" in name:
                _add(p)
        # 3) banner/hero-achtige bestanden
        for p in sorted(assets_dir.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in _IMAGE_SUFFIXES:
                continue
            name = p.name.lower()
            if any(token in name for token in ("banner", "hero", "header", "splash", "main")):
                _add(p)
            if len(candidates) >= 3:
                break
        # 4) Vul aan tot max_extras met sfeer/foto-assets uit uploads of root
        for p in sorted(assets_dir.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in _IMAGE_SUFFIXES:
                continue
            name = p.name.lower()
            # Filter ruis: bg_noise, sprite, icon, thumbnail
            if any(token in name for token in ("noise", "sprite", "icon", "favicon", "thumbnail")):
                continue
            _add(p)
            if len(candidates) >= 1 + max_extras:
                break

    return candidates[: 1 + max_extras]


# ── Public API ───────────────────────────────────────────────────────────────
def extract_visual_dna(collected_path: Path) -> VisualDNA:
    """Bouw een VisualDNA voor een prospect.

    Geeft VisualDNA(method="unavailable") terug wanneer dependencies ontbreken
    of geen geschikte afbeeldingen gevonden zijn.
    """
    if not _AVAILABLE:
        logger.info("visual_dna: ColorThief / materialyoucolor niet geinstalleerd")
        return VisualDNA(method="unavailable", seed_mode="unavailable")

    paths = _candidate_paths(collected_path)
    if not paths:
        return VisualDNA(method="unavailable", seed_mode="no_images")

    pool = _collect_pool(paths)
    if not pool:
        return VisualDNA(
            method="unavailable",
            seed_mode="extraction_failed",
            sources_inspected=[p.name for p in paths],
        )

    seed_rgb, seed_hct, source_path, mode = _pick_brand_seed(pool)
    seed_hct = _cap_chroma(seed_hct)
    palette = _generate_palette(seed_hct)

    return VisualDNA(
        seed_color=_rgb_hex(seed_rgb),
        seed_hue=round(seed_hct.hue, 1),
        seed_chroma=round(seed_hct.chroma, 1),
        seed_tone=round(seed_hct.tone, 1),
        seed_source=source_path.name,
        seed_mode=mode,
        palette=palette,
        pool_size=len(pool),
        method="colorthief+materialyoucolor",
        sources_inspected=[p.name for p in paths],
    )
