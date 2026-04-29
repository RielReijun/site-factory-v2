import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


FILE_START_PREFIX = "===FILE:"
FILE_END_MARKER = "===END_FILE==="
MANIFEST_FILENAME = "site_manifest.json"


def _normalize_marker(stripped: str) -> str:
    """Strip markdown heading/quote prefixes (# / ## / >) that AI sometimes adds."""
    return re.sub(r'^[#>]+\s*', '', stripped)


def read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Bestand niet gevonden: {path}")
    return path.read_text(encoding="utf-8", errors="ignore")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def is_safe_path(base_dir: Path, filename: str) -> bool:
    """Controleer of het pad na resolving binnen base_dir blijft."""
    if not filename:
        return False
    try:
        resolved = (base_dir / filename).resolve()
        resolved.relative_to(base_dir.resolve())
        return True
    except ValueError:
        return False


def parse_blocks(raw: str) -> list[dict]:
    """
    Parse alle ===FILE: pad=== ... ===END_FILE=== blokken uit raw text.

    State machine: outside -> inside -> outside.
    Gooit ValueError bij structuurfouten (nested blok, lege naam).
    Markeert het laatste blok als truncated als EOF bereikt wordt terwijl we inside zijn.

    Elke block: {filename, content, truncated}
    """
    blocks = []
    lines = raw.splitlines()

    state = "outside"
    current_filename = None
    current_lines = []

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        normalized = _normalize_marker(stripped)
        is_start = normalized.startswith(FILE_START_PREFIX) and normalized.endswith("===")
        is_end = stripped == FILE_END_MARKER or normalized == FILE_END_MARKER

        if is_start:
            if state == "inside":
                raise ValueError(
                    f"Regel {lineno}: nieuw ===FILE: blok gevonden terwijl '{current_filename}' "
                    f"nog niet afgesloten was met ===END_FILE==="
                )
            # Extraheer bestandsnaam: alles tussen "===FILE:" en de afsluitende "==="
            filename = normalized[len(FILE_START_PREFIX):-3].strip()
            if not filename:
                raise ValueError(f"Regel {lineno}: lege bestandsnaam in ===FILE: marker")
            current_filename = filename
            current_lines = []
            state = "inside"

        elif is_end:
            if state == "outside":
                print(f"[WARN] Regel {lineno}: losse ===END_FILE=== zonder openend ===FILE: blok — overgeslagen")
                continue
            blocks.append({
                "filename": current_filename,
                "content": _strip_code_fence("\n".join(current_lines)),
                "truncated": False,
            })
            state = "outside"
            current_filename = None
            current_lines = []

        elif state == "inside":
            current_lines.append(line)

    if state == "inside":
        # EOF bereikt zonder ===END_FILE=== — waarschijnlijk max_tokens afkap
        blocks.append({
            "filename": current_filename,
            "content": _strip_code_fence("\n".join(current_lines)),
            "truncated": True,
        })

    return blocks


def _strip_code_fence(content: str) -> str:
    """
    Strip alle ```...``` wrappers die de AI soms toevoegt, ook als ze:
    - midden in het bestand staan (na een prelude-regel zoals "use client";)
    - meerdere keren voorkomen (taalblokken)
    - alleen aan begin of einde staan
    """
    lines = content.splitlines()
    # Verwijder lege regels aan begin/einde
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return content
    # Verwijder ALLE fence-regels in de eerste 10 regels (Claude zet ze soms
    # na 'use client' of een comment, niet alleen aan het begin).
    new_lines: list[str] = []
    for i, line in enumerate(lines):
        if i < 10 and line.strip().startswith("```"):
            continue
        new_lines.append(line)
    lines = new_lines
    # Verwijder ALLE trailing fence-regels
    while lines and lines[-1].strip().startswith("```"):
        lines.pop()
    return "\n".join(lines)


def _extract_code_fence_fallback(raw: str) -> list[dict]:
    """
    Fallback: als er geen ===FILE: markers zijn maar wel markdown code fences,
    probeer het bestandspad te extraheren uit de omringende tekst.

    Ondersteunde patronen:
    - "copy it to `src/app/X/page.tsx`"
    - "Here is the complete file for `src/app/X/page.tsx`"
    - "```tsx\n...code...\n```"
    """
    import re

    # Zoek bestandspad in omringende tekst (voor de code fence). Ondersteun:
    #  "copy it to `src/app/...`"
    #  "Here is the file `src/app/...`"
    #  "**`src/app/...`**"  (bold-fenced filename in markdown)
    #  "### `src/app/...`"  (heading-style)
    #  "`src/app/.../page.tsx`" (gewoon backtick voor codefence)
    path_pat = re.compile(
        r"[`'\"*]\s*(src/[^\s`'\"*]+\.(?:tsx|ts|css|json))\s*[`'\"*]",
        re.IGNORECASE,
    )
    match = path_pat.search(raw)
    if not match:
        return []

    filepath = match.group(1)

    # Extract code fence inhoud
    fence_pat = re.compile(r"```(?:tsx|ts|jsx|js|css|json|typescriptreact)?\s*\n(.*?)```", re.DOTALL)
    fence_match = fence_pat.search(raw)
    if not fence_match:
        return []

    code = fence_match.group(1).strip()
    if not code:
        return []

    print(f"[INFO] parse: code-fence fallback voor {filepath} ({len(code)} tekens)")
    return [{"filename": filepath, "content": code, "truncated": False}]


