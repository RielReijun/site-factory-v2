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

# Componenten die automatisch geïmporteerd worden als ze in de TSX voorkomen
COMPONENT_IMPORTS = {
    "GalleryCarousel": 'import GalleryCarousel from "@/components/GalleryCarousel";',
    "LeafletMap":      'import LeafletMap from "@/components/LeafletMap";',
}


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


def render_section(section_type: str, variant: str, content: dict) -> tuple[str | None, set[str]]:
    """
    Rendert één sectie.
    Geeft (tsx_string, icons) terug.
    Geeft (None, set()) als template niet bestaat — signaal voor auto-generate.
    """
    template = load_template(section_type, variant)
    if template is None:
        return None, set()

    data  = _prepare_content(content, section_type)
    tsx   = render(template, data)
    icons = collect_icons(tsx)
    return tsx.strip(), icons


def generate_template(section_type: str, variant: str, content: dict) -> str | None:
    """
    Vraag Claude om een nieuw template te genereren en sla het op.
    Geeft de template-string terug (niet de gerenderde TSX).

    Wordt alleen aangeroepen als load_template() None geeft.
    De gegenereerde template is beschikbaar voor alle toekomstige sites.
    """
    import os
    from pathlib import Path as _Path
    from template_engine import COMPONENTS_DIR as _COMPS

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    # Bouw content-beschrijving voor de prompt
    field_desc = []
    for k, v in content.items():
        if isinstance(v, list) and v:
            sample = v[0]
            if isinstance(sample, dict):
                field_desc.append(f"  {k}: lijst van {{ {', '.join(sample.keys())} }}")
            else:
                field_desc.append(f"  {k}: lijst van strings")
        elif isinstance(v, str):
            field_desc.append(f"  {k}: tekst")
    fields_str = "\n".join(field_desc) or "  (zie content hieronder)"

    # Voorbeeld-template voor stijl-referentie
    example = load_template("services", "cards-3") or ""

    prompt = f"""Genereer een TSX-sectie-template voor de site factory.

Type: {section_type}
Variant: {variant}

Content-velden:
{fields_str}

Regels:
- Gebruik [[ field ]] voor tekstwaarden
- Gebruik [[ ?field ]]...[[ / ]] voor optionele blokken (alleen renderen als veld aanwezig)
- Gebruik [[ *listfield ]]...[[ / ]] voor lijsten, [[ .subfield ]] voor item-eigenschappen
- Gebruik [[ ~iconfield ]] voor Lucide-iconen (valideert automatisch)
- Alleen Tailwind CSS classes, geen custom CSS
- Responsive: gebruik md: en lg: prefixes
- Geen import-statements — die worden automatisch toegevoegd
- Geef ALLEEN de sectie-JSX terug, geen export default, geen wrapper-component

Stijlreferentie (bestaande template):
{example[:800]}

Genereer nu de template voor {section_type}/{variant}:"""

    try:
        from anthropic import Anthropic
        from pipeline_utils import get_model, with_retry

        client = Anthropic(api_key=api_key)
        model  = get_model()

        response = with_retry(
            lambda: client.messages.create(
                model=model, max_tokens=1500,
                messages=[{"role": "user", "content": prompt}]
            ),
            label=f"generate_template:{section_type}/{variant}",
        )
        raw = "".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()

        # Verwijder code fences en markdown-uitleg na de TSX
        import re as _re
        raw = _re.sub(r'^```\w*\s*', '', raw, flags=_re.MULTILINE)
        raw = _re.sub(r'\s*```\s*$', '', raw, flags=_re.MULTILINE)
        # Stop bij markdown-koppen/tabellen die na de TSX verschijnen
        raw = _re.split(r'\n#{1,3} |\n\|[-|]+\|', raw)[0]
        template = raw.strip()

        if not template:
            return None

        # Sla op in prompts/components/
        dest = _COMPS / section_type / f"{variant}.tsx"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(template, encoding="utf-8")
        print(f"[OK]  Nieuw template opgeslagen: {section_type}/{variant}.tsx")
        return template

    except Exception as e:
        print(f"[WARN] Template genereren mislukt voor {section_type}/{variant}: {e}")
        return None


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

        if tsx is None:
            # Template bestaat niet — genereer en sla op
            print(f"[INFO] Template niet gevonden: {sec_type}/{variant} — Claude genereert het")
            new_template = generate_template(sec_type, variant, content)
            if new_template:
                tsx, icons = render_section(sec_type, variant, content)
            else:
                print(f"[WARN] Template genereren mislukt voor {sec_type}/{variant} — sectie overgeslagen")
                tsx = ""

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
    # Gedeelde componenten (Carousel, Map)
    full_tsx = "\n".join(body_parts)
    for comp, imp in COMPONENT_IMPORTS.items():
        if comp in full_tsx:
            imports.append(imp)

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
