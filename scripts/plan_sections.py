"""
plan_sections.py — Claude genereert een JSON-pagina-plan vanuit de briefing.

Per pagina wordt één JSON-object gegenereerd met secties + content.
Dit vervangt de vrije TSX-generatie in generate_site.py.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

from pipeline_utils import get_claude_client
from component_registry import get_schema_description
from pipeline_utils import get_model, with_retry


DAISYUI_THEMES = [
    "light", "dark", "cupcake", "bumblebee", "emerald", "corporate",
    "retro", "garden", "forest", "aqua", "lofi", "pastel",
    "luxury", "dracula", "autumn", "business", "coffee", "winter",
    "dim", "nord", "sunset", "lemonade",
]


def pick_theme(client, model: str, briefing: str) -> str:
    """Laat Claude het beste DaisyUI-theme kiezen op basis van de briefing."""
    prompt = f"""Kies het beste DaisyUI-theme voor deze website op basis van de briefing.

Beschikbare themes en hun sfeer:
- light: neutraal/clean
- dark: donker/professioneel
- cupcake: zacht/roze/vrouwelijk
- bumblebee: geel/energiek
- emerald: groen/fris
- corporate: blauw/zakelijk
- retro: warm/vintage
- garden: aards/natuur
- forest: donkergroen/natuur
- aqua: blauw/water/zwembad
- lofi: minimaal/rustig
- pastel: zachte kleuren/lief
- luxury: goud/donker/exclusief
- dracula: paars/donker/modern
- autumn: warm/herfstkleuren
- business: grijs/zakelijk
- coffee: bruin/warm/gezellig
- winter: koel/blauw/fris
- dim: gedempte donkere tinten
- nord: nordic/minimaal/koel
- sunset: oranje/roze/warm
- lemonade: geel/fris/zomers

Briefing:
{briefing[:3000]}

Geef ALLEEN de theme-naam terug, geen uitleg."""

    try:
        response = with_retry(
            lambda: client.messages.create(
                model=model, max_tokens=20,
                messages=[{"role": "user", "content": prompt}]
            ),
            label="pick_theme",
        )
        theme = "".join(b.text for b in response.content
                        if getattr(b, "type", None) == "text").strip().lower()
        if theme in DAISYUI_THEMES:
            return theme
    except Exception:
        pass
    return "light"


def plan_page(client, model: str, briefing: str, page_slug: str,
              page_title: str, page_desc: str, images: list[str],
              nav_pages: list[str], is_homepage: bool = False,
              theme: str = "light") -> dict:
    """Vraag Claude om een JSON-sectieplan voor één pagina."""

    schema = get_schema_description()
    images_str = "\n".join(f"- {img}" for img in images[:20]) if images else "Geen afbeeldingen beschikbaar."
    nav_str    = ", ".join(nav_pages) if nav_pages else ""

    page_context = "Dit is de **homepage** — gebruik een uitgebreid hero-blok en toon de breedte van het aanbod." if is_homepage \
                   else f"Dit is de pagina **{page_title}** ({page_desc}). Gebruik een page_hero als eerste sectie."

    prompt = f"""Je bent een webstrateeg die een pagina-opzet maakt voor een statische Next.js website.

{page_context}

Geef een JSON-object met de secties voor deze pagina.
Gebruik ALLEEN de beschikbare sectietypes hieronder.
Vul ECHTE inhoud in op basis van de briefing — verzin NIETS wat niet in de briefing staat.
Markeer onzekerheden met AANNEMELIJK.

## Beschikbare secties
{schema}

## Beschikbare afbeeldingen (gebruik exacte paden)
{images_str}

## Navigatielinks voor CTA's
{nav_str}

## Briefing
{briefing[:8000]}

---

Geef ALLEEN dit JSON-object terug, geen uitleg:
{{
  "sections": [
    {{"type": "...", "variant": "...", "content": {{...}}}},
    ...
  ]
}}

Regels:
- homepage: 4-6 secties inclusief hero en cta (laatste voor footer)
- subpagina: 3-5 secties inclusief page_hero en cta (laatste)
- GEEN secties toevoegen als de briefing de content niet bevat (bijv. geen testimonials zonder echte reviews)
- Afbeeldingen: alleen paden gebruiken die hierboven staan
- Telefoon/WhatsApp: alleen als die in de briefing staat
"""

    def _call():
        return client.messages.create(
            model=model, max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )

    response = with_retry(_call, label=f"plan:{page_slug}")
    raw = "".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()
    raw = re.sub(r'^```\w*\s*', '', raw, flags=re.MULTILINE)
    raw = re.sub(r'\s*```\s*$', '', raw, flags=re.MULTILINE)

    try:
        plan = json.loads(raw.strip())
        if theme:
            plan["theme"] = theme
        return plan
    except json.JSONDecodeError as e:
        raise ValueError(f"Ongeldige JSON van Claude voor {page_slug}: {e}\n{raw[:300]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--brief",       required=True)
    parser.add_argument("--page-slug",   required=True)
    parser.add_argument("--page-title",  default="")
    parser.add_argument("--page-desc",   default="")
    parser.add_argument("--out",         required=True, help="JSON output pad")
    parser.add_argument("--image-manifest", default="")
    parser.add_argument("--nav-pages",   default="")
    parser.add_argument("--homepage",    action="store_true")
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY ontbreekt")

    briefing = Path(args.brief).read_text(encoding="utf-8", errors="ignore")

    images = []
    if args.image_manifest and Path(args.image_manifest).exists():
        try:
            images = json.loads(Path(args.image_manifest).read_text())
        except Exception:
            pass

    nav_pages = []
    if args.nav_pages:
        try:
            nav_pages = json.loads(args.nav_pages)
        except Exception:
            pass

    client = get_claude_client(api_key)
    model  = get_model()

    # Sitemap URL opslaan (wordt gebruikt door next-sitemap na de build)
    if args.homepage:
        # Lees URL uit briefing voor sitemap
        url_match = re.search(r'https?://[^\s\'"]+', briefing[:500])
        if url_match:
            sitemap_url = url_match.group(0).rstrip('/')
            url_file = Path(args.brief).parent / "site_url.txt"
            url_file.write_text(sitemap_url, encoding="utf-8")

    # Theme bepalen (alleen voor homepage, wordt gedeeld met alle pagina's)
    theme = ""
    if args.homepage:
        theme_file = Path(args.brief).parent / "theme.json"
        if theme_file.exists():
            theme = json.loads(theme_file.read_text()).get("theme", "")
        if not theme:
            print(f"[INFO] DaisyUI theme kiezen...")
            theme = pick_theme(client, model, briefing)
            theme_file.write_text(json.dumps({"theme": theme}), encoding="utf-8")
            print(f"[OK]  Theme gekozen: {theme}")
    else:
        # Gebruik bestaand theme van homepage
        theme_file = Path(args.brief).parent / "theme.json"
        if theme_file.exists():
            theme = json.loads(theme_file.read_text()).get("theme", "light")

    print(f"[INFO] Sectieplan genereren voor: {args.page_slug} ({'homepage' if args.homepage else 'subpagina'}) | theme: {theme}")

    plan = plan_page(
        client, model, briefing,
        page_slug=args.page_slug,
        page_title=args.page_title,
        page_desc=args.page_desc,
        images=images,
        nav_pages=nav_pages,
        is_homepage=args.homepage,
        theme=theme,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK]  Sectieplan opgeslagen: {out_path} ({len(plan.get('sections', []))} secties)")


if __name__ == "__main__":
    main()
