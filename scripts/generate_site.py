import argparse
import json
import os
import time
from pathlib import Path

from anthropic import Anthropic


def read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Bestand niet gevonden: {path}")
    return path.read_text(encoding="utf-8", errors="ignore")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def common_rules(briefing: str, company_name: str, images: list[str] | None = None,
                 nav_pages: list[str] | None = None) -> str:
    image_section = ""
    if images:
        image_list = "\n".join(f"- {img}" for img in images)
        image_section = f"""
## Beschikbare afbeeldingen van de originele site
Gebruik waar logisch en visueel passend deze echte afbeeldingen in de HTML.
Gebruik de EXACTE bestandsnamen zoals hieronder, als relatief pad vanuit het HTML-bestand.
Kies afbeeldingen die passen bij de context van de sectie (hero, team, diensten, gallerij, etc.).
Als geen relevante afbeelding beschikbaar is, gebruik dan: <img src="assets/images/placeholder.jpg" alt="beschrijving">

{image_list}

"""

    default_nav = ["index.html", "over-ons.html", "pakketten.html", "showcase.html", "contact.html"]
    pages_for_nav = nav_pages if nav_pages else default_nav
    nav_list = "\n".join(f"  - {p}" for p in pages_for_nav) + "\n"

    return f"""
Je bent een senior front-end developer en webdesigner.

Gebruik onderstaande briefing om statische websitebestanden te genereren voor {company_name}.
{image_section}
## Harde beperkingen — NOOIT overtreden
- Gebruik in HTML UITSLUITEND `assets/css/style.css` als stylesheet — geen components.css, geen andere CSS-bestanden
- Gebruik in HTML UITSLUITEND `assets/js/main.js` als script — geen andere JS-bestanden
- Alle interne `<a href>` links mogen UITSLUITEND verwijzen naar de onderstaande pagina's of naar ankers (#…):
{nav_list}  Maak GEEN enkele link naar HTML-bestanden die niet in deze lijst staan.
  Als je een dienst wilt noemen zonder linkbare pagina: schrijf gewoon tekst of link naar contact.html.
- Geen markdown in je output
- Geen uitleg, alleen bestanden

## Overige regels
- Bouw statische bestanden
- Gebruik alleen HTML, CSS en minimale vanilla JavaScript
- Geen React, Next.js, Vue, npm of build tooling
- Responsive en modern
- Geen lorem ipsum
- Geen verzonnen feiten buiten de briefing
- Schrijf teksten in het Nederlands
- Gebruik relatieve links
- Gebruik nette, production-minded code
- Gebruik dezelfde visuele richting op alle pagina's
- Gebruik GEEN em-dashes (—) in lopende tekst, gebruik een komma, punt of nieuwe zin als dat natuurlijker klinkt

## Formulieren
- Gebruik voor alle contactformulieren: `<form action="https://formspree.io/f/FORMSPREE_ID" method="POST">`
- Voeg een hidden `<input type="hidden" name="_subject" value="Nieuw bericht via website">` toe
- Voeg `<input type="text" name="_gotcha" style="display:none">` toe als spam-bescherming
- Voeg na het formulier een klein commentaar toe: `<!-- Vervang FORMSPREE_ID door uw eigen Formspree endpoint -->`

## Google Maps
- Als er een adres in de briefing staat: voeg op de contactpagina een Google Maps embed in
- Gebruik: `<iframe src="https://maps.google.com/maps?q=ADRES&output=embed" width="100%" height="300" style="border:0;border-radius:8px" allowfullscreen loading="lazy"></iframe>`
- Vervang ADRES door het URL-encoded adres uit de briefing

## Wat NOOIT mag worden verzonnen
Dit zijn secties die je WEGLAAT als de briefing er geen concrete data voor bevat:
- Klantreviews of testimonials: alleen opnemen als er letterlijke citaten of klantnamen in de briefing staan
- Statistieken en cijfers: "500+ klanten", "10 jaar ervaring", "98% tevreden" alleen als dit expliciet in de briefing staat
- Teamnamen, functies of persoonlijke verhalen van medewerkers: alleen als de briefing dit noemt
- Prijzen en tarieven: alleen concrete bedragen uit de briefing, geen verzonnen prijsranges
- Certificaten, diploma's of keurmerken: alleen als de briefing ze noemt

Als zo'n sectie ontbreekt in de briefing: laat de sectie volledig weg. Voeg geen placeholder-tekst toe zoals "Voeg hier uw reviews toe" — laat het gewoon achterwege. Een compacte site zonder die secties is beter dan een site met verzonnen inhoud.

## Briefing
{briefing}
"""


