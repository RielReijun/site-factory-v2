#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from v2.archetypes import detect_archetype, registered_archetypes
from v2.generators.astro.build_astro_site import build_astro_source, maybe_build
from v2.pipeline.context import build_site_plan
from v2.pipeline.inventory import build_inventory
from v2.pipeline.quality_gate import (
    QualityReport,
    print_report,
    validate_inventory,
    write_report,
)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return default


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an Astro v2 prototype from existing v1 collected data.",
    )
    parser.add_argument("--slug", required=True, help="Existing data slug, e.g. www-beautysaloncarlijn-nl")
    parser.add_argument("--force", action="store_true", help="Overwrite existing Astro source")
    parser.add_argument("--build", action="store_true", help="Run npm build if node_modules exists")
    parser.add_argument(
        "--archetype",
        default="auto",
        help="Archetype name, 'auto' (detect), or 'generic' (skip archetype, use legacy generator)",
    )
    parser.add_argument(
        "--ignore-quality-gate",
        action="store_true",
        help="Bouw zelfs als de archetype-quality-gate fails geeft (warns blokkeren nooit)",
    )
    parser.add_argument(
        "--gate-only",
        action="store_true",
        help="Alleen inventory + quality gate draaien, geen render",
    )
    args = parser.parse_args()

    collected_path = PROJECT_DIR / "data" / args.slug
    briefing_path = collected_path / "briefing.md"
    if not briefing_path.exists():
        print(f"[FAIL] Briefing niet gevonden: {briefing_path}")
        return 1

    artifact_dir = PROJECT_DIR / "artifacts" / "v2" / args.slug
    source_dir = artifact_dir / "astro-source"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    briefing = briefing_path.read_text(encoding="utf-8", errors="ignore")
    structured = _read_json(collected_path / "structured_data.json", {})
    meta = _read_json(collected_path / "meta.json", {})

    archetype = _resolve_archetype(args.archetype, briefing, structured, meta)

    # ── Quality gate (alleen voor archetypes; generic slaat hem over) ────────
    if archetype is not None:
        print(f"[INFO] Archetype geselecteerd: {archetype.name}")
        inventory = build_inventory(args.slug, collected_path)
        inventory_path = artifact_dir / "content_inventory.json"
        inventory_path.write_text(
            json.dumps(inventory.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"[OK]   inventory geschreven: {inventory_path}")

        # Evidence-check moet alleen op de gescrapete bron (text.txt) draaien.
        # De briefing is onze eigen editorial laag — daar staat bv. "klantreviews
        # worden later aangeleverd", dat is geen bewijs dat reviews bestaan.
        source_text = _read_text(collected_path / "text.txt")
        report = validate_inventory(
            inventory,
            archetype.get_contract(),
            archetype_name=archetype.name,
            source_text=source_text,
        )
        print_report(report)
        report_path = artifact_dir / "quality_report.json"
        write_report(report, report_path)
        print(f"[OK]   quality report: {report_path}")

        if not report.ok and not args.ignore_quality_gate:
            print("[FAIL] Quality gate heeft fail-niveau issues. "
                  "Gebruik --ignore-quality-gate om toch te bouwen.")
            return 1

        if args.gate_only:
            print("[INFO] --gate-only actief, render overgeslagen.")
            return 0

    if args.gate_only:
        print("[INFO] --gate-only actief maar geen archetype gekozen — niets te valideren.")
        return 0

    # ── Render ────────────────────────────────────────────────────────────────
    if archetype is not None:
        # Inventory is hier al gebouwd door de gate-stap; geef hem mee zodat
        # build_plan hem niet opnieuw hoeft te bouwen.
        plan = archetype.build_plan(
            args.slug, collected_path, briefing, structured, meta,
            inventory=inventory,
        )
        plan_path = artifact_dir / "site_plan.json"
        plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[OK]   Site plan geschreven: {plan_path}")
        archetype.render(plan=plan, collected_path=collected_path, out_dir=source_dir, force=args.force)
        print(f"[OK]   Astro source geschreven: {source_dir}")
    else:
        print("[INFO] Geen archetype-match. Gebruik generieke generator.")
        plan = build_site_plan(collected_path, args.slug)
        plan_path = artifact_dir / "site_plan.json"
        plan_path.write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[OK]   Site plan geschreven: {plan_path}")
        build_astro_source(plan, source_dir, force=args.force)
        print(f"[OK]   Astro source geschreven: {source_dir}")

    if args.build:
        ok = maybe_build(source_dir)
        return 0 if ok else 1

    print("[INFO] Build overgeslagen. Run in astro-source: npm install && npm run build")
    return 0


def _resolve_archetype(name: str, briefing: str, structured: dict, meta: dict):
    if name == "generic":
        return None
    if name == "auto":
        match = detect_archetype(briefing, structured, meta)
        if match is None:
            return None
        return match
    for archetype in registered_archetypes():
        if archetype.name == name:
            return archetype
    print(f"[WARN] Onbekende archetype-naam: {name}, val terug op auto-detect")
    return detect_archetype(briefing, structured, meta)


if __name__ == "__main__":
    raise SystemExit(main())
