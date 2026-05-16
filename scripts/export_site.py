"""
export_site.py — Exporteer de gegenereerde site + maak een screenshot van de homepage.

CLI gebruik:
  python scripts/export_site.py --name "Omnitour"
  python scripts/export_site.py --name "Omnitour" --zip

Output:
  output/[slug]-next/screenshot_homepage.png   (altijd)
  exports/[slug].zip                           (alleen met --zip)
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROSPECTS_FILE = Path("/workspace/data/prospects.json")
OUTPUT_DIR     = Path("/workspace/output")
EXPORTS_DIR    = Path("/workspace/exports")


# ── Helpers ───────────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    return name.strip("-")


def _find_site_dir(slug: str) -> Path | None:
    """Geeft het /out pad terug als het bestaat en index.html bevat."""
    for candidate in [
        OUTPUT_DIR / f"{slug}-next" / "out",
        OUTPUT_DIR / f"{slug}-site",
    ]:
        if candidate.exists() and (candidate / "index.html").exists():
            return candidate
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _take_screenshot(site_dir: Path, out_path: Path) -> bool:
    """Start HTTP-server + Playwright screenshot van de homepage (1280×720px, 16:9)."""
    port = _free_port()

    server_proc = subprocess.Popen(
        ["python3", "-m", "http.server", str(port), "--directory", str(site_dir)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.8)

    try:
        script = (
            "from playwright.sync_api import sync_playwright\n"
            "with sync_playwright() as p:\n"
            "    browser = p.chromium.launch()\n"
            f"    page = browser.new_page(viewport={{'width': 1280, 'height': 720}})\n"
            f"    page.goto('http://127.0.0.1:{port}/', wait_until='networkidle', timeout=15000)\n"
            "    page.wait_for_timeout(1500)\n"
            f"    page.screenshot(path='{str(out_path)}', full_page=False)\n"
            "    browser.close()\n"
        )
        result = subprocess.run(
            ["python3", "-c", script],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            print(f"[WARN] Screenshot fout:\n{result.stderr[:400]}")
            return False
        return True
    except subprocess.TimeoutExpired:
        print("[WARN] Screenshot timeout na 60s")
        return False
    except Exception as e:
        print(f"[WARN] Screenshot mislukt: {e}")
        return False
    finally:
        server_proc.terminate()
        server_proc.wait(timeout=5)


def _make_zip(site_dir: Path, slug: str) -> Path:
    """Maak een ZIP van de /out directory in EXPORTS_DIR."""
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    zip_base = EXPORTS_DIR / slug
    shutil.make_archive(str(zip_base), "zip", root_dir=str(site_dir.parent), base_dir=site_dir.name)
    return Path(str(zip_base) + ".zip")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exporteer gegenereerde site + maak screenshot van homepage."
    )
    parser.add_argument("--name", required=True, help="Naam van de prospect (bijv. 'Omnitour')")
    parser.add_argument("--zip",  action="store_true", help="Pak de /out directory in als ZIP")
    parser.add_argument("--no-screenshot", action="store_true", help="Sla screenshot over")
    args = parser.parse_args()

    slug     = slugify(args.name)
    site_dir = _find_site_dir(slug)

    if site_dir is None:
        print(f"[FAIL] Geen gegenereerde site gevonden voor '{args.name}' (slug: {slug})")
        print(f"       Verwacht: {OUTPUT_DIR}/{slug}-next/out/index.html")
        sys.exit(1)

    print(f"[INFO] Site gevonden: {site_dir}")
    print(f"[INFO] HTML-bestanden: {len(list(site_dir.rglob('*.html')))}")

    # ── Screenshot ────────────────────────────────────────────────────────────
    screenshot_path = site_dir.parent / "screenshot_homepage.png"

    if args.no_screenshot:
        print("[INFO] Screenshot overgeslagen (--no-screenshot)")
    else:
        print(f"[INFO] Screenshot maken van homepage (1280×900px)...")
        ok = _take_screenshot(site_dir, screenshot_path)
        if ok:
            size_kb = screenshot_path.stat().st_size // 1024
            print(f"[OK]  Screenshot: {screenshot_path}  ({size_kb}KB)")
        else:
            print(f"[WARN] Screenshot mislukt — controleer of Playwright geïnstalleerd is")

    # ── ZIP ───────────────────────────────────────────────────────────────────
    if args.zip:
        print(f"[INFO] ZIP maken van {site_dir.name}...")
        zip_path = _make_zip(site_dir, slug)
        size_mb  = zip_path.stat().st_size / (1024 * 1024)
        print(f"[OK]  ZIP: {zip_path}  ({size_mb:.1f}MB)")

    # ── Samenvatting ──────────────────────────────────────────────────────────
    print(f"\n{'─' * 50}")
    print(f"[OK]  Export klaar voor: {args.name}")
    print(f"      Site:       {site_dir}")
    if not args.no_screenshot and screenshot_path.exists():
        print(f"      Screenshot: {screenshot_path}")
    if args.zip:
        print(f"      ZIP:        {zip_path}")
    print(f"{'─' * 50}")


if __name__ == "__main__":
    main()
