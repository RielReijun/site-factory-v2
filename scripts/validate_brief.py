import argparse
import json
import re
import sys
from pathlib import Path


REQUIRED_HEADINGS = [
    "# Website brief voor Claude",
    "## Project",
    "## Doelgroep",
    "## Wat er mis of zwak is aan de huidige site",
    "## Wat behouden moet blijven",
    "## Gewenste verbeteringen",
    "## Tone of voice",
    "## Designrichting",
    "## Aanbevolen paginastructuur",
    "## Belangrijkste secties op de homepage",
    "## Contentregels",
    "## Wat niet verzonnen mag worden",
    "## Technische eisen",
    "## Samenvatting in 1 alinea",
]

HARD_RISK_PATTERNS = [
    r"\b\d+x meer\b",
    r"\b\d+%\b",
    r"\bROI\b",
    r"\bmeer conversie\b",
    r"\bhogere conversie\b",
    r"\bmeer leads\b",
    r"\bmeer omzet\b",
    r"\bmarktleider\b",
]

SOFT_RISK_PATTERNS = [
    r"\bgemiddeld\b",
    r"\brevolutionair\b",
    r"\bstate of the art\b",
    r"\bgame[- ]changer\b",
    r"\bcutting-edge\b",
    r"\bdisruptive\b",
    r"\bde beste\b",
]

IGNORE_SECTIONS_FOR_RISK = {
    "## Wat niet verzonnen mag worden",
}

IGNORE_LINE_HINTS = [
    "niet schrijven",
    "niet:",
    "geen ",
    "zonder bron",
    "niet verzonnen",
    "mag niet",
    "verzin",
    "verboden",
    "in plaats daarvan",
    "superlatieven zonder onderbouwing",
]

MIN_CHARS = 2500


def load_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Bestand niet gevonden: {path}")
    return path.read_text(encoding="utf-8", errors="ignore")


def find_missing_headings(text: str):
    # Check per regel: heading moet voorkomen aan het begin van een regel
    # (met optionele trailing whitespace). Zo overleven kleine afwijkingen
    # zoals een extra spatie of Windows-regeleindes.
    lines_normalized = {line.strip() for line in text.splitlines()}
    missing = []
    for heading in REQUIRED_HEADINGS:
        if heading.strip() not in lines_normalized:
            missing.append(heading)
    return missing


def count_headings(text: str):
    return len(re.findall(r"^##? ", text, flags=re.MULTILINE))


def split_lines_with_sections(text: str):
    current_section = None
    current_submode = None
    rows = []

    for line in text.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()

        if stripped.startswith("## "):
            current_section = stripped
            current_submode = None

        if lowered in {"**niet:**", "**niet schrijven:**"}:
            current_submode = "forbidden_examples"
        elif lowered.startswith("**wel"):
            current_submode = "allowed_examples"
        elif stripped.startswith("**") and stripped.endswith("**") and lowered not in {"**niet:**", "**niet schrijven:**"}:
            current_submode = None

        rows.append({
            "section": current_section,
            "submode": current_submode,
            "line": line,
            "stripped": stripped,
        })

    return rows


def line_should_be_ignored_for_risk(row: dict) -> bool:
    section = row["section"]
    stripped = row["stripped"].lower()

    if section in IGNORE_SECTIONS_FOR_RISK:
        return True

    if row.get("submode") == "forbidden_examples":
        return True

    for hint in IGNORE_LINE_HINTS:
        if hint in stripped:
            return True

    return False


def find_pattern_matches(rows, patterns):
    findings = []

    for row in rows:
        if not row["stripped"]:
            continue

        if line_should_be_ignored_for_risk(row):
            continue

        for pattern in patterns:
            matches = re.finditer(pattern, row["line"], flags=re.IGNORECASE)
            for match in matches:
                findings.append({
                    "section": row["section"],
                    "submode": row["submode"],
                    "pattern": pattern,
                    "match": match.group(0),
                    "line": row["stripped"],
                })

    return findings


def validate_brief(path: Path):
    text = load_text(path)
    rows = split_lines_with_sections(text)

    missing_headings = find_missing_headings(text)
    hard_risks = find_pattern_matches(rows, HARD_RISK_PATTERNS)
    soft_risks = find_pattern_matches(rows, SOFT_RISK_PATTERNS)

    result = {
        "file": str(path),
        "char_count": len(text),
        "heading_count": count_headings(text),
        "missing_headings": missing_headings,
        "hard_risks": hard_risks,
        "soft_risks": soft_risks,
        "looks_too_short": len(text) < MIN_CHARS,
        "passes": True,
    }

    if missing_headings:
        result["passes"] = False

    if result["looks_too_short"]:
        result["passes"] = False

    if hard_risks:
        result["passes"] = False

    return result


def print_findings(title: str, items: list):
    if not items:
        print(f"[OK] Geen {title.lower()} gevonden")
        return

    print(f"[WARN] {title}:")
    for item in items:
        print(f"  - Match: {item['match']}")
        print(f"    Section: {item['section']}")
        print(f"    Pattern: {item['pattern']}")
        print(f"    Line: {item['line']}")


def print_report(result: dict):
    print(f"[INFO] Bestand: {result['file']}")
    print(f"[INFO] Aantal tekens: {result['char_count']}")
    print(f"[INFO] Aantal headings: {result['heading_count']}")

    if result["looks_too_short"]:
        print(f"[WARN] Briefing lijkt te kort (< {MIN_CHARS} tekens)")
    else:
        print(f"[OK] Briefinglengte is voldoende")

    if result["missing_headings"]:
        print("[FAIL] Ontbrekende headings:")
        for heading in result["missing_headings"]:
            print(f"  - {heading}")
    else:
        print("[OK] Alle verplichte headings gevonden")

    print_findings("Hard risks", result["hard_risks"])
    print_findings("Soft risks", result["soft_risks"])

    if result["passes"]:
        print("[PASS] Briefing is structureel geldig")
    else:
        print("[FAIL] Briefing is NIET structureel geldig")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="Pad naar briefing.md")
    parser.add_argument("--json", action="store_true", help="Print alleen JSON output")
    args = parser.parse_args()

    path = Path(args.file)
    result = validate_brief(path)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print_report(result)

    if not result["passes"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
