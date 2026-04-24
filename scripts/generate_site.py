import argparse
import json
import os
import time
from pathlib import Path

from anthropic import Anthropic

PROMPTS_DIR = Path("/workspace/prompts/impeccable")


def _load_impeccable() -> str:
    """Laad relevante Impeccable referentiebestanden als design-context."""
    files = [
        "typography.md",
        "color-and-contrast.md",
        "spatial-design.md",
        "responsive-design.md",
        "interaction-design.md",
    ]
    parts = []
    for f in files:
        path = PROMPTS_DIR / f
        if path.exists():
            parts.append(f"### {f}\n{path.read_text(encoding='utf-8')[:2000]}")
    if not parts:
        return ""
    return "\n\n".join(parts)


def read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Bestand niet gevonden: {path}")
    return path.read_text(encoding="utf-8", errors="ignore")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def common_rules(briefing: str, company_name: str,
                 images: list[str] | None = None,
                 nav_pages: list[str] | None = None) -> str:
    impeccable = _load_impeccable()
    impeccable_section = f"""
## Design referentie (Impeccable)
{impeccable}
""" if impeccable else ""

    image_section = ""
    if images:
        image_list = "\n".join(f"- {img}" for img in images)
        image_section = f"""
## Beschikbare afbeeldingen
Gebruik deze echte afbeeldingen waar passend. Pad is relatief aan de projectroot (public/).
{image_list}
"""

    default_nav = ["", "over-ons", "diensten", "contact"]
    routes = nav_pages if nav_pages else default_nav
    nav_list = "\n".join(f"  - /{r}" for r in routes) + "\n"

    return f"""Je bent een senior React/Next.js developer en webdesigner.

Genereer Next.js 14 (App Router) TypeScript bestanden voor {company_name}.
Gebruik Tailwind CSS voor alle styling — geen aparte CSS tenzij expliciet gevraagd.
{impeccable_section}{image_section}
## Harde beperkingen
- Gebruik UITSLUITEND Tailwind utility classes voor styling
- Geen inline style= attributen behalve voor dynamische waarden
- Alle interne links via Next.js `<Link href="...">` component
- Navigatieroutes zijn UITSLUITEND:
{nav_list}  Maak GEEN links naar routes die niet in deze lijst staan
- Geen markdown in je output, geen uitleg — alleen bestanden
- Voeg `"use client"` toe aan elk component dat hooks of event handlers gebruikt
- Afbeeldingen: gebruik gewone `<img>` tags (geen next/image — statische export)

## Technische eisen
- TypeScript met eenvoudige types (geen complexe generics)
- Import paths: `@/components/...`, `@/lib/...`
- Elk bestand begint met imports, daarna de component, daarna `export default`
- Tailwind responsive: mobile-first, gebruik `md:` en `lg:` prefixes

## Design principes (anti-patronen vermijden)
- GEEN Inter als enige font — combineer met een serif of display font
- GEEN grijze tekst op gekleurde achtergrond
- GEEN cards genest in cards
- GEEN pure zwart/grijs — altijd getinte neutrals
- Spacing: consistent 4px grid via Tailwind (p-4, p-8, gap-6, etc.)
- Elke sectie heeft duidelijke visuele hiërarchie

## Conversie-eisen
- Telefoonnummer als klikbare `<a href="tel:...">` in de header
- CTA-sectie met primaire knop op ELKE pagina voor de footer
- WhatsApp-link als mobiel nummer in briefing staat: `https://wa.me/31XXXXXXXXX`

## Footer — ALTIJD exact deze structuur
```tsx
<footer className="bg-[kleur] text-white">
  <div className="container mx-auto px-4 py-12">
    <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
      <div>{{/* brand + adres */}}</div>
      <div>{{/* navigatie */}}</div>
      <div>{{/* contact */}}</div>
    </div>
    <div className="border-t border-white/20 mt-8 pt-6 text-sm text-white/60">
      <p>&copy; {{new Date().getFullYear()}} {company_name}</p>
    </div>
  </div>
</footer>
```

## Briefing
{briefing}
"""