def validate_blocks(blocks: list[dict]) -> tuple[bool, list[tuple[str, str]]]:
    """
    Valideer parsed blocks op duplicaten, lege namen en lege content.

    Geeft (is_fatal_ok, issues) terug.
    Issues zijn (level, message) tuples met level "FAIL" of "WARN".
    """
    issues = []

    if not blocks:
        return False, [("FAIL", "Geen ===FILE: blokken gevonden in de input")]

    seen = {}
    for i, block in enumerate(blocks):
        filename = block["filename"]

        if filename in seen:
            issues.append((
                "FAIL",
                f"Dubbel bestandspad '{filename}' — blok {seen[filename] + 1} en blok {i + 1}"
            ))
        else:
            seen[filename] = i

        if not block["content"].strip():
            issues.append(("WARN", f"Bestand '{filename}' heeft lege inhoud"))

        if block["truncated"]:
            issues.append((
                "WARN",
                f"Bestand '{filename}' is afgekapt (geen ===END_FILE===) — "
                "waarschijnlijk max_tokens bereikt in generate_site.py"
            ))

    fatal = any(level == "FAIL" for level, _ in issues)
    return not fatal, issues


def check_conflicts(blocks: list[dict], out_dir: Path) -> list[str]:
    """Geef lijst van bestandsnamen die al bestaan in out_dir."""
    return [
        block["filename"]
        for block in blocks
        if (out_dir / block["filename"]).exists()
    ]


def write_blocks(blocks: list[dict], out_dir: Path) -> list[dict]:
    """Schrijf alle blokken weg en geef een lijst met resultaten terug."""
    results = []
    for block in blocks:
        filename = block["filename"]
        content = block["content"]
        target = out_dir / filename

        write_text(target, content)

        is_empty = not content.strip()
        if block["truncated"]:
            print(f"[WARN] Geschreven (afgekapt): {target}  ({len(content)} tekens)")
        elif is_empty:
            print(f"[WARN] Geschreven (leeg): {target}")
        else:
            print(f"[OK]   Geschreven: {target}  ({len(content)} tekens)")

        results.append({
            "filename": filename,
            "output_path": str(target),
            "char_count": len(content),
            "truncated": block["truncated"],
            "empty": is_empty,
        })
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Parse raw AI-gegenereerde site output naar echte bestanden op disk"
    )
    parser.add_argument("--input", required=True, help="Pad naar raw output .txt bestand")
    parser.add_argument("--outdir", required=True, help="Doelmap voor de weggeschreven bestanden")
    parser.add_argument("--force", action="store_true", help="Overschrijf bestaande bestanden")
    args = parser.parse_args()

    input_path = Path(args.input)
    out_dir = Path(args.outdir).resolve()

    print(f"[INFO] Input:     {input_path}")
    print(f"[INFO] Outputmap: {out_dir}")
    if args.force:
        print("[INFO] --force actief: bestaande bestanden worden overschreven")

    # Lees input
    try:
        raw = read_text(input_path)
    except FileNotFoundError as e:
        print(f"[FAIL] {e}")
        sys.exit(1)

    # Parse blokken
    try:
        blocks = parse_blocks(raw)
    except ValueError as e:
        print(f"[FAIL] Parse fout: {e}")
        sys.exit(1)

    # Fallback: als geen ===FILE: blokken, probeer code-fence extractie
    if not blocks:
        blocks = _extract_code_fence_fallback(raw)

    # Valideer blokken
    ok, issues = validate_blocks(blocks)
    for level, message in issues:
        print(f"[{level}] {message}")

    if not ok:
        print("[FAIL] Validatie mislukt — geen bestanden geschreven")
        sys.exit(1)

    print(f"[INFO] {len(blocks)} bestand(en) gevonden")

    # Controleer pad-veiligheid voor alle blokken
    for block in blocks:
        if not is_safe_path(out_dir, block["filename"]):
            print(f"[FAIL] Onveilig pad gedetecteerd: '{block['filename']}' — schrijven geweigerd")
            sys.exit(1)

    # Controleer conflicten met bestaande bestanden
    conflicts = check_conflicts(blocks, out_dir)
    if conflicts and not args.force:
        print(f"[FAIL] De volgende bestanden bestaan al in {out_dir}:")
        for name in conflicts:
            print(f"       - {name}")
        print("[FAIL] Gebruik --force om bestaande bestanden te overschrijven")
        sys.exit(1)

    if conflicts and args.force:
        print(f"[WARN] {len(conflicts)} bestand(en) worden overschreven vanwege --force")

    # Schrijf bestanden weg
    results = write_blocks(blocks, out_dir)

    # Schrijf manifest
    manifest = {
        "input_file": str(input_path),
        "out_dir": str(out_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files_written": len(results),
        "files": results,
    }
    manifest_path = out_dir / MANIFEST_FILENAME
    write_text(manifest_path, json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"[OK]   Manifest opgeslagen: {manifest_path}")

    # Eindsamenvatting
    truncated_count = sum(1 for r in results if r["truncated"])
    empty_count = sum(1 for r in results if r["empty"])

    if truncated_count:
        print(f"[WARN] {truncated_count} bestand(en) afgekapt — verhoog max_tokens in generate_site.py")
    if empty_count:
        print(f"[WARN] {empty_count} bestand(en) zijn leeg")
    if not truncated_count and not empty_count:
        print(f"[OK]   Alle {len(results)} bestand(en) volledig en correct geschreven")


if __name__ == "__main__":
    main()
