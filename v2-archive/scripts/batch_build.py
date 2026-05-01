#!/usr/bin/env python3
"""batch_build.py — draai de v2 pipeline op alle collected prospects.

Per prospect onder data/<slug>/:
  1. Detect archetype (auto)
  2. Build inventory + run quality gate
  3. Bij gate-pass: schrijf site_plan + Astro source
  4. Optioneel: --build-astro draait ook `npx astro build` (vereist node).

Resultaat per prospect wordt geaggregeerd in artifacts/v2/_index.json,
zodat het v2-dashboard alles in één lijst kan tonen zonder per prospect
een aparte aanroep.

Gebruik:
    python3 v2/scripts/batch_build.py --all
    python3 v2/scripts/batch_build.py --all --build-astro
    python3 v2/scripts/batch_build.py --slug kapsalon-frank-nl --force
    python3 v2/scripts/batch_build.py --all --skip-gate-fails

Snelheid: per prospect ~1-3s zonder astro-build, ~30s met. Voor 50 prospects
is het zonder build dus < 3 min; met astro-build ~25 min.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from v2.archetypes import detect_archetype, registered_archetypes  # noqa: E402
from v2.pipeline.inventory import build_inventory  # noqa: E402
from v2.pipeline.quality_gate import validate_inventory  # noqa: E402

DATA_DIR = PROJECT_DIR / "data"
ARTIFACTS_DIR = PROJECT_DIR / "artifacts" / "v2"
INDEX_PATH = ARTIFACTS_DIR / "_index.json"

# Slugs (data-mappen) die we overslaan: trash, niet-prospect-mappen.
_SKIP_DIRS = {"trash", "trash_old", "_archive"}


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


def _list_prospect_slugs(filter_slug: str | None = None) -> list[str]:
    if not DATA_DIR.exists():
        return []
    slugs: list[str] = []
    for entry in sorted(DATA_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in _SKIP_DIRS:
            continue
        if not (entry / "briefing.md").exists():
            continue
        if filter_slug and entry.name != filter_slug:
            continue
        slugs.append(entry.name)
    return slugs


def _astro_build(source_dir: Path) -> tuple[bool, str]:
    """Run npm install + npx astro build. Detecteert of we al in een docker
    container draaien (en dus direct shell kunnen gebruiken) of vanaf host
    via `docker run --rm` moeten."""
    if not source_dir.exists():
        return False, "astro source ontbreekt"
    in_docker = Path("/.dockerenv").exists() or shutil.which("docker") is None
    if in_docker:
        # Direct shell binnen worker — node is hier beschikbaar
        cmd = ["bash", "-lc",
               "npm install --silent 2>&1 | tail -1 && npx astro build 2>&1 | tail -3"]
        proc = subprocess.run(cmd, cwd=str(source_dir), capture_output=True, text=True, timeout=180)
    else:
        rel = source_dir.relative_to(PROJECT_DIR)
        cmd = [
            "docker", "run", "--rm",
            "-v", f"{PROJECT_DIR}:/workspace",
            "-w", f"/workspace/{rel}",
            "site-factory-worker",
            "bash", "-lc",
            "npm install --silent 2>&1 | tail -1 && npx astro build 2>&1 | tail -3",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    output = (proc.stdout + proc.stderr).strip().splitlines()
    last = output[-1] if output else ""
    return proc.returncode == 0, last


def _process(slug: str, args) -> dict:
    """Verwerk één prospect. Geeft een dict terug die in _index.json landt."""
    started = time.time()
    collected_path = DATA_DIR / slug
    artifact_dir = ARTIFACTS_DIR / slug
    source_dir = artifact_dir / "astro-source"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    briefing = _read_text(collected_path / "briefing.md")
    structured = _read_json(collected_path / "structured_data.json", {})
    meta = _read_json(collected_path / "meta.json", {})

    # Archetype-detectie
    archetype = detect_archetype(briefing, structured, meta) if briefing else None
    archetype_name = archetype.name if archetype else "generic"

    record: dict = {
        "slug": slug,
        "company_name": meta.get("company_name") or slug.replace("-", " ").title(),
        "source_url": meta.get("url", ""),
        "archetype": archetype_name,
        "build_time": datetime.now(timezone.utc).isoformat(),
        "render_status": "skipped",
        "gate": {"passes": 0, "warns": 0, "fails": 0, "ok": False},
        "theme": {},
        "signature": "",
        "site_url": "",
        "site_built": False,
        "duration_ms": 0,
        "warnings": [],
    }

    if archetype is None:
        record["render_status"] = "no_archetype"
        record["duration_ms"] = int((time.time() - started) * 1000)
        return record

    # Inventory + gate
    try:
        inventory = build_inventory(slug, collected_path)
    except Exception as exc:
        record["render_status"] = "inventory_failed"
        record["warnings"].append(f"inventory exception: {exc}")
        record["duration_ms"] = int((time.time() - started) * 1000)
        return record

    contract = archetype.get_contract()
    source_text = _read_text(collected_path / "text.txt")
    report = validate_inventory(
        inventory, contract,
        archetype_name=archetype.name,
        source_text=source_text,
    )
    record["gate"] = {
        "passes": len(report.passes),
        "warns":  len(report.warns),
        "fails":  len(report.fails),
        "ok":     report.ok,
        "fail_reasons": [f"{r.field}: {r.message}" for r in report.fails],
    }
    record["warnings"] = list(inventory.warnings)

    # Quality-report wegschrijven (zelfde plek als single-build doet)
    from v2.pipeline.quality_gate import write_report
    write_report(report, artifact_dir / "quality_report.json")

    # Schrijf inventory voor dashboard-inspectie
    (artifact_dir / "content_inventory.json").write_text(
        json.dumps(inventory.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    if not report.ok and not args.ignore_gate:
        record["render_status"] = "gate_failed"
        record["duration_ms"] = int((time.time() - started) * 1000)
        return record

    # Render
    try:
        plan = archetype.build_plan(slug, collected_path, briefing, structured, meta, inventory=inventory)
    except Exception as exc:
        record["render_status"] = "build_plan_failed"
        record["warnings"].append(f"build_plan exception: {exc}")
        record["duration_ms"] = int((time.time() - started) * 1000)
        return record

    plan_path = artifact_dir / "site_plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    record["theme"] = {
        "primary":      plan.get("theme", {}).get("primary", ""),
        "primary_dark": plan.get("theme", {}).get("primary_dark", ""),
        "accent":       plan.get("theme", {}).get("accent", ""),
        "background":   plan.get("theme", {}).get("background", ""),
    }
    record["signature"] = plan.get("hero", {}).get("body", "")[:200]

    try:
        archetype.render(plan=plan, collected_path=collected_path, out_dir=source_dir, force=True)
        record["render_status"] = "rendered"
        record["site_url"] = f"/sites/{slug}/"
    except Exception as exc:
        record["render_status"] = "render_failed"
        record["warnings"].append(f"render exception: {exc}")
        record["duration_ms"] = int((time.time() - started) * 1000)
        return record

    if args.build_astro:
        ok, last = _astro_build(source_dir)
        record["site_built"] = ok
        if not ok:
            record["warnings"].append(f"astro build failed: {last[:120]}")

    record["duration_ms"] = int((time.time() - started) * 1000)
    return record


def _print_row(record: dict) -> None:
    icon = {
        "rendered":          "OK ",
        "gate_failed":       "GTE",
        "no_archetype":      "?? ",
        "inventory_failed":  "INV",
        "build_plan_failed": "BPL",
        "render_failed":     "REN",
        "skipped":           "-- ",
    }.get(record["render_status"], "?? ")
    gate = record["gate"]
    gate_str = f"P{gate['passes']:>1}/W{gate['warns']:>1}/F{gate['fails']:>1}"
    duration = f"{record['duration_ms']:>4}ms"
    line = f"  [{icon}] {record['slug']:<32} {record['archetype']:<14} {gate_str}  {duration}"
    print(line)


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-build v2 pipeline op alle prospects.")
    parser.add_argument("--all", action="store_true", help="Verwerk alle prospects in data/")
    parser.add_argument("--slug", help="Alleen deze prospect verwerken")
    parser.add_argument("--force", action="store_true", help="Forceer rebuild zelfs als artifacts bestaan")
    parser.add_argument("--build-astro", action="store_true",
                        help="Run ook npm install + npx astro build per prospect (langzaam)")
    parser.add_argument("--ignore-gate", action="store_true",
                        help="Render zelfs als de quality gate fails geeft")
    parser.add_argument("--out", default=str(INDEX_PATH), help="Output _index.json pad")
    args = parser.parse_args()

    if not args.all and not args.slug:
        print("[FAIL] specify --all or --slug")
        return 2

    slugs = _list_prospect_slugs(args.slug if not args.all else None)
    if not slugs:
        print("[FAIL] geen prospects gevonden in data/")
        return 1

    print(f"[INFO] {len(slugs)} prospect(s) te verwerken")
    print(f"  {'STATUS':<5} {'slug':<32} {'archetype':<14} gate         duration")
    print(f"  {'-'*5} {'-'*32} {'-'*14} {'-'*12} {'-'*8}")

    results: list[dict] = []
    summary = {"rendered": 0, "gate_failed": 0, "render_failed": 0, "no_archetype": 0, "other": 0}

    for slug in slugs:
        record = _process(slug, args)
        results.append(record)
        _print_row(record)
        bucket = record["render_status"]
        if bucket in summary:
            summary[bucket] += 1
        else:
            summary["other"] += 1

    # Schrijf het index-bestand
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    index_doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prospects":    results,
        "summary":      summary,
    }
    out_path.write_text(json.dumps(index_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print()
    print(f"[OK]   index geschreven: {out_path}")
    print(f"  rendered: {summary['rendered']}    gate_failed: {summary['gate_failed']}    "
          f"render_failed: {summary['render_failed']}    no_archetype: {summary['no_archetype']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
