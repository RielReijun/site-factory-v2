"""
validate_generated_content.py — Controleer of de gegenereerde site echte bedrijfsinhoud bevat.

Checks:
1. Geen Lorem ipsum of placeholder-tekst
2. Bedrijfsnaam komt voor in de HTML
3. Telefoonnummer (als bekend uit structured_data.json) staat in de HTML
4. E-mailadres (als bekend) staat in de HTML
5. Geen ongesloten placeholders zoals [BEDRIJFSNAAM], {{naam}}, etc.
6. Alle pagina's uit de paginastructuur in de briefing zijn aanwezig

Output: geeft warnings naar stdout, exit 0 altijd (warnings blokkeren de pipeline niet).
Sla resultaten op als content_validation.json in de site-dir.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from pipeline_utils import PLACEHOLDER_PATTERNS_REGEX as PLACEHOLDER_PATTERNS


def load_structured_data(collected_path: Path) -> dict:
    sd_path = collected_path / "structured_data.json"
    if sd_path.exists():
        try:
            return json.loads(sd_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def get_all_html_text(site_dir: Path) -> str:
    """Combineer zichtbare tekst van alle HTML-bestanden."""
    parts = []
    for html_path in sorted(site_dir.glob("*.html")):
        if html_path.name.startswith("_"):
            continue
        parts.append(html_path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def normalize_phone(phone: str) -> str:
    """Verwijder spaties, streepjes en haakjes voor vergelijking."""
    return re.sub(r"[\s\-().+]", "", phone)


def check_company_name(html_text: str, company_name: str) -> list[str]:
    warnings = []
    # Gebruik de eerste twee woorden voor een soepelere match
    words = company_name.strip().split()
    search = " ".join(words[:2]) if len(words) >= 2 else company_name
    if search.lower() not in html_text.lower():
        warnings.append(f"Bedrijfsnaam '{search}' niet gevonden in de gegenereerde site")
    return warnings


def check_contact_info(html_text: str, structured: dict) -> list[str]:
    warnings = []
    contact = structured.get("contact", {})

    phone = contact.get("phone", "")
    if phone:
        norm_phone  = normalize_phone(phone)
        norm_html   = normalize_phone(html_text)
        if norm_phone not in norm_html:
            warnings.append(f"Telefoonnummer '{phone}' niet gevonden in de gegenereerde site")

    email = contact.get("email", "")
    if email and email.lower() not in html_text.lower():
        warnings.append(f"E-mailadres '{email}' niet gevonden in de gegenereerde site")

    return warnings


def check_placeholders(site_dir: Path) -> list[str]:
    warnings = []
    for html_path in sorted(site_dir.glob("*.html")):
        if html_path.name.startswith("_"):
            continue
        content = html_path.read_text(encoding="utf-8", errors="ignore")
        for pattern in PLACEHOLDER_PATTERNS:
            matches = pattern.findall(content)
            if matches:
                warnings.append(
                    f"{html_path.name}: placeholder '{matches[0]}' gevonden ({len(matches)}×)"
                )
    return warnings


def check_briefing_pages(site_dir: Path, collected_path: Path) -> list[str]:
    """Controleer of pagina's uit de briefing-paginastructuur aanwezig zijn."""
    warnings = []
    briefing_path = collected_path / "briefing.md"
    if not briefing_path.exists():
        return []

    briefing = briefing_path.read_text(encoding="utf-8", errors="ignore")

    # Haal paginastructuur op: regels als 'index.html — ...'
    page_section_re = re.compile(
        r'## Aanbevolen paginastructuur(.*?)(?=\n##|\Z)', re.DOTALL | re.IGNORECASE
    )
    m = page_section_re.search(briefing)
    if not m:
        return []

    page_re = re.compile(r'(\w[\w\-]*\.html)', re.IGNORECASE)
    expected_pages = page_re.findall(m.group(1))

    existing = {f.name.lower() for f in site_dir.glob("*.html")}
    for page in expected_pages:
        if page.lower() not in existing:
            warnings.append(f"Verwachte pagina '{page}' ontbreekt in de gegenereerde site")

    return warnings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-dir",       required=True)
    parser.add_argument("--collected-path", required=True)
    parser.add_argument("--company",        required=True)
    args = parser.parse_args()

    site_dir       = Path(args.site_dir)
    collected_path = Path(args.collected_path)
    company_name   = args.company

    if not site_dir.exists():
        print(f"[FAIL] Site-map niet gevonden: {site_dir}")
        sys.exit(0)  # Geen harde fout — pipeline gaat door

    structured = load_structured_data(collected_path)
    html_text  = get_all_html_text(site_dir)

    all_warnings: list[str] = []

    all_warnings += check_company_name(html_text, company_name)
    all_warnings += check_contact_info(html_text, structured)
    all_warnings += check_placeholders(site_dir)
    all_warnings += check_briefing_pages(site_dir, collected_path)

    result = {
        "company":  company_name,
        "warnings": all_warnings,
        "ok":       len(all_warnings) == 0,
    }

    out_path = site_dir / "content_validation.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    if all_warnings:
        print(f"[WARN] Content-check: {len(all_warnings)} waarschuwing(en)")
        for w in all_warnings:
            print(f"       - {w}")
    else:
        print(f"[OK]  Content-check geslaagd voor {company_name}")


if __name__ == "__main__":
    main()