def build_unit_part(unit: str, ref_tsx: str = "",
                    page_slug: str = "", page_title: str = "",
                    page_desc: str = "") -> str:

    if unit == "layout":
        return """
## Opdracht
Genereer deze bestanden:

===FILE: src/app/layout.tsx===
...inhoud...
===END_FILE===

===FILE: src/components/Header.tsx===
...inhoud...
===END_FILE===

===FILE: src/components/Footer.tsx===
...inhoud...
===END_FILE===

===FILE: src/app/globals.css===
...inhoud...
===END_FILE===

## Eisen layout.tsx
- Root layout met `<html lang="nl">`, metadata (title template, description)
- Importeer Header en Footer components
- Importeer globals.css
- Importeer Google Fonts via `next/font/google` (kies 2 complementaire fonts)

## Eisen Header.tsx
- `"use client"` directive (heeft state voor mobile menu)
- Logo/bedrijfsnaam links, navigatie rechts
- Mobiel hamburger menu met slide-in nav
- Telefoonnummer als klikbare `tel:` link (indien in briefing)
- Sticky met subtiele shadow na scrollen (`useEffect` + `scroll` event)
- Actieve route highlighten via `usePathname()`

## Eisen Footer.tsx
- Gebruik exact de footer-structuur uit de gemeenschappelijke regels
- Geen "use client" nodig

## Eisen globals.css
- `@tailwind base; @tailwind components; @tailwind utilities;`
- CSS custom properties voor brand-kleuren (uit briefing)
- Maximaal 50 regels
"""

    if unit == "home":
        return f"""
## Opdracht
Genereer ALLEEN dit bestand:

===FILE: src/app/page.tsx===
...inhoud...
===END_FILE===

## Eisen
- Complete homepage: hero, diensten/features, over-sectie, CTA-sectie, eventueel FAQ
- Importeer Header en Footer NIET — die zitten in layout.tsx
- Gebruik Tailwind voor alle styling
- Hero: grote heading, subtitel, 2 CTA-knoppen (primair + secundair)
- Elke sectie heeft `<section>` met id voor anchor-links
- CTA-sectie voor de footer: pakkende tekst + primaire knop naar /contact
"""

    if unit == "page":
        if not page_slug:
            raise ValueError("--page-slug is verplicht voor unit 'page'")
        title = page_title or page_slug.replace("-", " ").title()
        desc  = f"\n- Doel: {page_desc}" if page_desc else ""

        ref_section = ""
        if ref_tsx:
            ref_section = f"""
## Homepage referentie (page.tsx)
Gebruik dezelfde Tailwind klassen en component-patronen voor consistentie.
```tsx
{ref_tsx[:4000]}
```
"""
        return ref_section + f"""
## Opdracht
Genereer ALLEEN dit bestand:

===FILE: src/app/{page_slug}/page.tsx===
...inhoud...
===END_FILE===

## Eisen
- Pagina: {title}{desc}
- Importeer Header/Footer NIET (zitten in root layout)
- Page hero: `<section>` met achtergrondkleur, grote h1, korte beschrijving
- Gebruik dezelfde Tailwind patronen als de homepage referentie
- CTA-sectie voor het einde van de pagina
- Maximaal 300 regels
"""

    if unit == "globals":
        return f"""
## Opdracht
Genereer ALLEEN dit bestand:

===FILE: src/app/globals.css===
...inhoud...
===END_FILE===

## Eisen
- Tailwind directives: @tailwind base/components/utilities
- CSS custom properties voor brand-kleuren (uit briefing)
- Subtiele typografie-verbeteringen (font smoothing, line-height)
- Maximaal 60 regels
"""

    raise ValueError(f"Onbekende unit: {unit}")


def build_prompt(briefing: str, company_name: str, unit: str,
                 ref_tsx: str = "", page_slug: str = "",
                 page_title: str = "", page_desc: str = "",
                 images: list[str] | None = None,
                 nav_pages: list[str] | None = None) -> tuple[str, str]:
    base      = common_rules(briefing, company_name, images=images, nav_pages=nav_pages)
    unit_part = build_unit_part(unit, ref_tsx=ref_tsx, page_slug=page_slug,
                                page_title=page_title, page_desc=page_desc)
    return base, unit_part


