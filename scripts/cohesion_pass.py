"""
cohesion_pass.py — AI-powered cohesiecheck over alle gegenereerde pagina's.

Leest alle HTML-pagina's + style.css en vraagt Claude om:
1. CSS-overrides die subpagina-componenten consistent maken met de homepage
2. Structurele problemen zoals ontbrekende CTA-secties of telefoonlinks

Claude schrijft gerichte overrides die aan het einde van style.css worden
toegevoegd — nooit de bestaande CSS herschrijven.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path


COHESION_PROMPT = """Je bent een senior front-end developer die een gegenereerde website controleert op consistentie.

Je krijgt de HTML van alle pagina's en de huidige CSS. Jouw taak:

1. Vergelijk de subpagina's met de homepage — zoek CSS-klassen die op subpagina's voorkomen maar niet of slecht gestijld zijn
2. Schrijf gerichte CSS-overrides die subpagina's consistent maken met de homepage
3. Let specifiek op: page-hero's, kaart-secties, CTA-secties, typografie, spacing, kleuren

Regels:
- Schrijf ALLEEN CSS — geen HTML, geen uitleg, geen code fences
- Maximaal 80 regels CSS
- Gebruik specifieke selectors die alleen het probleem oplossen
- Raak NIET de bestaande goed werkende stijlen aan
- Als je geen problemen ziet: stuur precies `/* geen aanpassingen nodig */`

---

## Homepage (index.html) — de visuele referentie
{index_html}

---

## Subpagina's
{subpages}

---

## Huidige CSS (laatste deel — actieve overrides)
```css
{css_tail}
```
"""


def run(site_dir: Path, max_subpage_chars: int = 2500, css_tail_chars: int = 6000) -> int:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    model   = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    if not api_key:
        print("[WARN] cohesion_pass: ANTHROPIC_API_KEY ontbreekt")
        return 0

    index_path = site_dir / "index.html"
    css_path   = site_dir / "assets" / "css" / "style.css"

    if not index_path.exists() or not css_path.exists():
        print("[WARN] cohesion_pass: index.html of style.css niet gevonden")
        return 0

    index_html = index_path.read_text(encoding="utf-8", errors="ignore")[:4000]
    css_tail   = css_path.read_text(encoding="utf-8", errors="ignore")[-css_tail_chars:]

    # Verzamel subpagina's — sla _ bestanden en legal/sitemap over
    skip = {"legal.html", "sitemap.html", "404.html"}
    subpages_parts = []
    for html_file in sorted(site_dir.glob("*.html")):
        if html_file.name.startswith("_") or html_file.name == "index.html":
            continue
        if html_file.name in skip:
            continue
        content = html_file.read_text(encoding="utf-8", errors="ignore")[:max_subpage_chars]
        subpages_parts.append(f"### {html_file.name}\n{content}")

    if not subpages_parts:
        print("[INFO] cohesion_pass: geen subpagina's gevonden")
        return 0

    subpages_text = "\n\n---\n\n".join(subpages_parts)

    prompt = COHESION_PROMPT.format(
        index_html=index_html,
        subpages=subpages_text,
        css_tail=css_tail,
    )

    print(f"[INFO] cohesion_pass: {len(subpages_parts)} subpagina's analyseren...")

    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        css_fix = "".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ).strip()

        usage = getattr(response, "usage", None)
        in_tok  = getattr(usage, "input_tokens",  0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        print(f"[INFO] cohesion_pass: {in_tok} input / {out_tok} output tokens")

    except Exception as e:
        print(f"[WARN] cohesion_pass: Claude-aanroep mislukt: {e}")
        return 0

    if not css_fix or "geen aanpassingen nodig" in css_fix.lower():
        print("[OK]  cohesion_pass: geen CSS-aanpassingen nodig")
        return 0

    # Verwijder eventuele code fences
    css_fix = re.sub(r'^```\w*\s*', '', css_fix, flags=re.MULTILINE)
    css_fix = re.sub(r'\s*```\s*$', '', css_fix, flags=re.MULTILINE)

    injection = (
        "\n\n/* ── Cohesion-pass overrides ──────────────────────────────────────── */\n"
        + css_fix.strip()
        + "\n"
    )

    current = css_path.read_text(encoding="utf-8", errors="ignore")
    # Verwijder eerdere cohesion-pass block
    current = re.sub(
        r'\n\n/\* ── Cohesion-pass overrides.*?(?=\n\n/\*|\Z)',
        '',
        current,
        flags=re.DOTALL,
    )
    css_path.write_text(current + injection, encoding="utf-8")

    lines = css_fix.count('\n') + 1
    print(f"[OK]  cohesion_pass: {lines} regels CSS-overrides toegepast")
    return lines


def main():
    parser = argparse.ArgumentParser(
        description="AI-powered cohesiecheck — maakt subpagina's consistent met de homepage"
    )
    parser.add_argument("--site-dir", required=True, help="Pad naar de gegenereerde site-map")
    args = parser.parse_args()

    site_dir = Path(args.site_dir)
    if not site_dir.exists():
        print(f"[FAIL] Site-map niet gevonden: {site_dir}")
        sys.exit(1)

    fixes = run(site_dir)
    sys.exit(0)


if __name__ == "__main__":
    main()
