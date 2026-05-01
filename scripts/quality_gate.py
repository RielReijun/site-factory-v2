"""
quality_gate.py — Valideer een ContentInventory tegen een v1-contract.

Doel: voorkomen dat we een site bouwen wanneer evidente bron-data ontbreekt.
Voorbeeld: text.txt bevat €-bedragen maar inventory.prices is leeg → fail.

Geen LLM. Geen netwerkcalls. Alleen regex op extracted velden + bron.

Gebruik:
  python3 scripts/quality_gate.py --slug kapsalon-frank-nl
  python3 scripts/quality_gate.py --all
  python3 scripts/quality_gate.py --slug X --strict   # exit 1 bij fail
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inventory import (  # noqa: E402
    ContentInventory, build_inventory, load_inventory, write_inventory,
)


# ── Contract types ───────────────────────────────────────────────────────────
@dataclass
class FieldCheck:
    """
    rule:
      - "required"             : veld moet niet-leeg zijn
      - "recommended"          : missing → warn (severity defaults to warn)
      - "min_count"            : len(veld) >= min_count
      - "required_if_evidence" : if veld leeg + evidence_pattern matched
                                 evidence_min_matches × in source_text → fail
      - "any_of"               : minstens één van `any_of_fields` moet niet-leeg zijn
    """
    field: str
    rule: str
    severity: str = "warn"
    min_count: int = 1
    evidence_pattern: str = ""
    evidence_min_matches: int = 1
    any_of_fields: list[str] = field(default_factory=list)
    rationale: str = ""


@dataclass
class CheckResult:
    field: str
    rule: str
    severity: str
    passed: bool
    message: str
    rationale: str = ""


@dataclass
class QualityReport:
    slug: str
    contract: str
    fails: list[CheckResult] = field(default_factory=list)
    warns: list[CheckResult] = field(default_factory=list)
    passes: list[CheckResult] = field(default_factory=list)
    inventory_warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.fails) == 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _resolve(value: Any, dotted_path: str) -> Any:
    current = value
    for part in dotted_path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
    return current


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple, set, dict, str)):
        return len(value) == 0
    return False


def _length(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (list, tuple, set, dict, str)):
        return len(value)
    return 1


# ── V1 contract ──────────────────────────────────────────────────────────────
# Archetype-agnostisch: dit is wat élke MKB-site nodig heeft. Specifieke
# branche-eisen (bv. prijzen voor kapsalons) gaan via 'required_if_evidence'
# zodat we ze niet hardcoden voor elke prospect.
V1_CONTRACT: list[FieldCheck] = [
    FieldCheck(
        field="contact.phone",
        rule="any_of",
        any_of_fields=["contact.phone", "contact.email"],
        severity="fail",
        rationale="Site moet minstens één contactactie hebben",
    ),
    FieldCheck(
        field="prices",
        rule="required_if_evidence",
        evidence_pattern=r"€\s?\d",
        evidence_min_matches=4,
        severity="fail",
        rationale="text.txt bevat €-bedragen maar extractor pakte ze niet — site mist prijzen",
    ),
    FieldCheck(
        field="treatments",
        rule="min_count",
        min_count=3,
        severity="warn",
        rationale="≥3 dienst-/behandelingsnamen voor services-sectie",
    ),
    FieldCheck(
        field="opening_hours",
        rule="recommended",
        severity="warn",
        rationale="Openingstijden tonen wekt vertrouwen voor lokale bedrijven",
    ),
    FieldCheck(
        field="contact.address",
        rule="recommended",
        severity="warn",
        rationale="Adres voor MapSection en Local SEO",
    ),
    FieldCheck(
        field="images",
        rule="min_count",
        min_count=3,
        severity="warn",
        rationale="≥3 afbeeldingen voor hero/gallery/services",
    ),
    FieldCheck(
        field="signatures",
        rule="min_count",
        min_count=1,
        severity="warn",
        rationale="≥1 verbatim quote voor hero/about-copy",
    ),
    FieldCheck(
        field="pages_content",
        rule="min_count",
        min_count=1,
        severity="warn",
        rationale="≥1 subpage met body-content voor sub-pagina's",
    ),
    FieldCheck(
        field="reviews",
        rule="recommended",
        severity="warn",
        rationale="Echte reviews uit raw.html zijn beter dan geen testimonials",
    ),
    FieldCheck(
        field="source_copy.tagline",
        rule="recommended",
        severity="warn",
        rationale="og:description als hero-tagline ipv generieke AI-copy",
    ),
]


# ── Validatie ────────────────────────────────────────────────────────────────
def _evaluate(check: FieldCheck, inventory: ContentInventory, source_text: str) -> CheckResult:
    rule = check.rule

    def _result(passed: bool, message: str) -> CheckResult:
        return CheckResult(
            field=check.field,
            rule=rule,
            severity=check.severity,
            passed=passed,
            message=message,
            rationale=check.rationale,
        )

    if rule == "any_of":
        for f in check.any_of_fields:
            v = _resolve(inventory, f)
            if not _is_empty(v):
                return _result(True, f"any_of: {f} aanwezig")
        return _result(False, f"any_of {check.any_of_fields}: alle leeg")

    value = _resolve(inventory, check.field)

    if rule == "required":
        if _is_empty(value):
            return _result(False, f"{check.field} ontbreekt of is leeg")
        return _result(True, f"{check.field} aanwezig")

    if rule == "recommended":
        if _is_empty(value):
            return _result(False, f"{check.field} ontbreekt — aanbevolen")
        return _result(True, f"{check.field} aanwezig")

    if rule == "min_count":
        n = _length(value)
        if n < check.min_count:
            return _result(False, f"{check.field}: {n}/{check.min_count} (te weinig)")
        return _result(True, f"{check.field}: {n} (≥ {check.min_count})")

    if rule == "required_if_evidence":
        if not _is_empty(value):
            return _result(True, f"{check.field}: {_length(value)} aanwezig")
        if not check.evidence_pattern:
            return _result(True, f"{check.field}: leeg, geen evidence-pattern gedefinieerd")
        matches = re.findall(check.evidence_pattern, source_text)
        if len(matches) >= check.evidence_min_matches:
            return _result(
                False,
                f"{check.field}: leeg, maar bron bevat {len(matches)} matches op "
                f"'{check.evidence_pattern}' (threshold: {check.evidence_min_matches}) — "
                f"extractor heeft het gemist",
            )
        return _result(
            True,
            f"{check.field}: leeg, slechts {len(matches)} evidence-matches "
            f"(< threshold {check.evidence_min_matches})",
        )

    return _result(False, f"onbekende rule: {rule}")


def validate_inventory(
    inventory: ContentInventory,
    contract: list[FieldCheck] = V1_CONTRACT,
    *,
    source_text: str = "",
    contract_name: str = "v1-default",
) -> QualityReport:
    report = QualityReport(
        slug=inventory.slug,
        contract=contract_name,
        inventory_warnings=list(inventory.warnings),
    )
    for check in contract:
        result = _evaluate(check, inventory, source_text)
        if result.passed:
            report.passes.append(result)
        elif result.severity == "fail":
            report.fails.append(result)
        else:
            report.warns.append(result)
    return report


# ── Output ───────────────────────────────────────────────────────────────────
def print_report(report: QualityReport) -> None:
    print(f"\n══════ QUALITY GATE · {report.contract} · {report.slug} ══════")
    print(f"  passes: {len(report.passes)}    warns: {len(report.warns)}    fails: {len(report.fails)}")
    if report.fails:
        print("  FAILS")
        for r in report.fails:
            print(f"    [X] {r.field}: {r.message}")
            if r.rationale:
                print(f"          reden: {r.rationale}")
    if report.warns:
        print("  WARNS")
        for r in report.warns:
            print(f"    [!] {r.field}: {r.message}")
            if r.rationale:
                print(f"          reden: {r.rationale}")
    if report.inventory_warnings:
        print("  INVENTORY-WARNINGS (info)")
        for w in report.inventory_warnings:
            print(f"    . {w}")
    if report.ok and not report.warns:
        print("  alle checks geslaagd")
    print()


def write_report(report: QualityReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ── CLI ──────────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="Run quality gate op een prospect's inventory.")
    parser.add_argument("--slug", help="Naam van de prospect-map onder data/")
    parser.add_argument("--all", action="store_true", help="Run gate voor alle prospects")
    parser.add_argument("--data-dir", default="data", help="Pad naar data/-directory")
    parser.add_argument("--rebuild-inventory", action="store_true",
                        help="Rebuild inventory.json voordat de gate draait")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 1 bij fail (default: 0)")
    parser.add_argument("--quiet", action="store_true", help="Print alleen samenvatting per prospect")
    args = parser.parse_args()

    if not args.slug and not args.all:
        parser.error("specify --slug or --all")

    project_dir = Path(__file__).resolve().parents[1]
    data_dir = project_dir / args.data_dir if not Path(args.data_dir).is_absolute() else Path(args.data_dir)

    if args.slug:
        slugs = [args.slug]
    else:
        skip = {"trash", "trash_old", "_archive"}
        slugs = sorted(
            d.name for d in data_dir.iterdir()
            if d.is_dir() and d.name not in skip and (d / "text.txt").exists()
        )

    any_fail = False
    summary = {"ok": 0, "fail": 0, "warn_only": 0}
    for slug in slugs:
        collected = data_dir / slug
        if not collected.exists():
            print(f"[FAIL] {slug}: directory niet gevonden")
            any_fail = True
            continue

        inv_path = collected / "inventory.json"
        if args.rebuild_inventory or not inv_path.exists():
            inv = build_inventory(slug, collected)
            write_inventory(inv, inv_path)
        else:
            from dataclasses import fields
            # Lees als dict + reconstrueer alleen de top-level voor validatie.
            # We hebben geen volledig roundtrip nodig; _resolve traversed dicts ook.
            data = load_inventory(inv_path)
            inv = _ContentInventoryView(data)  # type: ignore[assignment]

        text = (collected / "text.txt").read_text(encoding="utf-8", errors="ignore") if (collected / "text.txt").exists() else ""
        report = validate_inventory(inv, source_text=text)

        report_path = collected / "quality_report.json"
        write_report(report, report_path)

        if report.fails:
            any_fail = True
            summary["fail"] += 1
            status = "FAIL"
        elif report.warns:
            summary["warn_only"] += 1
            status = "WARN"
        else:
            summary["ok"] += 1
            status = "OK  "

        if args.quiet:
            print(f"[{status}] {slug:<35} passes={len(report.passes):>2} warns={len(report.warns):>2} fails={len(report.fails):>2}")
        else:
            print_report(report)

    print(f"\n[SUMMARY] ok={summary['ok']}  warn_only={summary['warn_only']}  fail={summary['fail']}")
    return 1 if (any_fail and args.strict) else 0


class _ContentInventoryView:
    """Dunne wrapper rond een dict zodat _resolve() er door kan walken zonder
    dat we het hele dataclass-tree hoeven te reconstrueren bij --rebuild=False.
    """
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self.slug = data.get("slug", "")
        self.warnings = data.get("warnings", [])

    def __getattr__(self, name: str) -> Any:
        if name in self._data:
            return self._data[name]
        raise AttributeError(name)


if __name__ == "__main__":
    raise SystemExit(main())
