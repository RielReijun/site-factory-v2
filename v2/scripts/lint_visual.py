#!/usr/bin/env python3
"""lint_visual.py — non-blocking visual quality lint op een gebouwde Astro-site.

Wrapper rond de impeccable CLI (https://github.com/pbakaus/impeccable). Detecteert
generic-AI design tells (paarse gradients, bounce easing, side-tab borders) en
algemene UI-issues (kleine tap targets, skipped headings, slechte line-length).

Bewust **niet** gekoppeld aan build_from_existing_brief.py: deze loopt los, zodat
false positives de pipeline niet kunnen blokkeren. Resultaat is altijd exit 0;
findings staan in artifacts/v2/<slug>/visual_lint.json.

Gebruik:
    python3 v2/scripts/lint_visual.py --slug www-beautysaloncarlijn-nl
    python3 v2/scripts/lint_visual.py --dir artifacts/v2/<slug>/astro-source/dist
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
DOCKER_IMAGE = "site-factory-worker"
LINT_TOOLS_DIR = PROJECT_DIR / "v2" / ".lint-tools"


def _resolve_dist(slug: str | None, dir_path: str | None) -> Path:
    if dir_path:
        return Path(dir_path).resolve()
    if not slug:
        print("[FAIL] --slug of --dir verplicht")
        sys.exit(2)
    candidate = PROJECT_DIR / "artifacts" / "v2" / slug / "astro-source" / "dist"
    if not candidate.exists():
        print(f"[FAIL] dist/ niet gevonden: {candidate}")
        print("       Bouw eerst met astro build.")
        sys.exit(2)
    return candidate


def _ensure_impeccable_local() -> Path | None:
    """Installeer impeccable in v2/.lint-tools/ om herhaalde npx-downloads te
    voorkomen. Geeft pad naar bin terug, of None als er geen lokale npm is."""
    bin_path = LINT_TOOLS_DIR / "node_modules" / ".bin" / "impeccable"
    if bin_path.exists():
        return bin_path
    if not shutil.which("npm"):
        return None
    LINT_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    pkg = LINT_TOOLS_DIR / "package.json"
    if not pkg.exists():
        pkg.write_text(
            '{"private": true, "dependencies": {"impeccable": "latest"}}\n',
            encoding="utf-8",
        )
    print("[INFO] impeccable lokaal installeren in v2/.lint-tools/ ...")
    proc = subprocess.run(["npm", "install", "--silent"], cwd=str(LINT_TOOLS_DIR))
    if proc.returncode != 0 or not bin_path.exists():
        return None
    return bin_path


def _ensure_impeccable_docker() -> Path | None:
    """Installeer impeccable in v2/.lint-tools/ via de site-factory-worker
    container, voor wanneer er geen lokale node is."""
    bin_path = LINT_TOOLS_DIR / "node_modules" / ".bin" / "impeccable"
    if bin_path.exists():
        return bin_path
    LINT_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    pkg = LINT_TOOLS_DIR / "package.json"
    if not pkg.exists():
        pkg.write_text(
            '{"private": true, "dependencies": {"impeccable": "latest"}}\n',
            encoding="utf-8",
        )
    rel = LINT_TOOLS_DIR.relative_to(PROJECT_DIR)
    print("[INFO] impeccable installeren via docker worker ...")
    proc = subprocess.run([
        "docker", "run", "--rm",
        "-v", f"{PROJECT_DIR}:/workspace",
        "-w", f"/workspace/{rel}",
        DOCKER_IMAGE,
        "npm", "install", "--silent",
    ])
    if proc.returncode != 0 or not bin_path.exists():
        return None
    return bin_path


def _run_local(bin_path: Path, dist: Path, fast: bool) -> tuple[int, str]:
    cmd = [str(bin_path), "detect", "--json"]
    if fast:
        cmd.append("--fast")
    cmd.append(str(dist))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _run_docker(bin_path: Path, dist: Path, fast: bool) -> tuple[int, str]:
    rel_bin = bin_path.relative_to(PROJECT_DIR)
    rel_dist = dist.relative_to(PROJECT_DIR)
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{PROJECT_DIR}:/workspace",
        "-w", "/workspace",
        DOCKER_IMAGE,
        f"/workspace/{rel_bin}", "detect", "--json",
    ]
    if fast:
        cmd.append("--fast")
    cmd.append(f"/workspace/{rel_dist}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _parse_json(output: str) -> dict | list | None:
    """Trim eventuele banner-output en parse als JSON. Pak het *eerste*
    voorkomen van { of [ — niet altijd { eerst, want dan slik je objects
    binnenin een array op."""
    text = (output or "").strip()
    if not text:
        return None
    starts = [idx for idx in (text.find("["), text.find("{")) if idx >= 0]
    if not starts:
        return None
    candidate = text[min(starts):]
    for end in range(len(candidate), 0, -1):
        try:
            return json.loads(candidate[:end])
        except json.JSONDecodeError:
            continue
    return None


def _extract_issues(report: dict | list) -> list[dict]:
    """impeccable's JSON-shape is niet pubgedoc — probeer een paar veldnamen."""
    if isinstance(report, list):
        return [item for item in report if isinstance(item, dict)]
    if not isinstance(report, dict):
        return []
    for key in ("issues", "findings", "violations", "results", "antiPatterns"):
        value = report.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    # Soms zit alles plat onder een "files" key
    files = report.get("files")
    if isinstance(files, list):
        out: list[dict] = []
        for entry in files:
            for key in ("issues", "findings", "violations"):
                val = entry.get(key) if isinstance(entry, dict) else None
                if isinstance(val, list):
                    out.extend(item for item in val if isinstance(item, dict))
        return out
    return []


def _print_summary(report: dict | list, dist: Path) -> int:
    rel = dist
    try:
        rel = dist.relative_to(PROJECT_DIR)
    except ValueError:
        pass
    print(f"\n══════ VISUAL LINT · {rel} ══════")
    issues = _extract_issues(report)
    if not issues:
        print("  geen anti-patronen gedetecteerd")
        return 0
    by_rule: dict[str, int] = {}
    examples: dict[str, str] = {}
    for item in issues:
        rule = (
            item.get("antipattern")
            or item.get("rule")
            or item.get("name")
            or item.get("id")
            or item.get("type")
            or "unknown"
        )
        by_rule[rule] = by_rule.get(rule, 0) + 1
        if rule not in examples:
            example = item.get("file") or item.get("path") or item.get("location") or ""
            if isinstance(example, dict):
                example = example.get("file") or example.get("path") or ""
            examples[rule] = str(example)[:64]
    print(f"  {len(issues)} bevindingen")
    for rule, count in sorted(by_rule.items(), key=lambda x: -x[1]):
        ex = f"  ({examples[rule]})" if examples.get(rule) else ""
        print(f"    · {rule:<40} {count:>3}×{ex}")
    return len(issues)


def main() -> int:
    parser = argparse.ArgumentParser(description="Visual lint op een gebouwde Astro-site (impeccable wrapper).")
    parser.add_argument("--slug", help="prospect slug (zoekt artifacts/v2/<slug>/astro-source/dist)")
    parser.add_argument("--dir", help="custom dist-dir (override --slug)")
    parser.add_argument("--fast", action="store_true", default=True,
                        help="regex-only mode (default; geen puppeteer)")
    parser.add_argument("--no-fast", dest="fast", action="store_false")
    parser.add_argument("--no-docker", action="store_true",
                        help="alleen lokale npm/node toestaan")
    parser.add_argument("--out", help="schrijf rapport-JSON naar dit pad")
    args = parser.parse_args()

    dist = _resolve_dist(args.slug, args.dir)
    print(f"[INFO] lint target: {dist}")

    have_node = shutil.which("node") is not None
    bin_path: Path | None = None
    use_docker = False

    if have_node:
        bin_path = _ensure_impeccable_local()

    if bin_path is None and not args.no_docker:
        bin_path = _ensure_impeccable_docker()
        use_docker = bin_path is not None

    if bin_path is None:
        print("[WARN] geen impeccable kunnen installeren — non-blocking, exit 0")
        return 0

    # Als er geen lokale node is moeten we ook RUNNEN via docker, ook als
    # de bin al op disk staat (npm-install kan eerder gedaan zijn via docker).
    if not have_node:
        use_docker = True

    print(f"[INFO] impeccable bin: {bin_path}{' (via docker)' if use_docker else ''}")
    if use_docker:
        rc, output = _run_docker(bin_path, dist, args.fast)
    else:
        rc, output = _run_local(bin_path, dist, args.fast)

    if rc != 0:
        # rc != 0 betekent vaak "issues gevonden" — dat is geen pipeline-fout.
        # We loggen alleen als er ook geen output is.
        if not output.strip():
            print(f"[WARN] impeccable returnde exit {rc} zonder output — non-blocking")
            return 0

    report = _parse_json(output)
    if report is None:
        print("[WARN] kon impeccable output niet als JSON parsen — ruwe output (eerste 2KB):")
        print(output[:2000])
        return 0

    if args.out:
        out_path = Path(args.out)
    elif args.slug:
        out_path = PROJECT_DIR / "artifacts" / "v2" / args.slug / "visual_lint.json"
    else:
        out_path = None

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"[OK]   rapport opgeslagen: {out_path}")

    issue_count = _print_summary(report, dist)
    print(f"[INFO] non-blocking — exit 0 ({issue_count} bevindingen)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
