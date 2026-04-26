"""
screenshot_validate.py — Maak screenshots van de gegenereerde site en laat Claude de visuele problemen beoordelen.

Werking:
1. Start een lokale HTTP-server voor de site-directory
2. Maak screenshots van de eerste N HTML-pagina's met Playwright (headless Chromium)
3. Stuur elk screenshot naar Claude Vision
4. Sla issues op in screenshot_validation.json
5. Exit 0 altijd — dit is een waarschuwingsstap, geen blokkerende check

Vereist: playwright install chromium (in de Docker image)
"""
import argparse
import base64
import http.server
import json
import os
import sys
import threading
import time
from pathlib import Path

VISION_PROMPT = """Je bekijkt een screenshot van een gegenereerde statische website.
Beoordeel de pagina op de volgende visuele problemen en geef elk gevonden probleem een korte beschrijving.
Wees specifiek — zeg niet "mogelijk contrast-probleem", maar "witte tekst op witte achtergrond in de hero-sectie".

NEGEER volledig: een kleine demo-popup of badge rechtsonder in beeld — dit is een intentionele demo-banner en geen probleem.

Kijk op:
1. Kontrastproblemen — lichte tekst op lichte achtergrond of donkere tekst op donkere achtergrond
2. Gebroken layout — secties die over elkaar vallen, elementen buiten het scherm, kolommen die niet kloppen
3. Placeholder-content — lorem ipsum, [BEDRIJFSNAAM], ongeldige URL-tekst of duidelijk verzonnen namen (NIET de demo-popup)
4. Lege secties — grote witte vlakken zonder inhoud waar duidelijk iets hoort te staan
5. Typografische problemen — tekst die wordt afgesneden, onleesbaar kleine tekst, verkeerde lettergrootte

Geef je antwoord als JSON met één sleutel "issues" (lijst van strings). Iedere string is één probleem.
Als er geen problemen zijn, geef dan: {"issues": []}
Geef ALLEEN de JSON terug, geen uitleg."""


def start_server(site_dir: Path, port: int) -> http.server.HTTPServer:
    """Start een lokale HTTP-server die de site-directory bedient."""
    import os

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(site_dir), **kwargs)

        def log_message(self, format, *args):
            pass  # stil

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def take_screenshot(page, url: str, out_path: Path, viewport_width: int = 1280) -> bool:
    """Maak een screenshot van een URL op het opgegeven viewport-breedte."""
    try:
        page.set_viewport_size({"width": viewport_width, "height": 800})
        page.goto(url, timeout=15000, wait_until="domcontentloaded")
        page.wait_for_timeout(800)
        page.screenshot(path=str(out_path), full_page=False)
        return True
    except Exception as e:
        print(f"[WARN] Screenshot mislukt voor {url} (@{viewport_width}px): {e}")
        return False


