#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from v2.generators.astro.build_astro_site import build_astro_source, maybe_build
from v2.pipeline.context import build_site_plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an Astro v2 prototype from existing v1 collected data.")
    parser.add_argument("--slug", required=True, help="Existing data slug, e.g. kapsalon-frank-nl")
    parser.add_argument("--force", action="store_true", help="Overwrite existing Astro source")
    parser.add_argument("--build", action="store_true", help="Run npm build if node_modules exists")
    args = parser.parse_args()

    collected_path = PROJECT_DIR / "data" / args.slug
    briefing = collected_path / "briefing.md"
    if not briefing.exists():
        print(f"[FAIL] Briefing niet gevonden: {briefing}")
        return 1

    artifact_dir = PROJECT_DIR / "artifacts" / "v2" / args.slug
    source_dir = artifact_dir / "astro-source"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    plan = build_site_plan(collected_path, args.slug)
    plan_path = artifact_dir / "site_plan.json"
    plan_path.write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK]  Site plan geschreven: {plan_path}")

    build_astro_source(plan, source_dir, force=args.force)
    print(f"[OK]  Astro source geschreven: {source_dir}")

    if args.build:
        ok = maybe_build(source_dir)
        return 0 if ok else 1

    print("[INFO] Build overgeslagen. Run in astro-source: npm install && npm run build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
