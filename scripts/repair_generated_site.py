"""
repair_generated_site.py — Post-build HTML-reparaties voor Next.js /out/ directory.

Werkt uitsluitend op HTML-bestanden. Schrijft NIET naar assets/css/style.css of
assets/js/main.js — die bestaan niet in Next.js /out/ (CSS zit in _next/static/chunks/).

Behouden functies (HTML-direct):
1. fix_file_markers     — verwijder rauwe ===FILE: markers
2. fix_broken_links     — zet verwijzingen naar ontbrekende HTML naar #
3. fix_ref_files        — verwijder interne referentiebestanden (_*.html)
4. fix_favicon          — voeg favicon toe als die ontbreekt
5. fix_lazy_loading     — voeg loading=lazy toe aan img-tags
6. check_css_coverage   — [WARN] voor classes zonder CSS-regel (diagnostisch)
7. fix_screenshot_issues — injecteer CSS-overrides als <style> block in HTML

Verwijderd (schreven naar assets/css/style.css of assets/js/main.js):
fix_link_tags, fix_script_tags, fix_sr_only, fix_footer_year, fix_js_truncation,
fix_hamburger_mismatch, fix_portfolio_filter_class, fix_lightbox_class,
fix_font_loading, fix_faq_accordion (CSS/JS deel), fix_low_contrast, fix_footer,
fix_nav_active_state, fix_keuze_card_on_light_bg, fix_card_contrast
"""
import argparse
import json
import re
import sys
from pathlib import Path

from pipeline_utils import get_claude_client


FILE_MARKER_RE = re.compile(r'===FILE:[^\n]*\n?|===END_FILE===\n?')
A_HREF_RE      = re.compile(r'(<a\b[^>]*\bhref=["\'])([^"\']+)(["\'])', re.IGNORECASE)
IMG_RE         = re.compile(r'<img\b([^>]*)>', re.IGNORECASE)
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "javascript:", "#", "//")


def is_external(href: str) -> bool:
    return any(href.startswith(p) for p in EXTERNAL_PREFIXES)


# ── Per-bestand fixes ─────────────────────────────────────────────────────────

def fix_file_markers(content: str) -> tuple[str, list[str]]:
    """Verwijder rauwe ===FILE: en ===END_FILE=== markers."""
    new = FILE_MARKER_RE.sub("", content)
    return new, ["Rauwe file-markers verwijderd"] if new != content else []


def fix_broken_links(content: str, site_dir: Path, html_path: Path) -> tuple[str, list[str]]:
    """Zet interne <a href> die naar ontbrekende bestanden wijzen naar #."""
    fixes = []

    def replace_href(m: re.Match) -> str:
        href = m.group(2)
        if is_external(href):
            return m.group(0)
        # Normaliseer Next.js routes (bijv. /over-ons/ → over-ons/index.html)
        clean = href.split("#")[0].split("?")[0].strip()
        if not clean or clean == "/":
            return m.group(0)
        # Controleer of het pad bestaat (als bestand of als directory/index.html)
        try:
            resolved = (html_path.parent / clean).resolve()
            resolved.relative_to(site_dir.resolve())
            if resolved.exists():
                return m.group(0)
            # Next.js: probeer als directory-route
            as_dir = resolved / "index.html" if not resolved.suffix else None
            if as_dir and as_dir.exists():
                return m.group(0)
        except ValueError:
            pass
        fixes.append(f"Gebroken link gerepareerd: {href} → #")
        return m.group(1) + "#" + m.group(3)

    new = A_HREF_RE.sub(replace_href, content)
    return new, fixes


def repair_file(html_path: Path, site_dir: Path) -> list[str]:
    """Voer per-bestand fixes uit op een HTML-bestand."""
    try:
        content = html_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    fixes: list[str] = []
    content, f = fix_file_markers(content)
    fixes.extend(f)
    content, f = fix_broken_links(content, site_dir, html_path)
    fixes.extend(f)

    if fixes:
        html_path.write_text(content, encoding="utf-8")
    return fixes


# ── Site-brede fixes ──────────────────────────────────────────────────────────

def fix_ref_files(site_dir: Path) -> list[str]:
    """Verwijder interne referentiebestanden (_*.html) die door de pipeline zijn aangemaakt."""
    fixes = []
    for f in site_dir.rglob("_*.html"):
        if not f.name.startswith("_"):
            continue
        f.unlink()
        fixes.append(f"Referentiefile verwijderd: {f.name}")
    return fixes


