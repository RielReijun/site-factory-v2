import argparse
import difflib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from pipeline_utils import get_claude_client
from validate_brief import validate_brief, REQUIRED_HEADINGS


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def format_validation_issues(result: dict) -> str:
    lines = []
    if result.get("missing_headings"):
        lines.append("Ontbrekende headings:")
        for h in result["missing_headings"]:
            lines.append(f"- {h}")
    if result.get("hard_risks"):
        lines.append("Hard risks (ongefundeerde claims):")
        for item in result["hard_risks"]:
            lines.append(f"- Match: {item['match']} | Section: {item['section']} | Line: {item['line']}")
    if result.get("looks_too_short"):
        lines.append("- Briefing lijkt te kort")
    return "\n".join(lines).strip()


def sections_to_fix(result: dict) -> list[str]:
    """Bepaal welke secties herschreven of aangemaakt moeten worden."""
    needed = []

    for h in result.get("missing_headings", []):
        needed.append(f"{h}  ← ontbreekt volledig, schrijf deze sectie aan")

    seen_sections = set()
    for item in result.get("hard_risks", []):
        s = item.get("section")
        if s and s not in seen_sections:
            seen_sections.add(s)
            needed.append(f"{s}  ← bevat ongefundeerde claim '{item['match']}', herschrijf zonder percentages/ROI/statistieken")

    return needed


def extract_section(brief: str, heading: str) -> str:
    """Extraheer één sectie uit de briefing op basis van de heading."""
    lines = brief.splitlines()
    level = len(heading) - len(heading.lstrip("#"))
    start = None
    for i, line in enumerate(lines):
        if line.strip() == heading.strip():
            start = i
            break
    if start is None:
        return ""
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^#{1,%d} " % level, lines[i].strip()):
            end = i
            break
    return "\n".join(lines[start:end]).strip()


_HEADING_DEFAULTS = {
    "## Doelgroep": (
        "- Particulieren die hun woning willen laten stukadoren of pleisteren\n"
        "- AANNEMELIJK ook zakelijke opdrachtgevers zoals aannemers of bouwbedrijven\n"
        "- Huiseigenaren die kwaliteit en betrouwbaarheid boven prijs stellen\n"
        "- Lokale zoekers op termen als de bedrijfsnaam of branche + plaatsnaam"
    ),
    "## Tone of voice": (
        "- Nuchter en direct — vakman die zichzelf laat zien door zijn werk\n"
        "- Geen superlatieven of marketingjargon\n"
        "- Persoonlijk en toegankelijk, maar zakelijk van toon\n"
        "- Gebruik AANNEMELIJK bij onzekerheden in positionering of toon"
    ),
    "## Designrichting": (
        "- Clean en professioneel, foto's van uitgevoerd werk leidend\n"
        "- AANNEMELIJK neutrale kleurpalet: wit, grijs of een branchepassende accentkleur\n"
        "- Geen overdreven decoratie — het werk spreekt voor zich\n"
        "- Mobielvriendelijk first: grote knoppen, leesbare tekst, klikbaar telefoonnummer"
    ),
    "## Wat niet verzonnen mag worden": (
        "- Concrete prijzen of tarieven\n"
        "- Specifieke klantreviews of namen (tenzij aanwezig in de research)\n"
        "- Exacte projectlocaties of adressen van klanten\n"
        "- Certificeringen of lidmaatschappen die niet in de research staan\n"
        "- Garantiepercentages of doorlooptijdbeloften"
    ),
}


def inject_missing_headings(brief: str, missing_headings: list[str]) -> str:
    """Voeg ontbrekende verplichte secties deterministisch toe vóór ## Technische eisen."""
    if not missing_headings:
        return brief

    anchor = "## Technische eisen"
    insert_pos = brief.find(f"\n{anchor}")
    if insert_pos == -1:
        insert_pos = len(brief)

    new_sections = []
    for heading in missing_headings:
        default_body = _HEADING_DEFAULTS.get(heading)
        if default_body:
            new_sections.append(f"{heading}\n{default_body}")
        else:
            new_sections.append(f"{heading}\n- AANNEMELIJK — vul aan op basis van de research")

    block = "\n\n" + "\n\n".join(new_sections) + "\n\n"
    return brief[:insert_pos] + block + brief[insert_pos:]


def build_repair_prompt(current_brief: str, validation_result: dict) -> str:
    """Bouw prompt alleen voor hard_risks — ontbrekende headings worden deterministisch afgehandeld."""
    hard_risks = validation_result.get("hard_risks", [])
    if not hard_risks:
        return ""

    relevant_sections = []
    for item in hard_risks:
        heading = item.get("section", "")
        if heading:
            snippet = extract_section(current_brief, heading)
            if snippet:
                relevant_sections.append(snippet)
    context = "\n\n".join(relevant_sections) or current_brief[:4000]

    fix_list = "\n".join(
        f"- {item['section']}  ← bevat ongefundeerde claim '{item['match']}', herschrijf zonder percentages/ROI/statistieken"
        for item in hard_risks
    )

    return f"""
Je bent een senior webstrateeg en editor.

Repareer ALLEEN de aangegeven secties van een website briefing.
Geef alleen de gecorrigeerde secties terug — geen uitleg, geen andere secties.

## Aanwijzingen
- Begin elke sectie met de exacte heading (bijv. ## Doelgroep)
- Maximaal 12 regels per sectie, gebruik bullets
- Geen ongefundeerde claims: geen percentages, geen ROI, geen statistieken
- Gebruik AANNEMELIJK voor onzekerheden

## Te repareren secties
{fix_list}

## Huidige inhoud van de betreffende secties
{context}
"""


