import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_utils import get_claude_client

PROMPTS_DIR = Path("/workspace/prompts/impeccable")


def _load_inventory(brief_path: Path) -> dict:
    """Lees inventory.json naast briefing.md. Graceful fallback bij ontbreken."""
    inv_path = brief_path.parent / "inventory.json"
    if not inv_path.exists():
        return {}
    try:
        return json.loads(inv_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _inventory_facts_block(inv: dict) -> str:
    """Compact verbatim feiten-block voor de generator-prompt.

    Doel: de LLM ziet de echte prijzen, het echte telefoonnummer, signature-
    zinnen, enz. en mag GEEN AANNEMELIJK gebruiken voor dingen die hierin staan.
    """
    if not inv:
        return ""

    out: list[str] = ["## Bron-feiten (verbatim — citeer exact, geen AANNEMELIJK)"]

    contact = inv.get("contact") or {}
    parts = []
    if contact.get("phone_display"): parts.append(f"telefoon={contact['phone_display']}")
    if contact.get("phone"):         parts.append(f"phone-href={contact['phone']}")
    if contact.get("email"):         parts.append(f"email={contact['email']}")
    if contact.get("address"):       parts.append(f"adres={contact['address']}")
    if parts:
        out.append("**Contact:** " + " | ".join(parts))

    sc = inv.get("source_copy") or {}
    if sc.get("tagline"):
        out.append(f"**Tagline (uit og:description):** {sc['tagline']}")
    if sc.get("primary_cta"):
        out.append(f"**Primaire CTA-tekst (uit knoppen):** {sc['primary_cta']}")

    sigs = inv.get("signatures") or []
    if sigs:
        out.append("**Signature-zinnen (gebruik in hero/about):**")
        for s in sigs[:5]:
            q = s.get("quote", "").strip()
            if q:
                out.append(f"  > \"{q}\"")

    prices = inv.get("prices") or []
    if prices:
        out.append(f"**Prijzen ({len(prices)} stuks — gebruik EXACT, geen AANNEMELIJK):**")
        for p in prices[:30]:
            label = p.get("label", "")
            amount = p.get("amount", "")
            cat = p.get("category", "")
            cat_str = f" [{cat}]" if cat and cat.lower() != "overig" else ""
            out.append(f"  - {label}: {amount}{cat_str}")
        if len(prices) > 30:
            out.append(f"  - ...en nog {len(prices) - 30} prijzen")

    treatments = inv.get("treatments") or []
    if treatments and not prices:
        names = [t.get("name") for t in treatments[:10] if t.get("name")]
        out.append(f"**Behandelingen/diensten:** {', '.join(names)}")

    hours = inv.get("opening_hours") or []
    if hours:
        rows = [f"{h.get('day','')} {h.get('range','')}" for h in hours[:7]]
        out.append("**Openingstijden:** " + " | ".join(rows))

    reviews = inv.get("reviews") or []
    if reviews:
        out.append(f"**Echte reviews ({len(reviews)}):**")
        for r in reviews[:3]:
            q = r.get("quote", "").strip()
            if q:
                out.append(f"  > \"{q[:200]}\"")

    pages_content = inv.get("pages_content") or {}
    if pages_content:
        out.append("**Per-pagina bodycopy (gebruik verbatim op de juiste subpagina):**")
        for slug, pc in list(pages_content.items())[:6]:
            lead = (pc.get("lead") or "").strip()[:200]
            if lead:
                out.append(f"  /{slug or 'home'}: {lead}")

    voice = inv.get("voice_profile") or {}
    if voice.get("addressing") in ("je", "u"):
        out.append(f"**Aanspreekvorm (gemeten):** {voice['addressing']}-vorm, "
                   f"formality={voice.get('formality','gemengd')} — gebruik consistent")

    return "\n".join(out) + "\n"


def _check_aannemelijk(tsx: str, page_slug: str = "") -> int:
    """Tel hoe vaak 'AANNEMELIJK' nog in de gegenereerde TSX zit.
    Print een waarschuwing — niet fatal, maar wel een signaal."""
    n = tsx.count("AANNEMELIJK")
    if n > 0:
        suffix = f" voor {page_slug}" if page_slug else ""
        print(f"[WARN] {n}x 'AANNEMELIJK' nog aanwezig in gegenereerde TSX{suffix} — "
              f"feiten zijn waarschijnlijk niet uit inventory gehaald")
    return n


def _load_impeccable() -> str:
    """Laad Impeccable referentiebestanden als design-context.

    Bij Claude Max (USE_CLAUDE_MAX=true) wordt de context ingekort tot 400 tekens
    per bestand om CLI-timeouts te voorkomen. De API-versie krijgt de volledige 2000.
    """
    files = [
        "typography.md",
        "color-and-contrast.md",
        "spatial-design.md",
        "responsive-design.md",
        "interaction-design.md",
    ]
    use_max = os.getenv("USE_CLAUDE_MAX", "").lower() in ("true", "1", "yes")
    max_chars = 400 if use_max else 2000

    parts = []
    for f in files:
        path = PROMPTS_DIR / f
        if path.exists():
            parts.append(f"### {f}\n{path.read_text(encoding='utf-8')[:max_chars]}")
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
                 nav_pages: list[str] | None = None,
                 inventory: dict | None = None) -> str:
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
Gebruik UITSLUITEND deze afbeeldingen — verzin GEEN andere bestandsnamen.
Pad is relatief aan de projectroot (public/).
{image_list}
"""
    else:
        image_section = """
