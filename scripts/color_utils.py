"""
color_utils.py — Genereer een volledig kleurenpalet vanuit 1-2 merkkleur(en).

Gebruikt de OKLCH-kleurruimte (perceptueel uniform, ideaal voor paletten).
Geen externe dependencies — pure Python math.

Gebruik:
  from color_utils import generate_palette, palette_to_css
  palette = generate_palette("#2C4A3E", secondary="#C9A96E")
  css = palette_to_css(palette)
"""
from __future__ import annotations
import math
import re


# ── sRGB ↔ Linear RGB ────────────────────────────────────────────────────────

def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(c: float) -> float:
    if c <= 0.0031308:
        return c * 12.92
    return 1.055 * c ** (1.0 / 2.4) - 0.055


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ── Hex ↔ OKLCH ──────────────────────────────────────────────────────────────

def hex_to_oklch(hex_color: str) -> tuple[float, float, float]:
    """Converteert een hex-kleur naar OKLCH (L: 0–1, C: 0–0.4, H: 0–360)."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = h[0]*2 + h[1]*2 + h[2]*2
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0

    r, g, b = _to_linear(r), _to_linear(g), _to_linear(b)

    # Linear RGB → LMS (via M1)
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b

    l, m, s = l ** (1/3), m ** (1/3), s ** (1/3)

    # LMS → OKLab
    L  =  0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s
    a  =  1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
    b_ =  0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s

    C = math.sqrt(a * a + b_ * b_)
    H = math.degrees(math.atan2(b_, a)) % 360
    return L, C, H


def oklch_to_hex(L: float, C: float, H: float) -> str:
    """Converteert OKLCH naar hex. Clampt automatisch buiten-gamut kleuren."""
    h_rad = math.radians(H)
    a  = C * math.cos(h_rad)
    b_ = C * math.sin(h_rad)

    # OKLab → LMS
    l = L + 0.3963377774 * a + 0.2158037573 * b_
    m = L - 0.1055613458 * a - 0.0638541728 * b_
    s = L - 0.0894841775 * a - 1.2914855480 * b_

    l, m, s = l ** 3, m ** 3, s ** 3

    # LMS → Linear RGB
    r  =  4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g  = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    b  = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s

    r = _clamp(_to_srgb(_clamp(r)))
    g = _clamp(_to_srgb(_clamp(g)))
    b = _clamp(_to_srgb(_clamp(b)))

    return "#{:02x}{:02x}{:02x}".format(int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5))


# ── Palet-generator ───────────────────────────────────────────────────────────

def generate_palette(primary_hex: str, secondary_hex: str | None = None) -> dict[str, str]:
    """
    Genereert een volledig DaisyUI-compatibel kleurenpalet vanuit 1-2 merkkleur(en).

    Teruggegevens: dict met DaisyUI CSS-variabelenamen als keys en hex-waarden.

    Logica:
    - primary: de merkkleur zelf
    - primary-content: hoog-contrast versie (licht als primary donker, donker als primary licht)
    - secondary: opgegeven secondaire kleur of automatisch afgeleid (analoog +30° hue)
    - neutral: donkere variant van primary (voor footer/navbar — altijd leesbaar)
    - base-100/200/300: lichte tinten van primary-hue voor achtergronden
    - base-content: donkere tekst
    """
    L, C, H = hex_to_oklch(primary_hex)
    is_dark  = L < 0.55

    # Content-kleur: maximaal contrast met primary
    content_L = 0.96 if is_dark else 0.13
    content_C = max(C * 0.15, 0.005)

    # Secondary
    if secondary_hex:
        sL, sC, sH = hex_to_oklch(secondary_hex)
    else:
        # Analoge kleur: +30° hue, iets hogere chroma
        sL = _clamp(L + 0.08 if is_dark else L - 0.08, 0.25, 0.85)
        sC = _clamp(C * 1.2, 0.04, 0.25)
        sH = (H + 30) % 360
        secondary_hex = oklch_to_hex(sL, sC, sH)

    # Neutral: donker en weinig verzadigd (voor footer)
    neutral_L = _clamp(L * 0.55 if is_dark else 0.25, 0.12, 0.35)
    neutral_C = max(C * 0.4, 0.01)

    # Base-kleuren: heel licht, subtiele hue-tint
    tint_C = max(C * 0.06, 0.005)

    palette = {
        # Primaire merkkleur
        "primary":           primary_hex,
        "primary-content":   oklch_to_hex(content_L, content_C, H),

        # Secundaire kleur
        "secondary":         secondary_hex,
        "secondary-content": oklch_to_hex(content_L, content_C, sH if not secondary_hex else H),

        # Accent = secondary (of licht afwijkende tint)
        "accent":            secondary_hex,
        "accent-content":    oklch_to_hex(content_L, content_C, H),

        # Neutral: altijd donker → footer/navbar altijd leesbaar
        "neutral":           oklch_to_hex(neutral_L, neutral_C, H),
        "neutral-content":   oklch_to_hex(0.96, 0.01, H),

        # Achtergronden: bijna wit met subtiele brand-tint
        "base-100":          oklch_to_hex(0.975, tint_C, H),
        "base-200":          oklch_to_hex(0.945, tint_C * 1.4, H),
        "base-300":          oklch_to_hex(0.910, tint_C * 1.8, H),
        "base-content":      oklch_to_hex(0.15, max(C * 0.25, 0.01), H),

        # Functionele kleuren (generiek, altijd werkend)
        "info":    "#3b82f6",
        "success": "#22c55e",
        "warning": "#f59e0b",
        "error":   "#ef4444",
        "info-content":    "#ffffff",
        "success-content": "#ffffff",
        "warning-content": "#000000",
        "error-content":   "#ffffff",
    }

    return palette


def palette_to_css(palette: dict[str, str], theme_name: str = "brand") -> str:
    """Converteert een palet-dict naar CSS die DaisyUI-variabelen overschrijft."""
    vars_lines = "\n".join(
        f"  --color-{k}: {v};"
        for k, v in palette.items()
    )
    return (
        f"/* Merkspecifiek kleurenpalet — automatisch gegenereerd */\n"
        f"[data-theme=\"{theme_name}\"],\n"
        f":root {{\n"
        f"{vars_lines}\n"
        f"  --rounded-box: 0.375rem;\n"
        f"  --rounded-btn: 0.25rem;\n"
        f"}}\n"
    )


def extract_colors_from_briefing(briefing_text: str) -> tuple[str | None, str | None]:
    """
    Extraheer primaire en secundaire hex-kleuren uit een briefing-markdown.
    Zoekt naar patronen als 'Primaire kleur: #2C4A3E'.
    """
    primary = secondary = None

    patterns = [
        (r"[Pp]rimaire?\s+kleur[:\s]+([#][0-9a-fA-F]{3,6})", "primary"),
        (r"[Ss]ecundaire?\s+kleur[:\s]+([#][0-9a-fA-F]{3,6})", "secondary"),
        (r"[Aa]ccentkleur[:\s]+([#][0-9a-fA-F]{3,6})", "secondary"),
    ]

    for pattern, kind in patterns:
        m = re.search(pattern, briefing_text)
        if m:
            if kind == "primary" and not primary:
                primary = m.group(1)
            elif kind == "secondary" and not secondary:
                secondary = m.group(1)

    return primary, secondary
