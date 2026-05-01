#!/usr/bin/env python3
"""capture_reference.py — screenshot de originele website van een prospect.

Voor elke prospect met meta.url, render het origineel via playwright op
1440px en sla op als artifacts/v2/<slug>/_reference.png. Het dashboard
toont deze screenshot naast onze gegenereerde versie zodat een visuele
diff per oogopslag mogelijk is.

Tegelijk extraheren we 4 referentie-signalen die de render kan honoreren:
  - dominant brightness (gemiddelde helderheid)
  - hero aspect-hint (heeft de site een grote hero-foto bovenaan?)
  - section count (hoeveel duidelijke section-changes telt de pagina)

Schrijft naar artifacts/v2/<slug>/_reference.json zodat build_plan deze
later kan inlezen.

Run via docker:
    python3 v2/scripts/capture_reference.py --slug <slug>
    python3 v2/scripts/capture_reference.py --all
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

ARTIFACTS_DIR = PROJECT_DIR / "artifacts" / "v2"
DATA_DIR = PROJECT_DIR / "data"
DOCKER_IMAGE = "site-factory-worker"


def _list_slugs(filter_slug: str | None) -> list[str]:
    if filter_slug:
        return [filter_slug] if (DATA_DIR / filter_slug / "meta.json").exists() else []
    return sorted(
        d.name for d in DATA_DIR.iterdir()
        if d.is_dir() and (d / "meta.json").exists() and not d.name.startswith("trash")
    )


def _load_url(slug: str) -> str:
    try:
        meta = json.loads((DATA_DIR / slug / "meta.json").read_text(encoding="utf-8"))
        return (meta.get("url") or "").strip()
    except Exception:
        return ""


def _capture_via_docker(slug: str, url: str, timeout_s: int = 25) -> dict:
    """Run playwright in docker om de originele site te screenshotten."""
    out_dir = ARTIFACTS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    rel_out = out_dir.relative_to(PROJECT_DIR)
    script = f"""
import json, sys
from playwright.sync_api import sync_playwright
url = sys.argv[1]
out_png = sys.argv[2]
out_json = sys.argv[3]
result = {{"url": url, "method": "playwright"}}
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={{"width": 1440, "height": 900}})
        page = ctx.new_page()
        page.goto(url, wait_until="networkidle", timeout={timeout_s * 1000})
        page.screenshot(path=out_png, full_page=False, clip={{"x":0,"y":0,"width":1440,"height":900}})
        # Probeer wat aanvullende heuristiek: vrgr aantal section-elements
        try:
            sections_count = page.evaluate("document.querySelectorAll('section').length")
            h2_count = page.evaluate("document.querySelectorAll('h2').length")
            has_hero_image = page.evaluate('''() => {{
                const imgs = document.querySelectorAll("img");
                for (const img of imgs) {{
                    const r = img.getBoundingClientRect();
                    if (r.top < 200 && r.width > 600) return true;
                }}
                return false;
            }}''')
            result["sections_count"] = sections_count
            result["h2_count"] = h2_count
            result["has_hero_image"] = has_hero_image
        except Exception as e:
            result["heuristics_error"] = str(e)
        browser.close()
    result["status"] = "ok"
except Exception as e:
    result["status"] = "failed"
    result["error"] = str(e)[:300]
with open(out_json, "w") as f:
    json.dump(result, f, indent=2)
print(json.dumps(result))
"""
    cmd = [
        "docker", "run", "--rm",
        "--network", "host",
        "-v", f"{PROJECT_DIR}:/workspace",
        "-w", "/workspace",
        DOCKER_IMAGE,
        "python3", "-c", script,
        url,
        f"/workspace/{rel_out}/_reference.png",
        f"/workspace/{rel_out}/_reference.json",
    ]
    import subprocess
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + 30)
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        return {"status": "parse_failed", "stdout": proc.stdout[-300:], "stderr": proc.stderr[-300:]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", help="Single prospect")
    parser.add_argument("--all", action="store_true", help="Alle prospects")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Sla over als _reference.png al bestaat")
    args = parser.parse_args()
    slugs = _list_slugs(args.slug if not args.all else None)
    if not slugs:
        print("[FAIL] geen prospects geselecteerd")
        return 1
    print(f"[INFO] {len(slugs)} prospect(s) te capturen")
    for slug in slugs:
        out_png = ARTIFACTS_DIR / slug / "_reference.png"
        if args.skip_existing and out_png.exists():
            print(f"  [SKIP] {slug}: bestaat al")
            continue
        url = _load_url(slug)
        if not url:
            print(f"  [WARN] {slug}: geen URL in meta.json")
            continue
        print(f"  [...] {slug}: {url}")
        result = _capture_via_docker(slug, url)
        status = result.get("status", "?")
        if status == "ok":
            sec = result.get("sections_count", "?")
            h2 = result.get("h2_count", "?")
            print(f"  [OK ] {slug}  sections={sec}  h2={h2}")
        else:
            print(f"  [FAIL] {slug}: {result.get('error', status)[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
