"""
template_engine.py — Minimale template engine voor TSX-componentbestanden.

Syntax (conflicteert niet met JSX `{}`-expressies):

  [[ field ]]                   — waarde-substitutie
  [[ ?field ]] ... [[ / ]]      — optioneel blok (render als field truthy)
  [[ *items ]] ... [[ / ]]      — loop over lijst, binnenin:
                                    [[ .field ]]   → item-eigenschap
                                    [[ .field.sub ]] → geneste eigenschap
  [[ ~field ]]                  — veilig Lucide-icon (controleert whitelist)

Bestanden: prompts/components/{type}/{variant}.tsx
Imports worden automatisch gecollecteerd uit de gerenderde TSX.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

COMPONENTS_DIR = Path(__file__).parent.parent / "prompts" / "components"

# Iconen die echt bestaan in lucide-react
SAFE_ICONS = {
    "Phone", "Mail", "MapPin", "Clock", "ChevronRight", "ChevronDown",
    "Check", "Star", "Scissors", "Sparkles", "Heart", "User", "Users",
    "Home", "Building", "Wrench", "Hammer", "Paintbrush", "Truck",
    "Camera", "Image", "Video", "Play", "Globe", "ExternalLink",
    "Menu", "X", "ArrowRight", "ArrowLeft", "Search", "Plus", "Minus",
    "MessageCircle", "Send", "Briefcase", "Shield", "Award", "Leaf",
    "Sun", "Moon", "Coffee", "Smile", "Zap", "Gift", "Calendar",
    "FileText", "Info", "AlertCircle", "CheckCircle", "XCircle",
    "Package", "Settings", "Layers", "Shovel", "Tool",
}


def safe_icon(name: str, fallback: str = "Check") -> str:
    if name in SAFE_ICONS:
        return name
    for s in SAFE_ICONS:
        if s.lower() == name.lower():
            return s
    return fallback


# ── Regex patronen ────────────────────────────────────────────────────────────

# Volgorde is belangrijk: loops en conditionals vóór eenvoudige substitutie
_LOOP_RE  = re.compile(r'\[\[\s*\*(\w+)\s*\]\](.*?)\[\[\s*/\s*\]\]', re.DOTALL)
_COND_RE  = re.compile(r'\[\[\s*\?(\w+)\s*\]\](.*?)\[\[\s*/\s*\]\]', re.DOTALL)
_ICON_RE  = re.compile(r'\[\[\s*~(\w+)\s*\]\]')
_VAR_RE   = re.compile(r'\[\[\s*(\.?\w+(?:\.\w+)*)\s*\]\]')


def _get(data: dict | None, key: str, item: dict | None = None) -> Any:
    """Haal een waarde op. key kan 'field' of '.field' (item-scope) zijn."""
    if key.startswith("."):
        src = item or {}
        parts = key[1:].split(".")
    else:
        src = data or {}
        parts = key.split(".")
    val = src
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p, "")
        else:
            return ""
    return val or ""


def _render_loop(template: str, items: list, data: dict) -> str:
    """Render een loop-blok voor elke item in de lijst."""
    parts = []
    for item in items:
        rendered = _render_inner(template, data, item)
        parts.append(rendered)
    return "\n".join(parts)


def _render_inner(template: str, data: dict, item: dict | None = None) -> str:
    """Render één level van de template (loops al verwerkt op dit punt)."""

    # Conditionals
    def replace_cond(m: re.Match) -> str:
        field   = m.group(1)
        content = m.group(2)
        val     = _get(data, field, item)
        return _render_inner(content, data, item) if val else ""

    result = _COND_RE.sub(replace_cond, template)

    # Icons
    def replace_icon(m: re.Match) -> str:
        return safe_icon(str(_get(data, m.group(1), item)) or m.group(1))

    result = _ICON_RE.sub(replace_icon, result)

    # Eenvoudige variabelen (inclusief .item-velden)
    def replace_var(m: re.Match) -> str:
        return str(_get(data, m.group(1), item))

    result = _VAR_RE.sub(replace_var, result)
    return result


def render(template: str, data: dict) -> str:
    """
    Render een template string met de gegeven data.
    Verwerkt loops, conditionals en substitutie.
    """
    # Loops eerst (buitenste niveau)
    def replace_loop(m: re.Match) -> str:
        field    = m.group(1)
        body     = m.group(2)
        items    = data.get(field, [])
        if not isinstance(items, list):
            return ""
        return _render_loop(body, items, data)

    result = _LOOP_RE.sub(replace_loop, template)

    # Dan conditionals en substitutie
    result = _render_inner(result, data)

    return result


def collect_icons(tsx: str) -> set[str]:
    """Scan gerenderde TSX op gebruikte Lucide-iconen."""
    found = set()
    for icon in SAFE_ICONS:
        if f"<{icon}" in tsx or f"{{{icon}}}" in tsx:
            found.add(icon)
    return found


def load_template(section_type: str, variant: str) -> str | None:
    """
    Laad een template-bestand.
    Zoekt: prompts/components/{type}/{variant}.tsx
    Valt terug op: prompts/components/{type}/default.tsx
    """
    base = COMPONENTS_DIR / section_type
    for name in [variant, "default"]:
        path = base / f"{name}.tsx"
        if path.exists():
            return path.read_text(encoding="utf-8")
    return None


def list_sections() -> dict[str, list[str]]:
    """Geeft een overzicht van beschikbare sectietypes en varianten."""
    result: dict[str, list[str]] = {}
    if not COMPONENTS_DIR.exists():
        return result
    for section_dir in sorted(COMPONENTS_DIR.iterdir()):
        if not section_dir.is_dir():
            continue
        variants = [f.stem for f in sorted(section_dir.glob("*.tsx"))]
        if variants:
            result[section_dir.name] = variants
    return result
