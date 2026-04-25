"""
template_engine.py — Template engine voor TSX-componentbestanden.

Syntax (conflicteert niet met JSX-expressies):

  [[ field ]]                   — waarde-substitutie
  [[ ?field ]] ... [[ / ]]      — optioneel blok (render als field truthy)
  [[ *items ]] ... [[ / ]]      — loop, binnenin:
                                    [[ .field ]]      item-eigenschap
                                    [[ ?.field ]]     optioneel item-eigenschap
  [[ ~field ]]                  — veilig Lucide-icon (controleert whitelist)
  [[ ~.field ]]                 — Lucide-icon van item-eigenschap

Nesting: conditionals en loops mogen in elkaar genest zijn.
De tokenizer-gebaseerde parser verwerkt nesting correct.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

COMPONENTS_DIR = Path(__file__).parent.parent / "prompts" / "components"

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


# ── Tokenizer ─────────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r'\[\[\s*(.*?)\s*\]\]', re.DOTALL)


def _tokenize(template: str) -> list[tuple[str, str]]:
    """Splits template in afwisselend TEXT en DIRECTIVE tokens."""
    tokens: list[tuple[str, str]] = []
    last = 0
    for m in _TOKEN_RE.finditer(template):
        if m.start() > last:
            tokens.append(("TEXT", template[last:m.start()]))
        tokens.append(("DIR", m.group(1).strip()))
        last = m.end()
    if last < len(template):
        tokens.append(("TEXT", template[last:]))
    return tokens


# ── Parser ────────────────────────────────────────────────────────────────────

def _parse_block(tokens: list, pos: int) -> tuple[list, int]:
    """
    Parse een blok nodes totdat een [[ / ]] sluit-tag of einde bereikt.
    Geeft (nodes, pos_na_sluit_tag) terug.

    Node-types:
      ("text", str)              — letterlijke tekst
      ("var",  str)              — [[ field ]] of [[ .field ]]
      ("icon", str)              — [[ ~field ]] of [[ ~.field ]]
      ("cond", str, list)        — [[ ?field ]]...nodes...[[ / ]]
      ("loop", str, list)        — [[ *field ]]...nodes...[[ / ]]
    """
    nodes: list = []
    while pos < len(tokens):
        kind, value = tokens[pos]
        if kind == "TEXT":
            nodes.append(("text", value))
            pos += 1
        elif kind == "DIR":
            if value == "/":
                return nodes, pos + 1   # stop, consumeer [[ / ]]
            elif value.startswith("?"):
                field = value[1:].strip()
                body, pos = _parse_block(tokens, pos + 1)
                nodes.append(("cond", field, body))
            elif value.startswith("*"):
                field = value[1:].strip()
                body, pos = _parse_block(tokens, pos + 1)
                nodes.append(("loop", field, body))
            elif value.startswith("~"):
                nodes.append(("icon", value[1:].strip()))
                pos += 1
            else:
                nodes.append(("var", value))
                pos += 1
        else:
            pos += 1
    return nodes, pos


# ── Evaluator ─────────────────────────────────────────────────────────────────

def _get(data: dict | None, key: str, item: dict | None = None) -> Any:
    """Haal waarde op. key kan 'field' of '.field' (item-scope) zijn."""
    if key.startswith("."):
        src   = item or {}
        parts = key[1:].split(".")
    else:
        src   = data or {}
        parts = key.split(".")
    val = src
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p, "")
        else:
            return ""
    return val if val is not None else ""


def _evaluate(nodes: list, data: dict, item: dict | None = None) -> str:
    parts: list[str] = []
    for node in nodes:
        ntype = node[0]
        if ntype == "text":
            parts.append(node[1])
        elif ntype == "var":
            parts.append(str(_get(data, node[1], item)))
        elif ntype == "icon":
            raw = str(_get(data, node[1], item))
            parts.append(safe_icon(raw or node[1].lstrip(".")))
        elif ntype == "cond":
            _, field, body = node
            if _get(data, field, item):
                parts.append(_evaluate(body, data, item))
        elif ntype == "loop":
            _, field, body = node
            items_list = data.get(field, []) if not field.startswith(".") \
                         else (_get(data, field, item) or [])
            if isinstance(items_list, list):
                for loop_item in items_list:
                    parts.append(_evaluate(body, data, loop_item if isinstance(loop_item, dict) else {}))
    return "".join(parts)


# ── Publieke API ──────────────────────────────────────────────────────────────

def render(template: str, data: dict) -> str:
    """Render een template-string met de gegeven data."""
    tokens      = _tokenize(template)
    nodes, _    = _parse_block(tokens, 0)
    return _evaluate(nodes, data)


def collect_icons(tsx: str) -> set[str]:
    """Scan gerenderde TSX op gebruikte Lucide-iconen."""
    found = set()
    for icon in SAFE_ICONS:
        if f"<{icon}" in tsx or f"{{{icon}}}" in tsx:
            found.add(icon)
    return found


def load_template(section_type: str, variant: str) -> str | None:
    """Laad een template-bestand. Valt terug op default.tsx."""
    base = COMPONENTS_DIR / section_type
    for name in [variant, "default"]:
        path = base / f"{name}.tsx"
        if path.exists():
            return path.read_text(encoding="utf-8")
    return None


def list_sections() -> dict[str, list[str]]:
    """Geeft beschikbare sectietypes en varianten."""
    result: dict[str, list[str]] = {}
    if not COMPONENTS_DIR.exists():
        return result
    for d in sorted(COMPONENTS_DIR.iterdir()):
        if d.is_dir():
            variants = [f.stem for f in sorted(d.glob("*.tsx"))]
            if variants:
                result[d.name] = variants
    return result