## Geen afbeeldingen beschikbaar
Er zijn GEEN lokale afbeeldingen beschikbaar. Gebruik daarom:
- CSS gradient achtergronden voor hero-secties (bijv. `bg-gradient-to-br from-[kleur] to-[kleur]`)
- Emoji's of Lucide-iconen als visuele accenten
- NOOIT `<img src="/bestandsnaam.jpg">` met verzonnen namen — die bestanden bestaan niet
"""

    default_nav = ["", "over-ons", "diensten", "contact"]
    routes = nav_pages if nav_pages else default_nav
    nav_list = "\n".join(f"  - /{r}" for r in routes) + "\n"
    # Expliciete waarschuwing: Claude gebruikt anders href="#" als fallback
    nav_href_warning = (
        "\n**BELANGRIJK:** Gebruik ALTIJD de echte route-URL als href in navigatielinks "
        "(bijv. `href=\"/over-ons/\"`). NOOIT `href=\"#\"` voor links die naar een pagina verwijzen — "
        "gebruik `href=\"#\"` uitsluitend voor intra-page anchor-links.\n"
    )

    inventory_block = _inventory_facts_block(inventory or {})
    inventory_section = f"\n{inventory_block}\n" if inventory_block else ""

    return f"""Je bent een senior React/Next.js developer en webdesigner.

Genereer Next.js 14 (App Router) TypeScript bestanden voor {company_name}.
Gebruik Tailwind CSS voor alle styling — geen aparte CSS tenzij expliciet gevraagd.
{impeccable_section}{image_section}{inventory_section}
## Navigatielinks — gebruik ALTIJD echte routes
Gebruik in navigatie en CTA-knoppen ALTIJD de echte route-URL:
- `href="/over-ons/"` — NOOIT `href="#"`
- `href="/contact/"` — NOOIT `href="#"`
- `href="/diensten/"` — NOOIT `href="#"`
{nav_href_warning}
## Maak elke site UNIEK — geen generieke templates
Studeer de briefing grondig. Kies bewust voor dit specifieke merk:
- **Eigen layout-ritme**: varieer sectie-groottes, witruimte, asymmetrie
- **Merkspecifieke typografie**: kies heading-stijl die past bij de sfeer (elegant serif, bold sans, etc.)
- **Kleur creatief inzetten**: gebruik de primary/secondary brand-kleuren voor gradients, borders, accents
- Elke pagina moet aanvoelen als gebouwd voor exact dit bedrijf

## DaisyUI — kies components die de merksfeer versterken
Gebruik DaisyUI-klassen die passen bij het specifieke merk:
- `btn btn-primary` / `btn-outline` / `btn-ghost` — kies variant op basis van merk-energie
- `card card-body shadow-lg` — diepte en structuur
- `hero hero-content` — indrukwekkende hero-secties
- `stat stat-title stat-value stat-desc` — USPs en highlights visueel sterk
- **`table table-zebra`** — gebruik ALTIJD voor prijslijsten en openingstijden (nooit custom divs)
- `badge` — diensten en labels
- `divider` — elegante sectie-scheiding
- `rating` — klant-sterren