def fix_favicon(site_dir: Path) -> list[str]:
    """Maak een SVG favicon aan als die ontbreekt en voeg de link toe aan alle HTML."""
    fixes = []
    favicon_svg = site_dir / "favicon.svg"
    if not favicon_svg.exists():
        favicon_svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
            '<circle cx="16" cy="16" r="16" fill="#6366f1"/>'
            '<text x="16" y="22" text-anchor="middle" font-size="18" fill="white">◆</text>'
            '</svg>',
            encoding="utf-8",
        )
        fixes.append("favicon.svg aangemaakt")

    FAVICON_TAG = '<link rel="icon" type="image/svg+xml" href="/favicon.svg">'
    for html_path in site_dir.rglob("*.html"):
        if html_path.name.startswith("_"):
            continue
        content = html_path.read_text(encoding="utf-8", errors="ignore")
        if "favicon" in content.lower():
            continue
        new = re.sub(r"(</head>)", FAVICON_TAG + r"\n\1", content, flags=re.IGNORECASE, count=1)
        if new != content:
            html_path.write_text(new, encoding="utf-8")
            fixes.append(f"favicon link toegevoegd aan {html_path.name}")
    return fixes


def fix_lazy_loading(site_dir: Path) -> list[str]:
    """Voeg loading='lazy' toe aan alle <img> tags behalve de eerste per pagina."""
    fixes = []
    for html_path in site_dir.rglob("*.html"):
        if html_path.name.startswith("_"):
            continue
        content = html_path.read_text(encoding="utf-8", errors="ignore")
        first   = True
        changed = False

        def add_lazy(m: re.Match) -> str:
            nonlocal first, changed
            attrs = m.group(1)
            if first:
                first = False
                return m.group(0)
            if "loading=" in attrs.lower():
                return m.group(0)
            changed = True
            return f'<img loading="lazy"{attrs}>'

        new = IMG_RE.sub(add_lazy, content)
        if changed:
            html_path.write_text(new, encoding="utf-8")
            fixes.append(f"loading=lazy toegevoegd aan {html_path.name}")
    return fixes


_COVERAGE_SKIP = {
    "active", "inactive", "is-open", "is-active", "is-scrolled", "is-hidden",
    "is-visible", "is-loading", "open", "closed", "hidden", "visible",
    "disabled", "selected", "checked", "loading", "loaded", "error",
    "container", "wrapper", "inner", "outer", "row", "col", "grid",
    "sr-only", "clearfix", "js", "no-js", "lazyload", "lazyloaded",
    "site-factory-demo-banner",
}


def check_css_coverage(site_dir: Path) -> list[str]:
    """
    [WARN] voor HTML class names die geen CSS-regel hebben.
    Louter diagnostisch — past niets aan.
    In Next.js: CSS zit in _next/static/chunks/ — lees die voor coverage.
    """
    # Verzamel alle CSS uit _next/static/chunks/ en inline <style> tags
    all_css = ""
    chunks_dir = site_dir / "_next" / "static"
    if chunks_dir.exists():
        for css_file in chunks_dir.rglob("*.css"):
            try:
                all_css += css_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass
    # Ook inline <style> blokken
    for html_path in list(site_dir.rglob("*.html"))[:3]:  # alleen eerste paar
        try:
            for m in re.finditer(r'<style[^>]*>(.*?)</style>', html_path.read_text(), re.DOTALL):
                all_css += m.group(1)
        except Exception:
            pass

    if not all_css:
        return []

    all_classes: set[str] = set()
    for html_path in site_dir.rglob("*.html"):
        if html_path.name.startswith("_") or "_next" in str(html_path):
            continue
        try:
            for m in re.finditer(r'class="([^"]+)"',
                                  html_path.read_text(encoding="utf-8", errors="ignore")):
                for cls in m.group(1).split():
                    all_classes.add(cls)
        except Exception:
            pass

    missing = []
    for cls in sorted(all_classes):
        if cls in _COVERAGE_SKIP or "--" in cls or len(cls) < 3:
            continue
        if cls.startswith(("js-", "data-", "wp-")):
            continue
        if f".{cls}" not in all_css:
            missing.append(cls)

    if not missing:
        return []

    warnings = [f"[WARN] CSS ontbreekt voor class: .{cls}" for cls in missing[:10]]
    if len(missing) > 10:
        warnings.append(f"[WARN] ... en {len(missing) - 10} andere classes zonder CSS")
    for w in warnings:
        print(w)
    return []  # Geen fixes — alleen rapporteren


