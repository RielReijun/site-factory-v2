"""Typography extraction uit raw.html.

Vindt welke fonts de bronsite werkelijk gebruikt — meestal Google Fonts via
<link href="...fonts.googleapis.com/css?family=Foo|Bar"> of inline
font-family declarations. Geeft een Typography-shape terug die in render
de generieke Playfair+Inter default vervangt.

Geen LLM, alleen regex. Defaults blijven beschikbaar voor wanneer er niets
extraheerbaar is.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import unquote


# ── Data ─────────────────────────────────────────────────────────────────────
@dataclass
class Typography:
    heading_font: str = ""
    body_font: str = ""
    google_fonts: list[str] = field(default_factory=list)
    inline_fonts: list[str] = field(default_factory=list)
    method: str = "default"          # "extracted" | "default"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Regex ────────────────────────────────────────────────────────────────────
# Google Fonts CSS API — twee versies:
#   v1: ?family=Inter|Playfair+Display:400,700&display=swap
#   v2: ?family=Inter:wght@400;700&family=Playfair+Display:wght@500&display=swap
_GOOGLE_FONTS_RE = re.compile(
    r"fonts\.googleapis\.com/css2?\?[^\"'>]*?family=([^\"&']+)",
    re.IGNORECASE,
)
_GOOGLE_FONTS_V2_FAM_RE = re.compile(r"family=([^&]+)", re.IGNORECASE)

# Inline font-family declaratie — pak het EERSTE alternatief, want dat is
# meestal de gewenste font (rest is fallback).
_FONT_FAMILY_RE = re.compile(
    r"font-family\s*:\s*(['\"]?)([\w\s,'\"-]+?)\1\s*[;}]",
    re.IGNORECASE,
)
_HEADING_SELECTOR_RE = re.compile(
    r"(h[1-6]|\.heading|\.title|\.hero[-_ ]?title|header)\s*\{[^}]*font-family\s*:\s*[\"']?([\w\s,'\"-]+)",
    re.IGNORECASE | re.DOTALL,
)


# Generic fonts die we niet als 'extracted' willen rapporteren — die
# zeggen niets over brand-character. Icoon-fonts ook eruit (Font Awesome,
# Material Icons, etc.) want dat zijn geen tekst-fonts.
_GENERIC_FONTS = {
    "inherit", "initial", "unset", "revert", "currentcolor",
    "sans-serif", "serif", "monospace", "cursive", "fantasy", "system-ui",
    "ui-sans-serif", "ui-serif", "ui-monospace", "ui-rounded",
    "-apple-system", "blinkmacsystemfont", "segoe ui", "roboto", "helvetica",
    "arial", "georgia",
}
_ICON_FONT_PATTERNS = [
    re.compile(r"font\s*awesome", re.IGNORECASE),
    re.compile(r"material\s*icons", re.IGNORECASE),
    re.compile(r"glyphicons", re.IGNORECASE),
    re.compile(r"^fa[-_]", re.IGNORECASE),
    re.compile(r"^icon", re.IGNORECASE),
    re.compile(r"^ionic", re.IGNORECASE),
    re.compile(r"fontello", re.IGNORECASE),
    re.compile(r"feather", re.IGNORECASE),
    re.compile(r"dashicons", re.IGNORECASE),
]


def _clean_font_name(raw: str) -> str:
    name = raw.strip().strip("'\"").strip()
    # Pak alleen het eerste alternatief
    if "," in name:
        name = name.split(",", 1)[0].strip().strip("'\"").strip()
    return name


def _is_real_brand_font(name: str) -> bool:
    if not name or len(name) < 3 or len(name) > 50:
        return False
    if name.lower() in _GENERIC_FONTS:
        return False
    if any(p.search(name) for p in _ICON_FONT_PATTERNS):
        return False
    return True


def _extract_google_fonts(html: str) -> list[str]:
    """Vindt unique Google Font-families in href-strings."""
    fonts: list[str] = []
    seen: set[str] = set()
    for match in _GOOGLE_FONTS_RE.finditer(html):
        family_blob = unquote(match.group(1))
        # v1: families gescheiden door | ; v2: één family per match (we nemen
        # de hele blob en splitsen op | als die er is)
        for chunk in family_blob.split("|"):
            # 'Inter:wght@400;700' → 'Inter'
            name_part = chunk.split(":")[0]
            name = name_part.replace("+", " ").strip()
            if not _is_real_brand_font(name):
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            fonts.append(name)
    # Ook v2 met meerdere &family=...
    for match in _GOOGLE_FONTS_V2_FAM_RE.finditer(html):
        chunk = unquote(match.group(1))
        name_part = chunk.split(":")[0]
        name = name_part.replace("+", " ").strip()
        if not _is_real_brand_font(name):
            continue
        key = name.lower()
        if key in seen:
            continue
        # Skip inhoud die op een Google CSS-pad lijkt
        if "/" in name or "?" in name:
            continue
        seen.add(key)
        fonts.append(name)
    return fonts


def _extract_inline_fonts(html: str) -> list[str]:
    """Vindt unique font-family waardes in inline of <style>-CSS."""
    fonts: list[str] = []
    seen: set[str] = set()
    for match in _FONT_FAMILY_RE.finditer(html):
        name = _clean_font_name(match.group(2))
        if not _is_real_brand_font(name):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        fonts.append(name)
    return fonts


def _heading_font_from_css(html: str) -> str:
    """Probeer de heading-font te vinden via CSS-selectors als h1, .title."""
    for match in _HEADING_SELECTOR_RE.finditer(html):
        name = _clean_font_name(match.group(2))
        if _is_real_brand_font(name):
            return name
    return ""


# ── Public API ───────────────────────────────────────────────────────────────
def extract_typography(raw_html: str) -> Typography:
    if not raw_html:
        return Typography()

    google = _extract_google_fonts(raw_html)
    inline = _extract_inline_fonts(raw_html)
    css_heading = _heading_font_from_css(raw_html)

    # Heuristiek voor heading vs body:
    #  - Eerste Google Font is meestal heading (display/serif), tweede body
    #  - Als CSS-selector een heading-font specificeert, gebruik die
    #  - Anders: eerste inline font als heading, tweede als body
    heading: str = ""
    body: str = ""

    if css_heading:
        heading = css_heading
    elif google:
        heading = google[0]
    elif inline:
        heading = inline[0]

    if google and len(google) > 1:
        body = google[1] if google[1].lower() != heading.lower() else (google[0] if heading != google[0] else "")
    if not body and inline:
        for cand in inline:
            if cand.lower() != heading.lower():
                body = cand
                break

    method = "extracted" if (heading or body) else "default"
    return Typography(
        heading_font=heading,
        body_font=body,
        google_fonts=google,
        inline_fonts=inline[:6],   # cap voor leesbaarheid in JSON
        method=method,
    )