## Openingstijden — verplicht weergeven als ze in de briefing staan
Openingstijden zijn kritisch voor lokale bedrijven. Als de briefing ze noemt:
- Toon ze op de homepage (compact, goed zichtbaar)
- Toon ze uitgebreid op de contact-pagina als `table table-zebra`
- Schrijf "Maandag: gesloten" expliciet, niet "op aanvraag"

## Beschikbare libraries — gebruik deze altijd boven custom implementaties
- **shadcn/ui**: `import {{ Accordion, AccordionItem, AccordionTrigger, AccordionContent }} from "@/components/ui/accordion"` — gebruik ALTIJD voor FAQ secties
- **shadcn/ui**: `import {{ Card, CardHeader, CardTitle, CardContent }} from "@/components/ui/card"` — voor kaarten
- **shadcn/ui**: `import {{ Button }} from "@/components/ui/button"` — voor alle knoppen/CTAs
- **shadcn/ui**: `import {{ Sheet, SheetContent, SheetTrigger }} from "@/components/ui/sheet"` — voor mobiel nav
- **BookingWidget**: `import BookingWidget from "@/components/BookingWidget"` — online afspraken (gebruik als de briefing een booking-url of Calendly/Treatwell-link noemt: `<BookingWidget url="..." title="Maak een afspraak" />`)
- **LeafletMap**: `import LeafletMap from "@/components/LeafletMap"` — kaart op contact-pagina (gebruik het adres uit de briefing: `<LeafletMap address="Straatnaam 1, Stad" />`)
- **shadcn/ui**: `import {{ Badge }} from "@/components/ui/badge"` — voor labels/tags
- **Lucide React**: `import {{ Phone, Mail, MapPin, Clock, ChevronDown, Menu, X, Star, Check }} from "lucide-react"` — voor iconen
- **Tailwind Typography**: `className="prose prose-lg max-w-none"` voor lange teksten (over-ons etc.)
- **Tailwind Forms**: formuliervelden worden automatisch gestijld, geen extra klassen nodig

## Harde beperkingen
- Gebruik UITSLUITEND Tailwind utility classes voor styling
- Geen inline style= attributen behalve voor dynamische waarden
- Alle interne links via Next.js `<Link href="...">` component
- Navigatieroutes zijn UITSLUITEND:
{nav_list}  Maak GEEN links naar routes die niet in deze lijst staan
- Geen markdown in je output, geen uitleg, alleen bestanden
- Voeg `"use client"` toe aan elk component dat hooks of event handlers gebruikt
- Afbeeldingen: gebruik gewone `<img>` tags (geen next/image, statische export)
- FAQ: gebruik ALTIJD shadcn Accordion, nooit custom div/button implementaties

## Typografie en interpunctie
- **GEEN em-dashes (—) in tekst**: gebruik komma's of punten in plaats daarvan
- Geen en-dashes (–) in tekst (wel ok in numerieke ranges zoals 09:00-17:00)
- Gebruik gewone aanhalingstekens, geen typografische ("smart quotes")

## Behoud herkenbare elementen van de originele site
De gebruiker heeft de huidige website. Een vernieuwde versie verkoopt beter als
herkenbare elementen terugkomen. Studeer de briefing op:
- **Specifieke diensten/producten** met dezelfde namen die op de originele site staan
- **USPs en kernboodschappen** die het bedrijf zelf uit (citaat-waardig in nieuwe vorm)
- **Eigen vocabulaire** (vakjargon, signature-termen, lokale uitdrukkingen)
- **Afbeeldingen die kenmerkend zijn** voor het bedrijf, gebruik die prominent
- **Klantreviews/testimonials** uit de briefing letterlijk overnemen (geen verzonnen versies)
Het doel: de gebruiker herkent zijn eigen bedrijf direct, maar in een professionelere jas.