def analyze_screenshot(image_path: Path, client, model: str) -> list[str]:
    """Stuur een screenshot naar Claude Vision en haal issues op."""
    img_data = base64.standard_b64encode(image_path.read_bytes()).decode("utf-8")
    suffix = image_path.suffix.lstrip(".")
    media_type = f"image/{'jpeg' if suffix == 'jpg' else suffix}"

    try:
        response = client.messages.create(
            model=model,
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_data,
                        },
                    },
                    {"type": "text", "text": VISION_PROMPT},
                ],
            }],
        )
        text = response.content[0].text.strip()
        # Verwijder mogelijke markdown code fences
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
        data = json.loads(text)
        return data.get("issues", [])
    except Exception as e:
        print(f"[WARN] Vision-analyse mislukt: {e}")
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-dir",  required=True, help="Pad naar gegenereerde site")
    from config import SCREENSHOT_MAX_PAGES
    parser.add_argument("--max-pages", type=int, default=SCREENSHOT_MAX_PAGES,
                        help=f"Max aantal pagina's om te screenshotten (default: {SCREENSHOT_MAX_PAGES})")
    parser.add_argument("--port",      type=int, default=9876)
    args = parser.parse_args()

    site_dir = Path(args.site_dir)
    if not site_dir.exists():
        print(f"[FAIL] Site-map niet gevonden: {site_dir}")
        sys.exit(0)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    model   = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    if not api_key:
        print("[WARN] ANTHROPIC_API_KEY ontbreekt — screenshot-validatie overgeslagen")
        sys.exit(0)

    # Pagina-strategie:
    # - index.html altijd op BEIDE viewports (375px mobiel + 1280px desktop)
    # - daarna willekeurige subpagina's op 1280px (inclusief Next.js nested routes)
    import random

    # Next.js exporteert subpagina's als over-ons/index.html — gebruik rglob
    all_html = [
        f for f in sorted(site_dir.rglob("*.html"))
        if not f.name.startswith("_") and "_next" not in str(f)
    ]
    index = site_dir / "index.html"
    skip  = {"legal.html", "404.html", "sitemap.html"}
    rest  = [f for f in all_html if f != index and f.name not in skip]
    random.shuffle(rest)

    def _url_for(html_path: "Path") -> str:
        """Bouw correcte URL voor zowel root als nested Next.js routes."""
        rel = html_path.relative_to(site_dir)
        parts = rel.parts
        if len(parts) == 1:
            # Root: index.html → /, contact.html → /contact.html
            return f"http://127.0.0.1:{args.port}/{parts[0]}"
        else:
            # Nested: over-ons/index.html → /over-ons/
            return f"http://127.0.0.1:{args.port}/{parts[0]}/"

    # Bouw een lijst van (html_path, viewport_width, label, url) tuples
    shots: list[tuple] = []
    if index.exists():
        shots.append((index, 375,  "index@mobile",  f"http://127.0.0.1:{args.port}/"))
        shots.append((index, 1280, "index@desktop", f"http://127.0.0.1:{args.port}/"))
    for subpage in rest:
        if len(shots) >= args.max_pages:
            break
        rel = subpage.relative_to(site_dir)
        label = str(rel.parent) if subpage.name == "index.html" else rel.stem
        shots.append((subpage, 1280, f"{label}@desktop", _url_for(subpage)))

    if not shots:
        print("[WARN] Geen HTML-pagina's gevonden")
        sys.exit(0)

    # Start HTTP-server
    server = start_server(site_dir, args.port)
    time.sleep(0.3)
    print(f"[INFO] HTTP-server gestart op poort {args.port}")

    tmp_dir = site_dir / "_screenshots"
    tmp_dir.mkdir(exist_ok=True)

    all_results: list[dict] = []

    try:
        from playwright.sync_api import sync_playwright
        from anthropic import Anthropic

        client = Anthropic(api_key=api_key)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = browser.new_page()

            for html_path, viewport_w, label, url in shots:
                shot_path = tmp_dir / f"{label.replace('/', '-')}_{viewport_w}.png"

                print(f"[INFO] Screenshot: {label}")
                ok = take_screenshot(page, url, shot_path, viewport_width=viewport_w)
                if not ok:
                    continue

                issues = analyze_screenshot(shot_path, client, model)
                result = {
                    "page":      html_path.name,
                    "viewport":  viewport_w,
                    "label":     label,
                    "screenshot": str(shot_path.relative_to(site_dir)),
                    "issues":    issues,
                }
                all_results.append(result)

                if issues:
                    print(f"[WARN] {label}: {len(issues)} visueel probleem/problemen")
                    for issue in issues:
                        print(f"       - {issue}")
                else:
                    print(f"[OK]  {label}: geen visuele problemen")

            page.close()
            browser.close()

    except ImportError:
        print("[WARN] playwright niet beschikbaar — screenshot-validatie overgeslagen")
        sys.exit(0)
    except Exception as e:
        print(f"[WARN] Screenshot-validatie mislukt: {e}")
        sys.exit(0)
    finally:
        server.shutdown()

    # Resultaten opslaan
    total_issues = sum(len(r["issues"]) for r in all_results)
    output = {
        "pages_checked": len(all_results),
        "total_issues":  total_issues,
        "results":       all_results,
    }
    out_path = site_dir / "screenshot_validation.json"
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    if total_issues:
        print(f"\n[WARN] Screenshot-validatie: {total_issues} visueel probleem/problemen op {len(all_results)} pagina('s)")
    else:
        print(f"\n[OK]  Screenshot-validatie: geen problemen op {len(all_results)} pagina('s)")


if __name__ == "__main__":
    main()