def extract_response_text(response) -> str:
    chunks = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            chunks.append(block.text)
    return "\n".join(chunks).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--brief",      required=True)
    parser.add_argument("--company",    required=True)
    parser.add_argument("--unit",       required=True,
                        choices=["layout", "home", "page", "globals"])
    parser.add_argument("--out",        required=True)
    parser.add_argument("--ref-tsx",    default="", help="Homepage TSX als referentie voor subpagina's")
    parser.add_argument("--page-slug",  default="")
    parser.add_argument("--page-title", default="")
    parser.add_argument("--page-desc",  default="")
    parser.add_argument("--image-manifest", default="")
    parser.add_argument("--nav-pages",  default="")
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    model   = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY ontbreekt")

    briefing = read_text(Path(args.brief))

    ref_tsx = ""
    if args.ref_tsx and Path(args.ref_tsx).exists():
        ref_tsx = Path(args.ref_tsx).read_text(encoding="utf-8", errors="ignore")

    images = None
    if args.image_manifest:
        try:
            images = json.loads(Path(args.image_manifest).read_text(encoding="utf-8"))
        except Exception:
            pass

    nav_pages = None
    if args.nav_pages:
        try:
            nav_pages = json.loads(args.nav_pages)
        except Exception:
            pass

    base, unit_part = build_prompt(
        briefing, args.company, args.unit,
        ref_tsx=ref_tsx, page_slug=args.page_slug,
        page_title=args.page_title, page_desc=args.page_desc,
        images=images, nav_pages=nav_pages,
    )

    UNIT_MAX_TOKENS = {
        "layout":  8000,
        "home":   12000,
        "page":    8000,
        "globals": 2000,
    }
    max_tokens = UNIT_MAX_TOKENS.get(args.unit, 8000)

    client = Anthropic(api_key=api_key)
    print(f"[INFO] Model: {model} | unit: {args.unit} | max_tokens: {max_tokens}")

    messages_payload = [{
        "role": "user",
        "content": [
            {"type": "text", "text": base, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": unit_part},
        ],
    }]

    MAX_RATE_RETRIES = 5
    raw_output = ""
    response   = None
    for attempt in range(1, MAX_RATE_RETRIES + 1):
        try:
            chunks: list[str] = []
            with client.messages.stream(
                model=model, max_tokens=max_tokens,
                messages=messages_payload,
            ) as stream:
                for text in stream.text_stream:
                    chunks.append(text)
                response = stream.get_final_message()
            raw_output = "".join(chunks).strip()
            break
        except Exception as e:
            if "rate_limit" in str(e).lower() and attempt < MAX_RATE_RETRIES:
                wait = 60 * attempt
                print(f"[WARN] Rate limit (poging {attempt}) — wacht {wait}s")
                time.sleep(wait)
            else:
                raise

    if not raw_output:
        raise RuntimeError("Lege output teruggekregen")

    out_path = Path(args.out)
    write_text(out_path, raw_output)

    usage       = getattr(response, "usage", None)
    cache_read  = getattr(usage, "cache_read_input_tokens",  0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    meta = {
        "model": model, "unit": args.unit,
        "stop_reason": getattr(response, "stop_reason", None),
        "usage": {
            "input_tokens":                getattr(usage, "input_tokens",  None) if usage else None,
            "output_tokens":               getattr(usage, "output_tokens", None) if usage else None,
            "cache_read_input_tokens":     cache_read,
            "cache_creation_input_tokens": cache_write,
        },
    }
    write_text(out_path.with_suffix(".meta.json"),
               json.dumps(meta, indent=2, ensure_ascii=False))

    print(f"[OK] Raw output: {out_path}")
    print(f"[INFO] stop_reason: {meta['stop_reason']}")
    if cache_read:  print(f"[INFO] Cache hit: {cache_read} tokens")
    if cache_write: print(f"[INFO] Cache write: {cache_write} tokens")


if __name__ == "__main__":
    main()