## Technische eisen
- TypeScript met eenvoudige types (geen complexe generics)
- Import paths: `@/components/...`, `@/lib/...`
- Elk bestand begint met imports, daarna de component, daarna `export default`
- Tailwind responsive: mobile-first, gebruik `md:` en `lg:` prefixes

## Design principes (anti-patronen vermijden)
- NOOIT em-dashes (—) in kopij — gebruik een komma, dubbele punt of nieuwe zin
- NOOIT zelfverzonnen Tailwind-kleurnamen zoals `bg-cream`, `bg-forest`, `bg-ink` — gebruik DaisyUI semantisch (`bg-base-100`, `bg-primary`, `bg-neutral`) of exacte hex (`bg-[#2C4A3E]`)
- Header-achtergrond: ALTIJD `bg-base-100/95 backdrop-blur-sm` voor sticky header — nooit transparant of een onbekende kleur
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
                    page_desc: str = "", logo_path: str = "", **kwargs) -> str:

    if unit == "layout":
        logo_instruction = (
            f"\n- Logo beschikbaar op `/{logo_path}` — gebruik dit in de header als `<img src=\"/{logo_path}\" alt=\"logo\" />`"
            if logo_path else
            "\n- Geen logo-bestand beschikbaar — gebruik de bedrijfsnaam als tekst-logo in de header"
        )
        return f"""
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
- Logo links:{logo_instruction}
- Navigatie rechts
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
        no_backend = kwargs.get("no_backend", False)
        no_backend_block = """
## Geen backend-componenten (statische site)
Dit is een volledig statische site. De homepage mag ABSOLUUT GEEN van het volgende bevatten:
- Contactformulieren: `<form>`, `<input type="email">`, `<textarea>`, `<select>`
- Submit-knoppen voor formulieren
- BookingWidget of andere reserverings-widgets die een externe service vereisen
- API-calls, fetch, axios of server-side logica
- `<input>`, `<textarea>`, `<select>` elementen van welke aard dan ook

**Iframes zijn ALLEEN toegestaan als ze ook aantoonbaar op de originele website stonden**
(bijv. een virtuele tour van Matterport/Google Street View, een ingebedde YouTube video,
of een Google Maps embed). Gebruik dan de exacte src-URL uit de briefing of inventory.
Verzin GEEN iframe-URLs — als er geen iframe-URL in de brondata staat, gebruik je er geen.

Gebruik in plaats van een contactformulier:
- Een grote klikbare `<a href="tel:...">` telefoonknop
- Een `<a href="mailto:...">` e-maillink
- Een WhatsApp-link (`https://wa.me/31...`) als er een mobiel nummer in de briefing staat
""" if no_backend else ""

        return f"""
## Opdracht
Genereer ALLEEN dit bestand:

===FILE: src/app/page.tsx===
...inhoud...
===END_FILE===
{no_backend_block}
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
- EERSTE regel: `@import "tailwindcss";` (Tailwind v4 syntax — NOOIT @tailwind base/components/utilities)
- Plugins toevoegen: `@plugin "@tailwindcss/typography";` en `@plugin "@tailwindcss/forms";`
- Font theme (Tailwind v4 stijl):
  ```css
  @theme { --font-heading: var(--font-JOUW_HEADING_FONT); --font-sans: var(--font-JOUW_BODY_FONT); }
  ```
- Brand-kleuren als CSS custom properties (exacte hex-waarden uit briefing)
- shadcn CSS variabelen (HSL-waarden passend bij de brandkleuren):
  ```css
  :root {
    --background: ...; --foreground: ...;
    --primary: ...; --primary-foreground: ...;
    --card: ...; --card-foreground: ...;
    --muted: ...; --muted-foreground: ...;
    --border: ...; --radius: 0.375rem;
  }
  ```
