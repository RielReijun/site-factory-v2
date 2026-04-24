import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline_utils import PLACEHOLDER_PATTERNS_LABELED as PLACEHOLDER_PATTERNS  # noqa: F401


# ── Verwachte bestandsstructuur ────────────────────────────────────────────────

REQUIRED_FILES = [
    "index.html",
]

# Next.js /out/ heeft geen assets/css/style.css of assets/js/main.js
# CSS zit in _next/static/chunks/, JS is gehydrateerd via Next.js runtime
EXPECTED_ASSET_FILES: list[str] = []

# ── Regexes ───────────────────────────────────────────────────────────────────

# Stylesheet <link href="..."> — matcht ongeacht attribuutvolgorde
LINK_HREF_RE = re.compile(
    r'<link\b[^>]*\bhref=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# <script src="...">
SCRIPT_SRC_RE = re.compile(
    r'<script\b[^>]*\bsrc=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# <a href="...">
A_HREF_RE = re.compile(
    r'<a\b[^>]*\bhref=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

FILE_MARKER_RE = re.compile(r"===FILE:|===END_FILE===")

CTA_PATTERN = re.compile(
    r"\b(contact|offerte|bekijk|plan|gratis intake|neem contact)\b",
    re.IGNORECASE,
)

EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "javascript:", "_next/")
from config import MIN_HOMEPAGE_CHARS


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_external(href: str) -> bool:
    return any(href.startswith(p) for p in EXTERNAL_PREFIXES)


def resolve_local_path(html_path: Path, href: str, site_dir: Path) -> Path | None:
    """
    Resolve een relatieve href naar een absoluut pad binnen site_dir.
    Geeft None terug als de href extern is, een puur anker is, of buiten site_dir uitkomt.
    """
    href_clean = href.split("#")[0].split("?")[0].strip()
    if not href_clean or is_external(href_clean):
        return None
    try:
        resolved = (html_path.parent / href_clean).resolve()
        resolved.relative_to(site_dir.resolve())
        return resolved
    except ValueError:
        return None  # pad verlaat site_dir


# ── Check-functies ────────────────────────────────────────────────────────────

def check_file_structure(site_dir: Path) -> list[tuple]:
    """Controleer aanwezigheid van verplichte en verwachte bestanden."""
    issues = []

    for fname in REQUIRED_FILES:
        if not (site_dir / fname).exists():
            issues.append(("FAIL", fname, "Verplicht bestand ontbreekt"))

    for fname in EXPECTED_ASSET_FILES:
        if not (site_dir / fname).exists():
            issues.append(("WARN", fname, "Verwacht asset-bestand ontbreekt"))

    return issues


def check_html_structure(html_path: Path, content: str) -> list[tuple]:
    """Controleer de HTML-structuur op aanwezigheid van vereiste tags."""
    fname = html_path.name
    issues = []

    if not content.strip():
        issues.append(("FAIL", fname, "HTML-bestand is leeg"))
        return issues  # verdere checks hebben geen zin

    structural_checks = [
        (r"<!doctype\s+html",           "FAIL", "<!DOCTYPE html> ontbreekt"),
        (r"<html[\s>]",                 "FAIL", "<html> tag ontbreekt"),
        (r"<head[\s>]",                 "FAIL", "<head> tag ontbreekt"),
        (r"<body[\s>]",                 "FAIL", "<body> tag ontbreekt"),
        (r"<title[\s>]",                "FAIL", "<title> tag ontbreekt"),
        (r'<meta[^>]+viewport',         "WARN", "viewport meta tag ontbreekt"),
        (r'<link[^>]+stylesheet',       "WARN", "stylesheet <link> ontbreekt"),
    ]

    for pattern, level, message in structural_checks:
        if not re.search(pattern, content, re.IGNORECASE):
            issues.append((level, fname, message))

    return issues


def check_file_markers(html_path: Path, content: str) -> list[tuple]:
    """Controleer op raw AI-output markers die niet in het eindbestand horen."""
    fname = html_path.name
    issues = []

    if FILE_MARKER_RE.search(content):
        issues.append(("FAIL", fname, "Raw file marker gevonden (===FILE: of ===END_FILE===) — output niet correct geparst"))

    return issues


def check_placeholders(html_path: Path, content: str) -> list[tuple]:
    """Controleer op bekende placeholder-teksten. Geeft [WARN] per patroon."""
    fname = html_path.name
    issues = []

    for pattern, label in PLACEHOLDER_PATTERNS:
        for lineno, line in enumerate(content.splitlines(), start=1):
            if re.search(pattern, line, re.IGNORECASE):
                context = line.strip()[:120]
                issues.append(("WARN", fname, f"Placeholder '{label}' op regel {lineno}: {context}"))
                break  # één melding per patroon per bestand

    return issues


def check_html_links(html_path: Path, content: str, site_dir: Path) -> list[tuple]:
    """
    Controleer alle relatieve links en asset-referenties in een HTML-bestand.

    - <link href> naar lokale CSS die ontbreekt → FAIL
    - <script src> naar lokale JS die ontbreekt → FAIL
    - <a href> naar lokaal HTML-bestand dat ontbreekt → WARN
    """
    fname = html_path.name
    issues = []

    # Stylesheet links
    for m in LINK_HREF_RE.finditer(content):
        href = m.group(1)
        if is_external(href):
            continue
        resolved = resolve_local_path(html_path, href, site_dir)
        if resolved is not None and not resolved.exists():
            issues.append(("FAIL", fname, f"<link> stylesheet verwijst naar ontbrekend bestand: {href}"))

    # Script sources
    for m in SCRIPT_SRC_RE.finditer(content):
        src = m.group(1)
        if is_external(src):
            continue
        resolved = resolve_local_path(html_path, src, site_dir)
        if resolved is not None and not resolved.exists():
            issues.append(("FAIL", fname, f"<script> verwijst naar ontbrekend bestand: {src}"))

    # Anchor links
    seen = set()
    for m in A_HREF_RE.finditer(content):
        href = m.group(1)
        if is_external(href):
            continue
        href_clean = href.split("#")[0].split("?")[0].strip()
        if not href_clean or href_clean in seen:
            continue
        seen.add(href_clean)

        resolved = resolve_local_path(html_path, href_clean, site_dir)
        if resolved is None:
            # Pad verlaat site_dir — voor statische sites ongebruikelijk
            issues.append(("WARN", fname, f"<a> link verlaat de site-map of is onoplosbaar: {href}"))
        elif not resolved.exists():
            issues.append(("WARN", fname, f"<a> link naar ontbrekend bestand: {href_clean}"))

    return issues


def check_content_quality(html_path: Path, content: str) -> list[tuple]:
    """Controleer inhoudskwaliteit van de homepage."""
    fname = html_path.name
    issues = []

    if not re.search(r"<h1[\s>]", content, re.IGNORECASE):
        issues.append(("WARN", fname, "Geen <h1> gevonden"))

    if not CTA_PATTERN.search(content):
        issues.append(("WARN", fname, "Geen CTA-achtige tekst gevonden (contact, plan, bekijk, offerte, gratis intake)"))

    if len(content) < MIN_HOMEPAGE_CHARS:
        issues.append(("WARN", fname, f"Homepage is verdacht kort: {len(content)} tekens (minimum: {MIN_HOMEPAGE_CHARS})"))

    return issues


def check_asset_files(site_dir: Path) -> list[tuple]:
    """Controleer bestaande asset-bestanden op lege inhoud."""
    issues = []

    for fname in EXPECTED_ASSET_FILES:
        fpath = site_dir / fname
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8", errors="ignore")
            if not content.strip():
                issues.append(("WARN", fname, "Asset-bestand bestaat maar is leeg"))

    return issues


# ── Rapport opbouwen ──────────────────────────────────────────────────────────

def validate_site(site_dir: Path) -> dict:
    """Voer alle validaties uit en geef een gestructureerd rapport terug."""

    if not site_dir.exists():
        return {
            "site_dir": str(site_dir),
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "html_files_found": 0,
            "warnings": 0,
            "failures": 1,
            "missing_files": [str(site_dir)],
            "broken_links": [],
            "placeholders": [],
            "issues": [{"level": "FAIL", "file": "", "message": f"Site-map bestaat niet: {site_dir}"}],
            "status": "FAIL",
        }

    all_issues: list[tuple] = []
    missing_files: list[str] = []
    broken_links: list[dict] = []
    placeholders: list[dict] = []

    # 1. Bestandsstructuur
    structure_issues = check_file_structure(site_dir)
    all_issues.extend(structure_issues)
    missing_files = [i[1] for i in structure_issues]

    # 2. Per HTML-bestand — ook subdirectories (Next.js /out: about/index.html)
    html_files = [
        f for f in sorted(site_dir.rglob("*.html"))
        if not f.name.startswith("_") and "_next" not in str(f)
    ]

    for html_path in html_files:
        content = html_path.read_text(encoding="utf-8", errors="ignore")

        all_issues.extend(check_html_structure(html_path, content))
        all_issues.extend(check_file_markers(html_path, content))

        ph_issues = check_placeholders(html_path, content)
        all_issues.extend(ph_issues)
        placeholders.extend({"file": i[1], "message": i[2]} for i in ph_issues)

        link_issues = check_html_links(html_path, content, site_dir)
        all_issues.extend(link_issues)
        broken_links.extend({"file": i[1], "message": i[2]} for i in link_issues)

        if html_path.name == "index.html":
            all_issues.extend(check_content_quality(html_path, content))

    # 3. Asset-kwaliteit (bestaande maar lege bestanden)
    all_issues.extend(check_asset_files(site_dir))

    failures = sum(1 for i in all_issues if i[0] == "FAIL")
    warnings = sum(1 for i in all_issues if i[0] == "WARN")

    return {
        "site_dir": str(site_dir),
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "html_files_found": len(html_files),
        "warnings": warnings,
        "failures": failures,
        "missing_files": missing_files,
        "broken_links": broken_links,
        "placeholders": placeholders,
        "issues": [{"level": i[0], "file": i[1], "message": i[2]} for i in all_issues],
        "status": "FAIL" if failures else "PASS",
    }


# ── Afdrukken ─────────────────────────────────────────────────────────────────

def print_report(report: dict) -> None:
    print(f"[INFO] Site-map:            {report['site_dir']}")
    print(f"[INFO] HTML-bestanden:      {report['html_files_found']}")
    print(f"[INFO] Validatie uitgevoerd: {report['validated_at']}")
    print()

    issues = report["issues"]
    if not issues:
        print("[OK]   Geen problemen gevonden")
    else:
        current_file = None
        for issue in sorted(issues, key=lambda i: (i["file"], i["level"])):
            if issue["file"] != current_file:
                current_file = issue["file"]
                label = current_file if current_file else "(algemeen)"
                print(f"  ── {label}")
            level = issue["level"]
            msg = issue["message"]
            pad = "  " if level in ("OK", "FAIL") else " "
            print(f"  [{level}]{pad} {msg}")
        print()

    failures = report["failures"]
    warnings = report["warnings"]
    print(f"[INFO] Failures: {failures}   Warnings: {warnings}")

    if report["status"] == "PASS":
        print("[PASS] Validatie geslaagd")
    else:
        print(f"[FAIL] Validatie mislukt — {failures} fout(en) gevonden")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Valideer een gegenereerde statische site-map"
    )
    parser.add_argument("--site-dir", required=True, help="Pad naar de gegenereerde site-map")
    parser.add_argument("--json-out", help="Optioneel: pad om JSON-rapport op te slaan")
    args = parser.parse_args()

    site_dir = Path(args.site_dir)
    report = validate_site(site_dir)

    print_report(report)

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[OK]   JSON-rapport opgeslagen: {out_path}")

    sys.exit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
