"""
component_registry.py — Assembleert pagina's vanuit file-based templates.

Templates staan in prompts/components/{type}/{variant}.tsx
Nieuwe sectie = nieuw .tsx bestand, geen Python-code nodig.

Gebruik:
  from component_registry import assemble_page, list_sections
  tsx = assemble_page(plan, page_slug, company_name)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Zorg dat template_engine beschikbaar is
sys.path.insert(0, str(Path(__file__).parent))
from template_engine import (
    render, load_template, collect_icons, list_sections, safe_icon, SAFE_ICONS
)

SHADCN_IMPORTS = {
    "Accordion": 'import { Accordion, AccordionItem, AccordionTrigger, AccordionContent } from "@/components/ui/accordion";',
    "Badge":     'import { Badge } from "@/components/ui/badge";',
    "Button":    'import { Button } from "@/components/ui/button";',
}

ALWAYS_IMPORTS = ['import Link from "next/link";']


def _prepare_content(raw: dict, section_type: str) -> dict:
    """
    Normaliseert content voordat die naar de template gaat.
    Voegt index toe aan lijstitems, filtert lege waarden, etc.
    """
    data = dict(raw)

    # Voeg index toe aan FAQ items
    if section_type == "faq" and "items" in data:
        data["items"] = [
            {**item, "index": str(i)}
            for i, item in enumerate(data["items"])
        ]

    # Converteer bullets (list of strings) naar list of dicts
    if "bullets" in data and data["bullets"]:
        if isinstance(data["bullets"][0], str):
            data["bullets"] = [{"value": b} for b in data["bullets"]]

    # Converteer images (list of strings) naar list of dicts
    if "images" in data and data["images"]:
        if isinstance(data["images"][0], str):
            data["images"] = [{"src": img, "alt": ""} for img in data["images"]]

    # Verwijder lege strings zodat [[ ?field ]] conditionals correct werken
    return {k: v for k, v in data.items() if v != "" and v is not None}


def render_section(section_type: str, variant: str, content: dict) -> tuple[str, set[str]]:
    """
    Rendert één sectie.
    Geeft (tsx_string, set_van_gebruikte_lucide_icons) terug.
    Leeg string als template niet gevonden.
    """
    template = load_template(section_type, variant)
    if not template:
        return "", set()

    data  = _prepare_content(content, section_type)
    tsx   = render(template, data)
    icons = collect_icons(tsx)
    return tsx.strip(), icons


def assemble_page(plan: dict, page_slug: str, company_name: str) -> str:
    """
    Assembleert een complete page.tsx vanuit een JSON-plan.

    plan = {
        "sections": [
            {"type": "hero", "variant": "full-overlay", "content": {...}},
            ...
        ]
    }
    """
    sections    = plan.get("sections", [])
    all_icons:  set[str] = set()
    all_shadcn: set[str] = set()
    body_parts: list[str] = []

    for sec in sections:
        sec_type = sec.get("type", "")
        variant  = sec.get("variant", "default")
        content  = sec.get("content", {})

        tsx, icons = render_section(sec_type, variant, content)
        if tsx:
            body_parts.append(f"      {tsx}")
            all_icons.update(icons)

        # Detecteer shadcn-componenten
        if "AccordionItem" in tsx:
            all_shadcn.add("Accordion")

    # Bouw functienaam
    fn_name = "".join(w.capitalize() for w in re.sub(r"[^a-zA-Z0-9]", " ", page_slug).split()) or "Page"
    fn_name += "Page"

    # Bouw imports
    imports = list(ALWAYS_IMPORTS)
    safe = sorted(i for i in all_icons if i in SAFE_ICONS)
    if safe:
        imports.append(f'import {{ {", ".join(safe)} }} from "lucide-react";')
    for key in sorted(all_shadcn):
        if key in SHADCN_IMPORTS:
            imports.append(SHADCN_IMPORTS[key])

    body = "\n".join(body_parts) if body_parts else "      <div />"

    return "\n".join(imports) + f"""

export default function {fn_name}() {{
  return (
    <main>
{body}
    </main>
  );
}}
"""


def get_schema_description() -> str:
    """Beschrijving van beschikbare secties voor gebruik in prompts."""
    available = list_sections()
    lines = ["## Beschikbare secties\n"]
    for sec_type, variants in available.items():
        lines.append(f"### {sec_type}")
        lines.append(f"Varianten: {', '.join(variants)}\n")

    lines.append("""### Content-velden per sectie

**hero**: headline, subline (opt), cta_primary_text, cta_primary_link, cta_secondary_text (opt), cta_secondary_link (opt), image (opt)
**page_hero**: title, subtitle (opt)
**services**: headline (opt), intro (opt), items: [{icon, title, description, price (opt)}]
**about**: headline, body, image (opt), bullets (opt: lijst strings), cta_text (opt), cta_link
**stats**: headline (opt), items: [{value, label}]
**cta**: headline, body (opt), cta_text, cta_link, phone (opt), whatsapp (opt: alleen cijfers)
**faq**: headline, items: [{question, answer}]
**contact**: headline, phone (opt), email (opt), address (opt)
**team**: headline, members: [{name, role, photo (opt), bio (opt)}]
**gallery**: headline (opt), images: [paden naar foto's]
**testimonials**: headline, items: [{text, name, subtitle (opt)}]

### Veilige Lucide iconen voor services:
Phone, Mail, MapPin, Clock, Check, Star, Scissors, Sparkles, Heart, User, Users,
Home, Building, Wrench, Hammer, Paintbrush, Truck, Camera, Globe, Shield, Award,
Leaf, Coffee, Smile, Zap, Gift, Calendar, FileText, Briefcase, Package, Layers
""")
    return "\n".join(lines)