- @layer base: scroll-behavior smooth, body font + kleur, ::selection
- Maximaal 70 regels
"""

    raise ValueError(f"Onbekende unit: {unit}")


def build_prompt(briefing: str, company_name: str, unit: str,
                 ref_tsx: str = "", page_slug: str = "",
                 page_title: str = "", page_desc: str = "",
                 images: list[str] | None = None,
                 nav_pages: list[str] | None = None,
                 logo_path: str = "",
                 inventory: dict | None = None,
                 no_backend: bool = False) -> tuple[str, str]:
    base      = common_rules(briefing, company_name, images=images, nav_pages=nav_pages,
                             inventory=inventory)
    unit_part = build_unit_part(unit, ref_tsx=ref_tsx, page_slug=page_slug,
                                page_title=page_title, page_desc=page_desc,
                                logo_path=logo_path, no_backend=no_backend)
    return base, unit_part


def extract_response_text(response) -> str:
    chunks = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            chunks.append(block.text)
    return "\n".join(chunks).strip()


def assemble_from_plan(plan_path: Path, page_slug: str, company_name: str) -> str:
    """Assembleert TSX uit een JSON-sectieplan via de component registry."""
    from component_registry import assemble_page
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    return assemble_page(plan, page_slug, company_name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--brief",      required=True)
    parser.add_argument("--company",    required=True)
    parser.add_argument("--unit",       required=True,
                        choices=["layout", "home", "page", "globals", "assembled"])
    parser.add_argument("--out",        required=True)
    parser.add_argument("--ref-tsx",    default="", help="Homepage TSX als referentie voor subpagina's")
    parser.add_argument("--page-slug",  default="")
    parser.add_argument("--page-title", default="")
    parser.add_argument("--page-desc",  default="")
    parser.add_argument("--image-manifest", default="")
    parser.add_argument("--nav-pages",  default="")
    parser.add_argument("--logo-path",  default="", help="Relatief pad naar logo in public/ (bijv. logo.png)")
    parser.add_argument("--no-backend", action="store_true",
                        help="Verbied backend-componenten in home-unit (formulieren, widgets, iframes)")
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    model   = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY ontbreekt")

    briefing = read_text(Path(args.brief))
    inventory = _load_inventory(Path(args.brief))
    if inventory:
        n_p = len(inventory.get("prices") or [])
        n_t = len(inventory.get("treatments") or [])
        n_s = len(inventory.get("signatures") or [])
        print(f"[INFO] inventory geladen: prijzen={n_p} treatments={n_t} signatures={n_s}")
    else:
        print(f"[INFO] geen inventory.json — feiten alleen uit briefing")

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

    # Voor assembled units: geen Claude-generatie, direct assemblen uit plan
    if args.unit == "assembled":
        plan_path = Path(args.brief).parent / f"{args.page_slug}-plan.json"
        if not plan_path.exists():
            raise FileNotFoundError(f"Plan niet gevonden: {plan_path}")
        tsx = assemble_from_plan(plan_path, args.page_slug, args.company)
        out_path = Path(args.out)
        write_text(out_path, f"===FILE: src/app/{args.page_slug}/page.tsx===\n{tsx}\n===END_FILE===")
        _check_aannemelijk(tsx, args.page_slug)
        print(f"[OK] Assembled TSX: {out_path}")
        return

    base, unit_part = build_prompt(
        briefing, args.company, args.unit,
        ref_tsx=ref_tsx, page_slug=args.page_slug,
        page_title=args.page_title, page_desc=args.page_desc,
        images=images, nav_pages=nav_pages,
        logo_path=args.logo_path,
        inventory=inventory,
        no_backend=args.no_backend,
    )

    # Op Claude Max OAuth duren grote responses via 'claude --print' soms
    # 10+ min en lopen tegen retry-timeouts aan. Verlaag dan home-tokens
    # zodat het iets compactere maar nog wel volledige homepage wordt.
    use_max = os.getenv("USE_CLAUDE_MAX", "").lower() in ("true", "1", "yes")
    UNIT_MAX_TOKENS = {
        "layout":  6000 if use_max else 8000,
        "home":    8000 if use_max else 12000,  # Max CLI traag bij grote responses
        "page":    6000 if use_max else 8000,
        "globals": 2000,
    }
    max_tokens = UNIT_MAX_TOKENS.get(args.unit, 6000 if use_max else 8000)

    client = get_claude_client(api_key)
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
            unit_label = args.page_slug or args.unit
            with client.messages.stream(
                model=model, max_tokens=max_tokens,
                messages=messages_payload,
                label=unit_label,
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

    _check_aannemelijk(raw_output, args.page_slug or args.unit)

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
