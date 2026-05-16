"""
extract_brand_colors.py — Extraheer brand-kleuren via Playwright computed styles.

Werkt voor JS-heavy sites (React/Next.js) waar CSS gebundeld is in JavaScript.
Leest computed styles van primaire CTA-knoppen en brand-elementen.

Gebruik: python extract_brand_colors.py --url https://example.com --out brand_colors.json
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path


def rgb_to_hex(rgb: str):
    m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", rgb)
    if not m:
        return None
    r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
    # Grijstinten overslaan (r≈g≈b)
    if max(abs(r - g), abs(g - b), abs(r - b)) < 20:
        return None
    # Bijna-zwart en bijna-wit overslaan
    if r + g + b < 30 or r + g + b > 720:
        return None
    return f"#{r:02x}{g:02x}{b:02x}"


JS = """() => {
    const results = [];
    const selectors = [
        'a[class*="primary"], button[class*="primary"]',
        '.btn-primary, .button-primary, .cta-primary',
        '.hero a[href], .hero button',
        'header a[href*="demo"], header a[href*="trial"], header a[href*="start"]',
        'nav a.btn, header a.btn, header button',
        'a[class*="btn"], button[class*="btn"]',
        'a[class*="cta"], button[class*="cta"]',
        'a[class*="btn-color-"], button[class*="btn-color-"]',
        'a[class*="_btn"], button[class*="_btn"]',
        'a[class*="header-primary"], button[class*="header-primary"]',
    ];
    const SKIP = new Set([
        'transparent', 'rgba(0, 0, 0, 0)', 'rgb(0, 0, 0)', 'rgb(255, 255, 255)',
    ]);
    for (const sel of selectors) {
        try {
            const els = document.querySelectorAll(sel);
            for (const el of els) {
                const st = getComputedStyle(el);
                [st.backgroundColor, st.color].forEach(c => {
                    if (c && !SKIP.has(c)) results.push(c);
                });
            }
        } catch(e) {}
    }
    return results;
}"""


def extract(url: str, out_path: Path) -> dict | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[WARN] playwright niet beschikbaar", file=sys.stderr)
        return None

    # Probeer ook www-variant als de kale URL faalt
    from urllib.parse import urlparse
    parsed = urlparse(url)
    urls_to_try = [url]
    if not parsed.netloc.startswith("www."):
        www_url = url.replace(parsed.netloc, f"www.{parsed.netloc}", 1)
        urls_to_try.append(www_url)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        loaded = False
        for try_url in urls_to_try:
            try:
                page.goto(try_url, wait_until="networkidle", timeout=25000)
                loaded = True
                break
            except Exception:
                try:
                    page.goto(try_url, wait_until="domcontentloaded", timeout=15000)
                    loaded = True
                    break
                except Exception:
                    continue
        if not loaded:
            browser.close()
            return None
        page.wait_for_timeout(1000)
        colors = page.evaluate(JS)
        browser.close()

    primary_hex = None
    for rgb, _ in Counter(colors).most_common(20):
        h = rgb_to_hex(rgb)
        if h:
            primary_hex = h
            break

    if not primary_hex:
        return None

    data = {"primary": primary_hex, "source": "playwright_computed_style"}
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    result = extract(args.url, Path(args.out))
    if result:
        print(json.dumps(result))
    else:
        print("[WARN] Geen brand-kleur gevonden", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
