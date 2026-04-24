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

Kijk op:
1. Kontrastproblemen — lichte tekst op lichte achtergrond of donkere tekst op donkere achtergrond
2. Gebroken layout — secties die over elkaar vallen, elementen buiten het scherm, kolommen die niet kloppen
3. Placeholder-content — lorem ipsum, [BEDRIJFSNAAM], ongeldige URL-tekst of duidelijk verzonnen namen
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


def take_screenshot(page, url: str, out_path: Path) -> bool:
    """Maak een screenshot van een URL. Geeft True terug als geslaagd."""
    try:
        page.goto(url, timeout=15000, wait_until="domcontentloaded")
        page.wait_for_timeout(800)  # wacht op CSS-animaties
        page.screenshot(path=str(out_path), full_page=False)
        return True
    except Exception as e:
        print(f"[WARN] Screenshot mislukt voor {url}: {e}")
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

    # Bepaal pagina's om te screenshotten:
    # - index.html altijd als eerste (de homepage is de visuele referentie)
    # - daarna willekeurig uit de rest (subpagina's kunnen afwijkende styling hebben)
    import random
    html_files = [f for f in sorted(site_dir.glob("*.html")) if not f.name.startswith("_")]
    index      = site_dir / "index.html"
    rest       = [f for f in html_files if f != index]
    random.shuffle(rest)
    pages      = ([index] if index.exists() else []) + rest
    pages      = pages[:args.max_pages]

    if not pages:
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
            ctx  = browser.new_context(viewport={"width": 1280, "height": 800})
            page = ctx.new_page()

            for html_path in pages:
                url       = f"http://127.0.0.1:{args.port}/{html_path.name}"
                shot_path = tmp_dir / f"{html_path.stem}.png"

                print(f"[INFO] Screenshot: {html_path.name}")
                ok = take_screenshot(page, url, shot_path)
                if not ok:
                    continue

                issues = analyze_screenshot(shot_path, client, model)
                result = {
                    "page":       html_path.name,
                    "screenshot": str(shot_path.relative_to(site_dir)),
                    "issues":     issues,
                }
                all_results.append(result)

                if issues:
                    print(f"[WARN] {html_path.name}: {len(issues)} visueel probleem/problemen")
                    for issue in issues:
                        print(f"       - {issue}")
                else:
                    print(f"[OK]  {html_path.name}: geen visuele problemen")

            ctx.close()
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
