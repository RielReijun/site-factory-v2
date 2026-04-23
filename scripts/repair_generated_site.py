"""
repair_generated_site.py — Repareer veelvoorkomende fouten in een gegenereerde site.

Fixes:
1. Verkeerde CSS-link     → assets/css/style.css
2. Verkeerde JS-link      → assets/js/main.js
3. Gebroken <a href>      → verwijst naar HTML die niet bestaat → #
4. Rauwe file-markers     → ===FILE: / ===END_FILE=== verwijderen
"""
import argparse
import json
import re
import sys
from pathlib import Path


LINK_HREF_RE  = re.compile(r'<link\b([^>]*)>', re.IGNORECASE)
SCRIPT_SRC_RE = re.compile(r'(<script\b[^>]*\bsrc=["\'])([^"\']+)(["\'][^>]*>)', re.IGNORECASE)
A_HREF_RE     = re.compile(r'(<a\b[^>]*\bhref=["\'])([^"\']+)(["\'])', re.IGNORECASE)
FILE_MARKER_RE = re.compile(r'===FILE:[^\n]*\n?|===END_FILE===\n?')

EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "javascript:", "#", "//")


def is_external(href: str) -> bool:
    return any(href.startswith(p) for p in EXTERNAL_PREFIXES)


def fix_link_tags(content: str) -> tuple[str, list[str]]:
    """Vervang alle niet-standaard <link rel="stylesheet"> met de correcte CSS-link."""
    fixes = []

    def replace_link(m: re.Match) -> str:
        attrs = m.group(1)
        if 'stylesheet' not in attrs.lower():
            return m.group(0)  # geen stylesheet-link, niet aanraken
        href_m = re.search(r'href=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
        if not href_m:
            return m.group(0)
        href = href_m.group(1)
        if href == "assets/css/style.css":
            return m.group(0)  # al correct
        fixes.append(f"CSS-link {href!r} → assets/css/style.css")
        return '<link rel="stylesheet" href="assets/css/style.css">'

    new_content = LINK_HREF_RE.sub(replace_link, content)
    return new_content, fixes


def fix_script_tags(content: str, site_dir: Path, html_path: Path) -> tuple[str, list[str]]:
    """Vervang <script src> die verwijst naar ontbrekend bestand."""
    fixes = []

    def replace_script(m: re.Match) -> str:
        prefix, src, suffix = m.group(1), m.group(2), m.group(3)
        if is_external(src):
            return m.group(0)
        resolved = (html_path.parent / src).resolve()
        try:
            resolved.relative_to(site_dir.resolve())
        except ValueError:
            return m.group(0)
        if resolved.exists():
            return m.group(0)
        fixes.append(f"JS-src {src!r} → assets/js/main.js")
        return f'{prefix}assets/js/main.js{suffix}'

    new_content = SCRIPT_SRC_RE.sub(replace_script, content)
    return new_content, fixes


def fix_broken_links(content: str, site_dir: Path, html_path: Path) -> tuple[str, list[str]]:
    """Vervang <a href> die verwijst naar niet-bestaand HTML-bestand met #."""
    fixes = []

    def replace_href(m: re.Match) -> str:
        prefix, href, suffix = m.group(1), m.group(2), m.group(3)
        if is_external(href):
            return m.group(0)
        href_clean = href.split("?")[0].split("#")[0].strip()
        if not href_clean:
            return m.group(0)
        # Alleen HTML-bestanden repareren
        if not href_clean.endswith(".html"):
            return m.group(0)
        resolved = (html_path.parent / href_clean).resolve()
        try:
            resolved.relative_to(site_dir.resolve())
        except ValueError:
            return m.group(0)
        if resolved.exists():
            return m.group(0)
        fixes.append(f"Gebroken link {href_clean!r} → #")
        return f'{prefix}#{suffix}'

    new_content = A_HREF_RE.sub(replace_href, content)
    return new_content, fixes


def fix_file_markers(content: str) -> tuple[str, list[str]]:
    """Verwijder rauwe AI-output markers die in het HTML beland zijn."""
    new_content, n = FILE_MARKER_RE.subn("", content)
    fixes = [f"{n} rauwe file-marker(s) verwijderd"] if n else []
    return new_content, fixes


def repair_file(html_path: Path, site_dir: Path) -> list[str]:
    """Repareer één HTML-bestand. Geeft lijst van toegepaste fixes terug."""
    content = html_path.read_text(encoding="utf-8", errors="ignore")
    original = content
    all_fixes = []

    content, f = fix_file_markers(content)
    all_fixes.extend(f)

    content, f = fix_link_tags(content)
    all_fixes.extend(f)

    content, f = fix_script_tags(content, site_dir, html_path)
    all_fixes.extend(f)

    content, f = fix_broken_links(content, site_dir, html_path)
    all_fixes.extend(f)

    if content != original:
        html_path.write_text(content, encoding="utf-8")

    return all_fixes


def main():
    parser = argparse.ArgumentParser(description="Repareer veelvoorkomende fouten in een gegenereerde site")
    parser.add_argument("--site-dir",        required=True, help="Pad naar de gegenereerde site-map")
    parser.add_argument("--validation-json", help="Optioneel: pad naar validate_generated_site JSON-rapport")
    args = parser.parse_args()

    site_dir = Path(args.site_dir)
    if not site_dir.exists():
        print(f"[FAIL] Site-map niet gevonden: {site_dir}")
        sys.exit(1)

    # Bepaal welke bestanden gerepareerd moeten worden
    if args.validation_json:
        try:
            report = json.loads(Path(args.validation_json).read_text(encoding="utf-8"))
            # Repareer alle bestanden die FAIL-issues hebben + alle HTML's bij broken links
            files_with_issues = {i["file"] for i in report.get("issues", [])
                                 if i["level"] == "FAIL" and i["file"]}
            # Gebroken links kunnen in elk HTML-bestand zitten
            files_with_issues |= {i["file"] for i in report.get("broken_links", []) if i["file"]}
            html_to_repair = [site_dir / f for f in files_with_issues if f.endswith(".html")]
        except Exception as e:
            print(f"[WARN] Kon validation JSON niet lezen: {e} — repareer alle HTML-bestanden")
            html_to_repair = list(site_dir.glob("*.html"))
    else:
        html_to_repair = list(site_dir.glob("*.html"))

    if not html_to_repair:
        print("[INFO] Geen bestanden om te repareren")
        sys.exit(0)

    total_fixes = 0
    for html_path in sorted(html_to_repair):
        if not html_path.exists():
            continue
        fixes = repair_file(html_path, site_dir)
        if fixes:
            print(f"[OK]  {html_path.name}: {len(fixes)} fix(es)")
            for fix in fixes:
                print(f"      - {fix}")
            total_fixes += len(fixes)
        else:
            print(f"[INFO] {html_path.name}: geen fixes nodig")

    print(f"\n[OK]  Totaal {total_fixes} fix(es) toegepast in {len(html_to_repair)} bestand(en)")


if __name__ == "__main__":
    main()
