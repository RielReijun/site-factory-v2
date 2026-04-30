"""Quality gate: valideer een ContentInventory tegen een archetype-contract.

Doel: voorkomen dat een site-render doorgaat als de bron evidente data heeft
maar de inventory die niet teruggeeft. Voorbeeld: text.txt bevat €-bedragen
maar inventory.prices is leeg → fail, want renderen zonder prijzen geeft een
slechtere site dan de oude.

Geen LLM. Geen netwerkcalls. Alleen regex op extracted velden + bron.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .inventory import ContentInventory


# ── Contract types ───────────────────────────────────────────────────────────
@dataclass
class FieldCheck:
    """Eén regel in een archetype-contract.

    rule:
      - "required"             : veld moet niet-leeg zijn
      - "recommended"          : missing → warn
      - "min_count"            : len(veld) >= min_count
      - "required_if_evidence" : if veld leeg + evidence_pattern matcht in
                                 source-tekst → fail; anders pass
    """
    field: str                          # dotted path: "contact.phone", "prices", "images"
    rule: str
    severity: str = "warn"              # "fail" | "warn"
    min_count: int = 1
    evidence_pattern: str = ""
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
    archetype: str
    slug: str
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
    """Volg een dotted path door dataclasses/dicts. None bij ontbreken."""
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


# ── Validatie ────────────────────────────────────────────────────────────────
def _evaluate(check: FieldCheck, inventory: ContentInventory, source_text: str) -> CheckResult:
    value = _resolve(inventory, check.field)
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

    if rule == "required":
        if _is_empty(value):
            return _result(False, f"{check.field} ontbreekt of is leeg")
        return _result(True, f"{check.field} aanwezig")

    if rule == "recommended":
        if _is_empty(value):
            return _result(False, f"{check.field} ontbreekt — aanbevolen voor dit archetype")
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
        if re.search(check.evidence_pattern, source_text):
            return _result(
                False,
                f"{check.field}: leeg, maar bron bevat patroon "
                f"'{check.evidence_pattern}' — extractor heeft het gemist",
            )
        return _result(True, f"{check.field}: leeg, bron had ook geen evidence")

    return _result(False, f"onbekende rule: {rule}")


def validate_inventory(
    inventory: ContentInventory,
    contract: list[FieldCheck],
    *,
    archetype_name: str,
    source_text: str = "",
) -> QualityReport:
    """Run alle checks. Verzamel inventory.warnings ook in de report-context."""
    report = QualityReport(
        archetype=archetype_name,
        slug=inventory.slug,
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


# ── Pretty print ─────────────────────────────────────────────────────────────
def print_report(report: QualityReport) -> None:
    print(f"\n══════ QUALITY GATE · {report.archetype} · {report.slug} ══════")
    print(f"  passes: {len(report.passes)}    warns: {len(report.warns)}    fails: {len(report.fails)}")
    if report.fails:
        print("  FAILS")
        for r in report.fails:
            print(f"    ✗ [{r.field}] {r.message}")
            if r.rationale:
                print(f"        reden: {r.rationale}")
    if report.warns:
        print("  WARNS")
        for r in report.warns:
            print(f"    ! [{r.field}] {r.message}")
            if r.rationale:
                print(f"        reden: {r.rationale}")
    if report.inventory_warnings:
        print("  INVENTORY-WARNINGS (info)")
        for w in report.inventory_warnings:
            print(f"    · {w}")
    if report.ok and not report.warns:
        print("  alle checks geslaagd")
    print()


def write_report(report: QualityReport, path: Path) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