def fix_screenshot_issues(site_dir: Path, screenshot_json: Path) -> int:
    """
    Lees visuele issues uit screenshot_validation.json en injecteer
    gerichte CSS-overrides als <style> block in de <head> van elke HTML.
    Werkt voor zowel Next.js als statische HTML.
    """
    import os
    try:
        data = json.loads(screenshot_json.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] screenshot-repair: kan JSON niet lezen: {e}")
        return 0

    all_issues = [i for page in data.get("results", []) for i in page.get("issues", [])]
    if not all_issues:
        print("[INFO] screenshot-repair: geen visuele issues")
        return 0

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return 0

        client = get_claude_client(api_key)
    model  = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    # Lees CSS-context uit _next/static/chunks/ voor betere overrides
    css_context = ""
    chunks_dir  = site_dir / "_next" / "static"
    if chunks_dir.exists():
        for f in list(chunks_dir.rglob("*.css"))[:2]:
            try:
                css_context += f.read_text(encoding="utf-8", errors="ignore")[:3000]
            except Exception:
                pass

    issues_text = "\n".join(f"- {i}" for i in all_issues)
    prompt = f"""Je bent een CSS-expert. Schrijf ALLEEN de minimale CSS-overrides voor deze visuele issues:

{issues_text}

Huidige CSS-context (fragment):
```css
{css_context[:2000]}
```

Regels:
- Gebruik specifieke selectors met !important zodat ze Next.js bundled CSS overschrijven
- Geen uitleg, alleen CSS
- Maximaal 30 regels"""

    try:
        response = client.messages.create(
            model=model, max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        css_fix = "".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()
        css_fix = re.sub(r'^```\w*\s*', '', css_fix, flags=re.MULTILINE)
        css_fix = re.sub(r'\s*```\s*$', '', css_fix, flags=re.MULTILINE)
        if not css_fix:
            return 0

        # Injecteer als <style> block in alle HTML-bestanden
        style_block = f"\n<style>/* screenshot-repair */\n{css_fix.strip()}\n</style>"
        injected = 0
        for html_path in site_dir.rglob("*.html"):
            if html_path.name.startswith("_") or "_next" in str(html_path):
                continue
            content = html_path.read_text(encoding="utf-8", errors="ignore")
            if "screenshot-repair" in content:
                continue
            new = re.sub(r"(</head>)", style_block + r"\n\1", content, flags=re.IGNORECASE, count=1)
            if new != content:
                html_path.write_text(new, encoding="utf-8")
                injected += 1

        print(f"[OK]  screenshot-repair: CSS geïnjecteerd in {injected} HTML-bestanden")
        return len(all_issues)
    except Exception as e:
        print(f"[WARN] screenshot-repair mislukt: {e}")
        return 0


# ── Hoofd pass ────────────────────────────────────────────────────────────────

def run_one_pass(site_dir: Path, html_to_repair: list[Path]) -> tuple[int, int]:
    """Voer alle reparaties uit. Geeft (bestandsfixes, site-brede fixes) terug."""
    total_file = 0
    for html_path in html_to_repair:
        fixes = repair_file(html_path, site_dir)
        if fixes:
            total_file += len(fixes)
            for fix in fixes:
                print(f"[OK]  {fix}")

    site_fixes = 0
    for fn, label in [
        (fix_ref_files,      "ref-files"),
        (fix_favicon,        "favicon"),
        (fix_lazy_loading,   "lazy-loading"),
        (check_css_coverage, "css-coverage"),
    ]:
        result = fn(site_dir)
        site_fixes += len(result)
        if result:
            for fix in result:
                print(f"[OK]  {fix}")

    if not (total_file + site_fixes):
        print("[INFO] Geen fixes nodig")

    return total_file, site_fixes


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Post-build HTML-reparaties voor Next.js /out/")
    parser.add_argument("--site-dir",        required=True)
    parser.add_argument("--validation-json", help="validate_generated_site rapport (optioneel)")
    parser.add_argument("--screenshot-json", help="screenshot_validation.json voor visuele fixes")
    parser.add_argument("--passes",          type=int, default=1,
                        help="Reparatierondes (default: 1)")
    args = parser.parse_args()

    site_dir = Path(args.site_dir)
    if not site_dir.exists():
        print(f"[FAIL] Map niet gevonden: {site_dir}")
        sys.exit(1)

    html_to_repair = [
        f for f in sorted(site_dir.rglob("*.html"))
        if not f.name.startswith("_") and "_next" not in str(f)
    ]

    total_file = total_site = 0
    for i in range(args.passes):
        if args.passes > 1:
            print(f"\n[INFO] ── Ronde {i + 1}/{args.passes} ──")
        ff, sf = run_one_pass(site_dir, html_to_repair)
        total_file += ff
        total_site  += sf

    screenshot_fixes = 0
    if args.screenshot_json:
        shot_path = Path(args.screenshot_json)
        if shot_path.exists():
            print(f"\n[INFO] Screenshot-repair uitvoeren...")
            screenshot_fixes = fix_screenshot_issues(site_dir, shot_path)

    print(f"\n[OK]  Totaal: {total_file} bestandsfixes + {total_site} site-brede fixes + {screenshot_fixes} screenshot-fixes")


if __name__ == "__main__":
    main()