def extract_classes(html: str) -> str:
    """Extraheer unieke CSS-class names uit HTML voor gebruik in de styles-prompt."""
    import re
    classes = set()
    for match in re.finditer(r'class="([^"]+)"', html):
        for cls in match.group(1).split():
            classes.add(cls)
    return " ".join(sorted(classes))


def build_unit_part(unit: str, ref_html: str = "", ref_css: str = "",
                    page_file: str = "", page_title: str = "", page_desc: str = "") -> str:
    """Returnt het unit-specifieke deel van de prompt (wordt NIET gecached)."""
    if unit == "home_html":
        return """
## Opdracht
Genereer ALLEEN dit bestand:

===FILE: index.html===
...inhoud...
===END_FILE===

## Extra eisen
- Maak een complete homepage
- Inclusief header, hero, secties, intake CTA en footer
- Verwijs naar assets/css/style.css (ALLEEN dit stylesheet, niets anders)
- Verwijs naar assets/js/main.js (ALLEEN dit script, niets anders)
- Gebruik géén inline CSS
- Gebruik géén inline JS
- Gebruik duidelijke class names
"""

    if unit == "styles":
        ref_section = ""
        if ref_html:
            ref_section += f"""
## HTML structuurreferentie (index.html)
De homepage bepaalt het ontwerp. Stijl de class names exact zoals ze hieronder voorkomen.

```html
{ref_html[:15000]}
```
"""
        if ref_css:
            ref_section += f"""
## Originele CSS van de bestaande site (visuele referentie)
Neem de exacte kleuren, fonts, border-radius, spacing en shadows over.
Gebruik NIET dezelfde class names.

```css
{ref_css[:8000]}
```
"""

        class_list_section = ""
        if page_desc:
            class_list_section = f"""
## Structurele class names (uit homepage + gedeelde componenten)
Dit zijn de class names van de gedeelde componenten (header, nav, footer, hero, cards, buttons).
Zorg dat deze class names exact gestijld zijn. Subpagina's hergebruiken dezelfde structuur.
Voor class names die op subpagina's voorkomen maar niet in de lijst staan: laat stijlen erven
van de dichtstbijzijnde overeenkomende component (bijv. `.dienst-hero` erft van `.hero`).

{page_desc}
"""

        return ref_section + class_list_section + """
## Opdracht
Genereer ALLEEN dit bestand:

===FILE: assets/css/style.css===
...inhoud...
===END_FILE===

## Extra eisen
- Stijl alle class names uit de lijst hierboven expliciet
- Schrijf daarnaast uitgebreide basis HTML-elementstijlen (h1-h6, p, a, ul, section, article,
  main, form, input, textarea, button) zodat subpagina-elementen zonder eigen class-regel
  er netjes uitzien via overerving
- Neem kleuren, fonts en stijlkeuzes over uit de originele CSS-referentie
- Begin met @import voor Google Fonts (Montserrat + Open Sans of Roboto)
- Gebruik CSS variables in :root voor kleuren, spacing en fonts
- Mobile first, geen comments
- Dek af: header, mobile nav, hero, cards, grids, buttons, forms, footer, page hero

## Vaste afspraken voor open/active states — GEBRUIK ALTIJD EXACT DEZE KLASSEN
- Mobiel nav open: `nav.is-open` (of het exacte nav-element met `.is-open`)
- Dropdown open: `.nav-dropdown.is-open` of `.site-nav__dropdown.is-open`
- Sticky header: `header.is-scrolled`
- FAQ item open: `.faq__item.is-open` of `.faq-item.is-open`
Gebruik NOOIT `--open`, `--active`, `show` of andere varianten voor deze states.
"""

    if unit == "scripts":
        ref_section = ""
        if ref_html:
            ref_section = f"""
## HTML referentie (index.html)
Gebruik de EXACTE class names en IDs uit deze HTML voor event listeners en selectors.
Raad geen class names — lees ze af uit de HTML hieronder.

```html
{ref_html[:8000]}
```
"""
        return ref_section + """
## Opdracht
Genereer ALLEEN dit bestand:

===FILE: assets/js/main.js===
...inhoud...
===END_FILE===

## Extra eisen
- Gebruik alleen class names en IDs die voorkomen in de HTML referentie hierboven
- Ondersteun: mobiele navigatie toggle, sticky header, FAQ accordion, smooth scroll
- Geen libraries, defensive code
- Dropdown hover op desktop: gebruik altijd een sluitvertraging van 200ms via setTimeout zodat de gebruiker de muis naar het submenu kan bewegen zonder dat het wegklapt. Annuleer de timer bij mouseenter met clearTimeout.

## Vaste afspraken voor open/active states — GEBRUIK ALTIJD EXACT DEZE KLASSEN
- Mobiel nav open: voeg `is-open` toe aan het `<nav>` element (niet aan een wrapper)
- Dropdown open: voeg `is-open` toe aan het `<ul>` dropdown-element direct (niet aan de parent `<li>`)
- Sticky header: voeg `is-scrolled` toe aan het `<header>` element
- FAQ item open: voeg `is-open` toe aan het `.faq__item` of `.faq-item` element
Deze klassen zijn ook zo gedefineerd in de bijbehorende CSS — gebruik NIETS anders (geen `--open`, geen `--active`, geen `show`).
"""

    if unit == "over_ons":
        return """
## Opdracht
Genereer ALLEEN dit bestand:

===FILE: over-ons.html===
...inhoud...
===END_FILE===

## Extra eisen
- Gebruik dezelfde header/footer structuur als de homepage
- Verwijs naar assets/css/style.css en assets/js/main.js
- Gaat over bedrijf, werkwijze en vertrouwen
"""

    if unit == "pakketten":
        return """
## Opdracht
Genereer ALLEEN dit bestand:

===FILE: pakketten.html===
...inhoud...
===END_FILE===

## Extra eisen
- Gebruik dezelfde header/footer structuur als de homepage
- Verwijs naar assets/css/style.css en assets/js/main.js
- Pakketvergelijking met duidelijke verschillen en CTA
- Houd de HTML compact: maximaal 350 regels
"""

    if unit == "showcase":
        return """
## Opdracht
Genereer ALLEEN dit bestand:

===FILE: showcase.html===
...inhoud...
===END_FILE===

## Extra eisen
- Gebruik dezelfde header/footer structuur als de homepage
- Verwijs naar assets/css/style.css en assets/js/main.js
- Portfolio/cases tonen met placeholders waar nodig
- Maximaal 4 showcase-items, geen herhalingen
"""

    if unit == "contact":
        return """
## Opdracht
Genereer ALLEEN dit bestand:

===FILE: contact.html===
...inhoud...
===END_FILE===

## Extra eisen
- Gebruik dezelfde header/footer structuur als de homepage
- Verwijs naar assets/css/style.css en assets/js/main.js
- Duidelijke contactstructuur en formulier
"""

    if unit == "page":
        if not page_file:
            raise ValueError("--page-file is verplicht voor unit 'page'")
        title = page_title or page_file.replace(".html", "").replace("-", " ").title()
        desc  = f"\n- Doel en inhoud: {page_desc}" if page_desc else ""

        ref_section = ""
        if ref_html:
            ref_section = f"""
## Homepage referentie
Dit is de volledige homepage. Gebruik dit als visuele en structurele leidraad.

**Verplichte regels:**
1. Kopieer de `<header>` en `<footer>` EXACT — inclusief alle class names, structuur en attributen.
2. Voor je `<main>` inhoud: gebruik DEZELFDE class names als de homepage voor vergelijkbare
   componenten. Hero-sectie → gebruik `.hero`. Kaartjes → gebruik `.card`. Grid → gebruik `.grid`.
   Buttons → gebruik `.btn`, `.btn-primary`. Zo werkt de gedeelde CSS automatisch op elke pagina.
3. Verzin GEEN nieuwe class names voor componenten die al in de homepage bestaan.

```html
{ref_html[:15000]}
```
"""

        return ref_section + f"""
## Opdracht
Genereer ALLEEN dit bestand:

===FILE: {page_file}===
...inhoud...
===END_FILE===

## Extra eisen
- Paginanaam: {title}{desc}
- Verwijs naar assets/css/style.css en assets/js/main.js
- Houd de HTML compact maar volledig: maximaal 450 regels
"""

    raise ValueError("unit moet één van deze zijn: home_html, styles, scripts, over_ons, pakketten, showcase, contact, page")


