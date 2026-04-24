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
3. Let specifiek op:
   - **Responsive layout**: flex/grid containers die op desktop gestapeld blijven terwijl ze naast elkaar zouden moeten zijn (ontbrekende @media breakpoints)
   - **page-hero's**: subpagina page-heroes die te hoog, leeg of ongestijld zijn
   - **Kaartgrids**: kaarten die overflow hebben of buiten beeld vallen
   - **CTA-secties**: inconsistente achtergrondkleur, padding of knopstijl
   - **Typografie**: h1/h2/h3 die niet de homepage-stijl volgen
   - **Spacing**: secties met te veel of te weinig padding
   - **Kaarten op donkere achtergrond**: cards met `background: white` of lichte kleur binnen een donkere section krijgen GEEN donkere tekstkleur via overerving — voeg expliciet `color: var(--color-text, #1a1a1a)` toe aan zulke kaarten en hun children (p, h3, h4)

Regels:
- Schrijf ALLEEN CSS — geen HTML, geen uitleg, geen code fences
- Maximaal 100 regels CSS
- Gebruik specifieke selectors die alleen het probleem oplossen
- Voeg altijd `@media (min-width: 768px)` breakpoints toe waar een tweekoloms-layout ontbreekt
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


SPOT_CHECK_PROMPT = """Je bekijkt een screenshot van een subpagina van een gegenereerde website.
Kijk uitsluitend op deze drie problemen en geef elk gevonden probleem als korte, specifieke zin:

1. Witte of lichte tekst op witte of lichte achtergrond (onleesbaar)
2. Elementen die buiten beeld vallen of over elkaar heen staan (gebroken layout)
3. Grote lege vlakken zonder inhoud (sectie die duidelijk content mist)

NEGEER volledig: de demo-popup of badge rechtsonder in beeld.

Geef ALLEEN JSON: {"issues": ["..."]}, of {"issues": []} als er niets is."""


def spot_check(site_dir: Path) -> int:
    """
    Screenshot één willekeurige subpagina na de cohesion pass en check
    op contrast, layout-breaks en lege secties.
    Injecteert CSS-fix als er issues zijn.
    Geeft aantal gevonden issues terug.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    model   = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    if not api_key:
        return 0

    # Kies een willekeurige subpagina (niet index, niet legal)
    skip    = {"index.html", "legal.html", "sitemap.html"}
    pages   = [f for f in sorted(site_dir.glob("*.html"))
               if not f.name.startswith("_") and f.name not in skip]
    if not pages:
        return 0

    import random, base64, http.server, threading, time, json as _json, subprocess
    target = random.choice(pages)

    # Start lokale server
    port = 9877  # Afwijkend van screenshot_validate (9876) om conflicten te voorkomen
    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(site_dir), **kw)
        def log_message(self, *a): pass

    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.3)

    shot_path = site_dir / "_screenshots" / f"_spot_{target.stem}.png"
    shot_path.parent.mkdir(exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            page    = browser.new_page()
            page.set_viewport_size({"width": 1280, "height": 800})
            page.goto(f"http://127.0.0.1:{port}/{target.name}", timeout=15000,
                      wait_until="domcontentloaded")
            page.wait_for_timeout(600)
            page.screenshot(path=str(shot_path), full_page=False)
            page.close()
            browser.close()
    except Exception as e:
        print(f"[WARN] spot_check screenshot mislukt: {e}")
        server.shutdown()
        return 0
    finally:
        server.shutdown()

    # Analyseer met Claude Vision
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    img_b64 = base64.standard_b64encode(shot_path.read_bytes()).decode()

    try:
        resp = client.messages.create(
            model=model, max_tokens=300,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                 "media_type": "image/png", "data": img_b64}},
                {"type": "text", "text": SPOT_CHECK_PROMPT},
            ]}],
        )
        raw    = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        raw    = re.sub(r'^```\w*\s*|\s*```$', '', raw, flags=re.MULTILINE)
        issues = _json.loads(raw.strip()).get("issues", [])
    except Exception as e:
        print(f"[WARN] spot_check analyse mislukt: {e}")
        return 0

    usage   = getattr(resp, "usage", None)
    in_tok  = getattr(usage, "input_tokens",  0) or 0
    out_tok = getattr(usage, "output_tokens", 0) or 0
    print(f"[INFO] spot_check {target.name}: {in_tok}in/{out_tok}out tokens")

    if not issues:
        print(f"[OK]  spot_check {target.name}: geen visuele problemen")
        return 0

    print(f"[WARN] spot_check {target.name}: {len(issues)} issue(s)")
    for i in issues:
        print(f"       - {i}")

    # Schrijf tijdelijke screenshot_validation.json en roep screenshot-repair aan
    tmp_json = site_dir / "_spot_check_issues.json"
    tmp_json.write_text(
        _json.dumps({"results": [{"page": target.name, "issues": issues}],
                     "total_issues": len(issues)}, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        r = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "repair_generated_site.py"),
             "--site-dir", str(site_dir), "--screenshot-json", str(tmp_json), "--passes", "0"],
            capture_output=True, text=True, cwd=str(Path(__file__).parent),
        )
        print(r.stdout.strip())
    except Exception as e:
        print(f"[WARN] spot_check repair mislukt: {e}")
    finally:
        tmp_json.unlink(missing_ok=True)

    return len(issues)


def main():
    parser = argparse.ArgumentParser(
        description="AI-powered cohesiecheck — maakt subpagina's consistent met de homepage"
    )
    parser.add_argument("--site-dir", required=True, help="Pad naar de gegenereerde site-map")
    parser.add_argument("--skip-spot-check", action="store_true",
                        help="Sla de post-cohesion spot check over")
    args = parser.parse_args()

    site_dir = Path(args.site_dir)
    if not site_dir.exists():
        print(f"[FAIL] Site-map niet gevonden: {site_dir}")
        sys.exit(1)

    fixes = run(site_dir)

    if not args.skip_spot_check:
        print("\n[INFO] cohesion_pass: spot check op willekeurige subpagina...")
        spot_check(site_dir)

    sys.exit(0)


if __name__ == "__main__":
    main()
