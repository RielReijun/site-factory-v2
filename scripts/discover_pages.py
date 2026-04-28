"""
discover_pages.py — Bepaal welke HTML-pagina's gebouwd moeten worden op basis van de briefing.

Output:
  {out_path}  — pages.json met:
                  "pages":          max 20 te genereren pagina's
                  "overflow_pages": aanvullende pagina's die logisch bestaan maar niet auto-gegenereerd worden
"""
import argparse
import json
import os
import re
from pathlib import Path

from pipeline_utils import get_claude_client

MAX_AUTO_PAGES = 20


def _sanitize_pages(raw_pages: list) -> list:
    seen = set()
    clean = []
    for p in raw_pages:
        f = p.get("file", "").strip().lower()
        if not f:
            continue
        # Auto-voeg .html toe als het ontbreekt (LLM vergeet dit soms)
        if "." not in f:
            f = f + ".html"
        elif not f.endswith(".html"):
            continue
        # Geen subdirectories, geen homepage
        if "/" in f or f == "index.html":
            continue
        if f in seen:
            continue
        seen.add(f)
        clean.append({"file": f, "title": p.get("title", f), "description": p.get("description", "")})
    return clean


def discover_pages(client: Anthropic, model: str, briefing_text: str,
                   company_name: str) -> tuple[list[dict], list[dict]]:
    prompt = f"""
Je bent een senior webstrateeg.

Analyseer de briefing voor **{company_name}** en stel een volledige paginalijst op voor de statische website.

## Regels voor `pages` (worden automatisch gegenereerd)
- index.html is de homepage en staat NIET in deze lijst
- Maximum {MAX_AUTO_PAGES} pagina's
- Combineer bewust kleine, verwante pagina's:
  - privacy + algemene voorwaarden + disclaimer → legal.html
  - FAQ + huisregels → info.html (of verwerk in over-ons)
  - Meerdere kleine service-subcategorieën → één categorie-pagina
- Behoud altijd minstens: over-ons.html, contact.html
- Prioriteer pagina's met de hoogste bezoekers- en conversiewaarde
- Bestandsnamen: lowercase, koppeltekens, .html extensie, GEEN subdirectories

## Regels voor `overflow_pages` (optioneel, handmatig te genereren)
- Pagina's die logisch bij de site horen maar de limiet van {MAX_AUTO_PAGES} overschrijden
- Ook: pagina's die te specifiek zijn voor auto-generatie maar wel nuttig kunnen zijn
- Voorbeelden: individuele dienstpagina's, blog-artikelen, vacatures, partners
- Laat dit leeg (`[]`) als alle relevante pagina's al in `pages` zitten

## Briefing
{briefing_text[:20000]}

Geef ALLEEN dit JSON-object terug, zonder uitleg:
{{
  "pages": [
    {{"file": "over-ons.html",  "title": "Over ons",  "description": "Team, werkwijze, visie"}},
    {{"file": "diensten.html",  "title": "Diensten",  "description": "Overzicht aangeboden diensten"}},
    {{"file": "contact.html",   "title": "Contact",   "description": "Contactformulier, adres, openingstijden"}}
  ],
  "overflow_pages": [
    {{"file": "dienst-a.html", "title": "Dienst A", "description": "Gedetailleerde pagina over dienst A"}}
  ]
}}
"""
    response = client.messages.create(
        model=model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = "".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()
    raw = re.sub(r'^\s*```\w*\s*\n', '', raw)
    raw = re.sub(r'\n\s*```\s*$', '', raw)
    data = json.loads(raw.strip())
    pages          = _sanitize_pages(data.get("pages", []))[:MAX_AUTO_PAGES]
    overflow_pages = _sanitize_pages(data.get("overflow_pages", []))

    # Zorg dat overflow geen duplicaten bevat van pages
    page_files = {p["file"] for p in pages}
    overflow_pages = [p for p in overflow_pages if p["file"] not in page_files]

    return pages, overflow_pages


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--brief",   required=True, help="Pad naar briefing.md")
    parser.add_argument("--company", required=True, help="Bedrijfsnaam")
    parser.add_argument("--out",     required=True, help="Pad naar output pages.json")
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    model   = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY ontbreekt")

    client = get_claude_client(api_key)
    briefing_text = Path(args.brief).read_text(encoding="utf-8", errors="ignore")

    print(f"[INFO] Pagina's ontdekken voor: {args.company}")
    print(f"[INFO] Model: {model}")

    pages, overflow_pages = discover_pages(client, model, briefing_text, args.company)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"pages": pages, "overflow_pages": overflow_pages}, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print(f"[OK]  {len(pages)} pagina's automatisch te genereren:")
    for p in pages:
        print(f"      - {p['file']} — {p['title']}")
    if overflow_pages:
        print(f"[INFO] {len(overflow_pages)} overflow-pagina's (handmatig te genereren):")
        for p in overflow_pages:
            print(f"      - {p['file']} — {p['title']}")
    print(f"[OK]  Paginalijst opgeslagen: {out_path}")


if __name__ == "__main__":
    main()
