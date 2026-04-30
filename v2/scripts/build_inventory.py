#!/usr/bin/env python3
"""build_inventory.py — bouw een content_inventory.json voor één prospect.

Doel: laten zien wat we *zeker weten* uit de v1 collected data, met
provenance per veld. Geen archetype, geen render. Proof-of-concept om te
toetsen of de raw bron rijk genoeg is voor een latere quality gate.

Gebruik:
    python3 v2/scripts/build_inventory.py --slug www-beautysaloncarlijn-nl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from v2.pipeline.inventory import ContentInventory, build_inventory


def _print_summary(inventory: ContentInventory) -> None:
    summary = inventory.summary()
    print(f"\n{'═' * 64}")
    print(f"  CONTENT INVENTORY · {inventory.company_name} ({inventory.slug})")
    print(f"{'═' * 64}")
    print(f"  bronnen geïnspecteerd : {', '.join(inventory.sources_inspected)}")
    print(f"  prijzen               : {summary['prices']:>3}")
    print(f"  behandelingen         : {summary['treatments']:>3}")
    print(f"  merken                : {summary['brands']:>3}")
    print(f"  openingstijden        : {summary['opening_hours']:>3}")
    print(f"  reviews               : {summary['reviews']:>3}")
    print(f"  pagina's              : {summary['pages']:>3}")
    print(f"  afbeeldingen          : {summary['images']:>3}")
    print(f"{'─' * 64}")

    if inventory.contact.phone or inventory.contact.email or inventory.contact.address:
        print("  CONTACT")
        if inventory.contact.phone:
            print(f"    telefoon : {inventory.contact.phone_display or inventory.contact.phone}")
        if inventory.contact.email:
            print(f"    email    : {inventory.contact.email}")
        if inventory.contact.address:
            print(f"    adres    : {inventory.contact.address}")
        if inventory.contact.booking_methods:
            print(f"    booking  : {', '.join(inventory.contact.booking_methods)}")
        print(f"{'─' * 64}")

    if inventory.prices:
        print(f"  PRIJZEN ({len(inventory.prices)}, eerste 8)")
        by_category: dict[str, list] = {}
        for item in inventory.prices:
            by_category.setdefault(item.category, []).append(item)
        shown = 0
        for category, items in by_category.items():
            print(f"    [{category}]")
            for item in items[:3]:
                duration = f"  ({item.duration_min} min)" if item.duration_min else ""
                print(f"      · {item.label:<40} {item.amount}{duration}")
                shown += 1
                if shown >= 8:
                    break
            if shown >= 8:
                break
        print(f"{'─' * 64}")

    if inventory.brands:
        print(f"  MERKEN")
        for brand in inventory.brands:
            print(f"    · {brand.name:<24} ({brand.occurrences}× genoemd)")
        print(f"{'─' * 64}")

    if inventory.treatments:
        print(f"  BEHANDELINGEN ({len(inventory.treatments)}, eerste 8)")
        for treatment in inventory.treatments[:8]:
            print(f"    · {treatment.name}")
        print(f"{'─' * 64}")

    if inventory.warnings:
        print(f"  WARNINGS ({len(inventory.warnings)})")
        for w in inventory.warnings:
            print(f"    ! {w}")
        print(f"{'─' * 64}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Bouw content_inventory.json uit v1 collected data.")
    parser.add_argument("--slug", required=True, help="Bestaande data-slug, bv. www-beautysaloncarlijn-nl")
    parser.add_argument("--out", default="", help="Output-pad (default: artifacts/v2/<slug>/content_inventory.json)")
    args = parser.parse_args()

    collected_path = PROJECT_DIR / "data" / args.slug
    if not collected_path.exists():
        print(f"[FAIL] Collected dir niet gevonden: {collected_path}")
        return 1

    inventory = build_inventory(args.slug, collected_path)

    out_path = Path(args.out) if args.out else PROJECT_DIR / "artifacts" / "v2" / args.slug / "content_inventory.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(inventory.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    _print_summary(inventory)
    print(f"[OK]   inventory geschreven: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