def apply_section_patches(original: str, patch: str) -> str:
    """Vervang secties in de originele brief met gecorrigeerde versies uit de patch."""
    patch_sections: dict[str, str] = {}
    current_heading = None
    current_lines: list[str] = []

    for line in (patch + "\n").splitlines():
        stripped = line.strip()
        if re.match(r"^#{1,3} ", stripped):
            if current_heading is not None:
                patch_sections[current_heading] = "\n".join(current_lines).rstrip()
            current_heading = stripped
            current_lines = []
        elif current_heading is not None:
            current_lines.append(line)
    if current_heading is not None:
        patch_sections[current_heading] = "\n".join(current_lines).rstrip()

    if not patch_sections:
        return original

    lines = original.splitlines()

    for heading, new_content in patch_sections.items():
        level = len(heading) - len(heading.lstrip("#"))

        start_idx = None
        for i, line in enumerate(lines):
            if line.strip() == heading:
                start_idx = i
                break

        if start_idx is not None:
            end_idx = len(lines)
            for i in range(start_idx + 1, len(lines)):
                if re.match(r"^#{1,%d} " % level, lines[i].strip()):
                    end_idx = i
                    break
            new_section = [heading] + new_content.splitlines()
            lines = lines[:start_idx] + new_section + lines[end_idx:]
        else:
            lines += ["", heading] + new_content.splitlines()

    return "\n".join(lines)


def extract_response_text(response) -> str:
    return "\n".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file",     required=True, help="Pad naar briefing.md")
    parser.add_argument("--in-place", action="store_true", help="Overschrijf originele briefing")
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    model   = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY ontbreekt in environment")

    briefing_path = Path(args.file)
    if not briefing_path.exists():
        raise FileNotFoundError(f"Briefing niet gevonden: {briefing_path}")

    validation_result = validate_brief(briefing_path)
    current_brief     = read_text(briefing_path)

    print(f"[INFO] Repair briefing: {briefing_path}")
    print(f"[INFO] Model: {model}")

    backup_path = briefing_path.with_suffix(".before-repair.md")
    shutil.copyfile(briefing_path, backup_path)

    repaired_brief = current_brief

    # Stap 1: ontbrekende headings deterministisch invullen (geen LLM nodig)
    missing = validation_result.get("missing_headings", [])
    if missing:
        print(f"[INFO] {len(missing)} ontbrekende heading(s) deterministisch toevoegen:")
        for h in missing:
            print(f"       + {h}")
        repaired_brief = inject_missing_headings(repaired_brief, missing)

    # Stap 2: hard_risks via LLM repareren (alleen als er zijn)
    hard_risks = validation_result.get("hard_risks", [])
    stop_reason = None
    output_tokens = 0

    if hard_risks:
        print(f"[INFO] {len(hard_risks)} hard risk(s) via LLM repareren:")
        for item in hard_risks:
            print(f"       ! {item['section']}: '{item['match']}'")

        client = get_claude_client(api_key)
        prompt = build_repair_prompt(repaired_brief, validation_result)

        patch_text = ""
        with client.messages.stream(
            model=model,
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
                patch_text += text
            print()
            final = stream.get_final_message()

        patch_text = patch_text.strip()
        if patch_text:
            repaired_brief = apply_section_patches(repaired_brief, patch_text)
        stop_reason   = getattr(final, "stop_reason", None)
        output_tokens = getattr(final.usage, "output_tokens", 0) if getattr(final, "usage", None) else 0
    else:
        print("[INFO] Geen hard risks — geen LLM-aanroep nodig")

    output_path = briefing_path if args.in_place else briefing_path.with_name(briefing_path.stem + ".repaired.md")
    write_text(output_path, repaired_brief)

    # Sla een leesbaar diff-bestand op zodat je altijd kunt zien wat er gerepareerd werd
    diff_lines = list(difflib.unified_diff(
        current_brief.splitlines(keepends=True),
        repaired_brief.splitlines(keepends=True),
        fromfile="briefing.before-repair.md",
        tofile="briefing.md",
    ))
    diff_path = briefing_path.with_name("repair_diff.txt")
    if diff_lines:
        write_text(diff_path, "".join(diff_lines))
        print(f"[OK] Diff opgeslagen: {diff_path} ({len(diff_lines)} regels)")
    else:
        print("[INFO] Geen wijzigingen — geen diff opgeslagen")

    meta_path = briefing_path.with_name("repair_meta.json")
    meta = {
        "model":       model,
        "repaired_at": datetime.now(timezone.utc).isoformat(),
        "source_file": str(briefing_path),
        "output_file": str(output_path),
        "backup_file": str(backup_path),
        "diff_file":   str(diff_path) if diff_lines else None,
        "changed":     bool(diff_lines),
        "stop_reason": stop_reason,
        "usage": {"output_tokens": output_tokens},
    }
    write_text(meta_path, json.dumps(meta, indent=2, ensure_ascii=False))

    print(f"[OK] Backup gemaakt: {backup_path}")
    print(f"[OK] Gerepareerde briefing opgeslagen: {output_path}")
    print(f"[OK] Metadata opgeslagen: {meta_path}")
    if output_tokens:
        print(f"[INFO] Output tokens gebruikt: {output_tokens}")


if __name__ == "__main__":
    main()
