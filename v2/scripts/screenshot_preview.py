#!/usr/bin/env python3
"""screenshot_preview.py — visuele check van een lokaal draaiende Astro preview.

Maakt full-page screenshots op desktop + mobiel breedte voor home en
gespecificeerde subpagina's. Schrijft naar artifacts/v2/<slug>/screenshots/.

Run dit binnen de site-factory-worker container (heeft playwright + chromium):
    docker exec site-factory-worker python3 /workspace/v2/scripts/screenshot_preview.py \\
        --slug www-beautysaloncarlijn-nl --base http://172.17.0.1:4321

Buiten de container:
    python3 v2/scripts/screenshot_preview.py --slug www-beautysaloncarlijn-nl
        (auto wrapt zichzelf in docker exec)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PAGES = ["", "tarieven/", "behandelingen/", "contact/"]
WORKER_CONTAINER = "site-factory-worker"


def _run_in_container(slug: str, pages: list[str], base_url: str) -> int:
    """Wrap zichzelf via `docker run --rm` zodat het hele v2/ pad gemount is.

    De compose-worker mount `v2/` niet (alleen app/data/output/prompts/scripts).
    `docker run --rm --network host` met een schoon container-instance pakt
    en mount het volledige project, en kan via host-network localhost:4321
    bereiken.
    """
    cmd = [
        "docker", "run", "--rm",
        "--network", "host",
        "-v", f"{PROJECT_DIR}:/workspace",
        WORKER_CONTAINER,
        "python3", "/workspace/v2/scripts/screenshot_preview.py",
        "--slug", slug,
        "--base", base_url,
        "--in-container",
    ]
    for page in pages:
        cmd.extend(["--page", page])
    return subprocess.call(cmd)


def _shoot(slug: str, pages: list[str], base_url: str) -> int:
    from playwright.sync_api import sync_playwright

    out_dir = Path("/workspace") / "artifacts" / "v2" / slug / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)

    viewports = [
        ("desktop", 1280, 900),
        ("mobile",   390, 844),
    ]

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for vp_name, width, height in viewports:
                ctx = browser.new_context(viewport={"width": width, "height": height})
                try:
                    page = ctx.new_page()
                    for path in pages:
                        url = base_url.rstrip("/") + "/" + path.lstrip("/")
                        slug_name = path.strip("/").replace("/", "-") or "home"
                        out_path = out_dir / f"{slug_name}_{vp_name}.png"
                        try:
                            page.goto(url, wait_until="networkidle", timeout=15000)
                            page.screenshot(path=str(out_path), full_page=True)
                            size_kb = out_path.stat().st_size // 1024
                            print(f"[OK]  {url:<55} → {out_path.name} ({size_kb} KB)")
                        except Exception as e:
                            print(f"[WARN] {url}: {e}")
                finally:
                    ctx.close()
        finally:
            browser.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--base", default="", help="Override preview base URL")
    parser.add_argument("--page", action="append", default=[],
                        help="Path om te screenshotten (herhaalbaar, default: home + tarieven + behandelingen + contact)")
    parser.add_argument("--in-container", action="store_true",
                        help="Skip docker-wrap, draai direct (interne flag)")
    args = parser.parse_args()

    pages = args.page or DEFAULT_PAGES

    in_container = args.in_container or os.path.exists("/.dockerenv")
    if not in_container:
        base = args.base or "http://localhost:4321"
        return _run_in_container(args.slug, pages, base)

    base_url = args.base or "http://localhost:4321"
    return _shoot(args.slug, pages, base_url)


if __name__ == "__main__":
    raise SystemExit(main())