def build_prompt(briefing: str, company_name: str, unit: str, ref_html: str = "", ref_css: str = "",
                 page_file: str = "", page_title: str = "", page_desc: str = "",
                 images: list[str] | None = None, nav_pages: list[str] | None = None) -> tuple[str, str]:
    """Returnt (cacheable_base, unit_part) voor gebruik met prompt caching."""
    base      = common_rules(briefing, company_name, images=images, nav_pages=nav_pages)
    unit_part = build_unit_part(unit, ref_html=ref_html, ref_css=ref_css,
                                page_file=page_file, page_title=page_title, page_desc=page_desc)
    return base, unit_part


def extract_response_text(response) -> str:
    chunks = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            chunks.append(block.text)
    return "\n".join(chunks).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--brief", required=True, help="Pad naar briefing.md")
    parser.add_argument("--company", required=True, help="Bedrijfsnaam")
    parser.add_argument("--unit", required=True,
                        choices=["home_html", "styles", "scripts", "over_ons", "pakketten", "showcase", "contact", "page"])
    parser.add_argument("--out",        required=True, help="Pad naar raw output bestand")
    parser.add_argument("--ref-html",   help="Pad naar gegenereerde HTML voor class name context (alleen voor styles)")
    parser.add_argument("--ref-css",    help="Pad naar originele CSS voor visuele referentie (alleen voor styles)")
    parser.add_argument("--page-file",       help="Bestandsnaam van de pagina (alleen voor unit 'page')")
    parser.add_argument("--page-title",      help="Paginatitel (alleen voor unit 'page')")
    parser.add_argument("--page-desc",       help="Beschrijving/doel van de pagina (alleen voor unit 'page')")
    parser.add_argument("--image-manifest",  help="Pad naar images.json met lijst van beschikbare afbeeldingen")
    parser.add_argument("--nav-pages",       help="JSON-array van HTML-bestandsnamen voor de navigatie, bijv. '[\"index.html\",\"contact.html\"]'")
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY ontbreekt in environment")

    briefing_path = Path(args.brief)
    out_path = Path(args.out)

    briefing = read_text(briefing_path)

    ref_html = ""
    if args.ref_html:
        ref_html = read_text(Path(args.ref_html))

    ref_css = ""
    if args.ref_css:
        ref_css = read_text(Path(args.ref_css))

    images = None
    if args.image_manifest:
        try:
            images = json.loads(Path(args.image_manifest).read_text(encoding="utf-8"))
            print(f"[INFO] Afbeeldingen meegegeven: {len(images)}")
        except Exception:
            pass

    nav_pages = None
    if args.nav_pages:
        try:
            nav_pages = json.loads(args.nav_pages)
            print(f"[INFO] Nav-pagina's: {nav_pages}")
        except Exception:
            pass

    base, unit_part = build_prompt(briefing, args.company, args.unit,
                                   ref_html=ref_html, ref_css=ref_css,
                                   page_file=args.page_file  or "",
                                   page_title=args.page_title or "",
                                   page_desc=args.page_desc  or "",
                                   images=images, nav_pages=nav_pages)

    UNIT_MAX_TOKENS = {
        "home_html": 14000,
        "styles":    40000,
        "scripts":    5000,
        "over_ons":   8000,
        "pakketten":  8000,
        "showcase":   8000,
        "contact":    6000,
        "page":      12000,
    }
    max_tokens = UNIT_MAX_TOKENS.get(args.unit, 8000)

    client = Anthropic(api_key=api_key)

    print(f"[INFO] Model: {model} | max_tokens: {max_tokens}")

    messages_payload = [{
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": base,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": unit_part,
            },
        ],
    }]

    # Streaming verplicht voor grote outputs. Retry bij rate limit (8k tokens/min org-limiet):
    # na een parallelle batch is het budget op — wacht 60s zodat het venster reset.
    print(f"[INFO] Genereer unit '{args.unit}' voor: {args.company} (streaming)")
    MAX_RATE_RETRIES = 5
    raw_output = ""
    response = None
    for attempt in range(1, MAX_RATE_RETRIES + 1):
        try:
            chunks: list[str] = []
            with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
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
                print(f"[WARN] Rate limit (poging {attempt}/{MAX_RATE_RETRIES}) — wacht {wait}s")
                time.sleep(wait)
            else:
                raise

    if not raw_output:
        raise RuntimeError("Lege output teruggekregen")

    write_text(out_path, raw_output)

    usage = getattr(response, "usage", None)
    cache_read  = getattr(usage, "cache_read_input_tokens",  0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

    meta = {
        "model": model,
        "briefing_file": str(briefing_path),
        "output_file": str(out_path),
        "unit": args.unit,
        "stop_reason": getattr(response, "stop_reason", None),
        "usage": {
            "input_tokens":               getattr(usage, "input_tokens",  None) if usage else None,
            "output_tokens":              getattr(usage, "output_tokens", None) if usage else None,
            "cache_read_input_tokens":    cache_read,
            "cache_creation_input_tokens": cache_write,
        },
    }

    meta_path = out_path.with_suffix(".meta.json")
    write_text(meta_path, json.dumps(meta, indent=2, ensure_ascii=False))

    print(f"[OK] Raw output opgeslagen in: {out_path}")
    print(f"[OK] Metadata opgeslagen in: {meta_path}")
    print(f"[INFO] stop_reason: {meta['stop_reason']}")
    if cache_read:
        print(f"[INFO] Cache hit: {cache_read} tokens gelezen uit cache")
    if cache_write:
        print(f"[INFO] Cache write: {cache_write} tokens gecached")


if __name__ == "__main__":
    main()
