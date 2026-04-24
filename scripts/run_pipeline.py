"""
run_pipeline.py — Orchestrates the full site-factory pipeline for a single prospect.

Steps: collect → research → brief → validate_brief → [repair_brief] → discover_pages
       → generate (home_html + styles + scripts + N×page) → validate_site
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from pipeline_utils import slugify as _slugify  # noqa – run_pipeline heeft eigen slugify die name-based is
from config import MAX_WORKERS

_log_lock     = threading.Lock()
_step_timings: list[dict] = []
_pipeline_start: float    = 0.0


PROSPECTS_FILE = Path("/workspace/data/prospects.json")
OUTPUT_DIR     = Path("/workspace/output")
SCRIPTS_DIR    = Path("/workspace/scripts")
LOG_FILE       = Path("/workspace/data/pipeline.log")
STATUS_FILE    = Path("/workspace/data/pipeline_status.json")

# Vaste units die altijd gegenereerd worden (home_html altijd eerst voor CSS-ref)
BASE_UNITS = [
    ("home_html", "home-html", {}, None),  # (unit_key, file_stem, extra_kwargs, depends)
    ("styles",    "styles",    {}, "home_html"),
    ("scripts",   "scripts",   {}, None),
]

STEPS = ["collect", "research", "brief", "generate", "validate"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_duration(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"


def save_timings(collected_path: Path, company_name: str) -> None:
    total = time.monotonic() - _pipeline_start
    data = {
        "prospect":        company_name,
        "finished_at":     datetime.now(timezone.utc).isoformat(),
        "total_duration_s": round(total, 1),
        "steps":           _step_timings,
    }
    try:
        (collected_path / "timings.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log(f"[OK]  Tijdsduur opgeslagen ({_fmt_duration(total)} totaal)")
    except Exception as e:
        log(f"[WARN] Kon timings.json niet opslaan: {e}")


# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    with _log_lock:
        print(msg, flush=True)
        try:
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass


def write_status(running: bool, prospect: str = "", step: str = "",
                 step_n: int = 0, total: int = 0, result: str = "") -> None:
    data = {
        "running":    running,
        "prospect":   prospect,
        "step":       step,
        "step_n":     step_n,
        "total":      total,
        "result":     result,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        payload = json.dumps(data, ensure_ascii=False)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(STATUS_FILE.parent), prefix=".status_tmp_", suffix=".json"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tf:
                tf.write(payload)
            os.replace(tmp_path, str(STATUS_FILE))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception:
        pass


# ── Prospect helpers ──────────────────────────────────────────────────────────

from prospects_utils import load_prospects, update_prospect  # noqa: E402


def find_prospect(prospects: list, name: str) -> tuple[int, dict]:
    for i, p in enumerate(prospects):
        if p.get("name", "").strip().lower() == name.strip().lower():
            return i, p
    raise RuntimeError(f"Prospect '{name}' niet gevonden")


def find_next_prospect(prospects: list) -> str | None:
    for p in prospects:
        if p.get("status") == "failed":
            continue
        if p.get("site_status") == "done":
            continue
        if p.get("status") == "trashed":
            continue
        return p["name"]
    return None


def mark_site_done(name: str) -> None:
    update_prospect(name, site_status="done")


def slugify(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    return name.strip("-")


# ── Subprocess runners ────────────────────────────────────────────────────────

def run_cmd(cmd: list[str], step_label: str, step_n: int = 0,
            total: int = 0, prospect: str = "") -> bool:
    log(f"\n{'─' * 60}")
    log(f"[STAP {step_n}/{total}] {step_label}")
    log(f"{'─' * 60}")
    write_status(running=True, prospect=prospect, step=step_label,
                 step_n=step_n, total=total)

    t0 = time.monotonic()
    process = subprocess.Popen(
        cmd, cwd=str(SCRIPTS_DIR),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    for line in iter(process.stdout.readline, ""):
        log(line.rstrip("\n"))
    process.wait()

    duration = time.monotonic() - t0
    ok = process.returncode == 0
    _step_timings.append({"step": step_label, "duration_s": round(duration, 1), "ok": ok})

    if not ok:
        log(f"[FAIL] '{step_label}' mislukt (exit {process.returncode})")
    log(f"[INFO] Duur: {_fmt_duration(duration)}")
    return ok


def run_cmd_json(cmd: list[str]) -> dict | None:
    result = subprocess.run(cmd, cwd=str(SCRIPTS_DIR), capture_output=True, text=True)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        log(f"[FAIL] Kon JSON niet parsen:\n{result.stdout[:500]}")
        return None


# ── Pipeline stappen ──────────────────────────────────────────────────────────

def step_collect(name: str, force: bool, n: int, total: int, prospect: str) -> bool:
    cmd = ["python", str(SCRIPTS_DIR / "collect.py"), "--name", name]
    if force:
        cmd.append("--force")
    return run_cmd(cmd, "collect", n, total, prospect)


def step_research(name: str, force: bool, n: int, total: int, prospect: str) -> bool:
    cmd = ["python", str(SCRIPTS_DIR / "research.py"), "--name", name]
    if force:
        cmd.append("--force")
    return run_cmd(cmd, "research", n, total, prospect)


def step_brief(name: str, force: bool, n: int, total: int, prospect: str) -> bool:
    cmd = ["python", str(SCRIPTS_DIR / "brief.py"), "--name", name]
    if force:
        cmd.append("--force")
    return run_cmd(cmd, "brief", n, total, prospect)


def step_validate_brief(briefing_path: Path) -> bool:
    cmd = ["python", str(SCRIPTS_DIR / "validate_brief.py"), "--file", str(briefing_path), "--json"]
    data = run_cmd_json(cmd)
    if data is None:
        return False
    passes = bool(data.get("passes"))
    if passes:
        log("[OK]  Briefing is geldig")
    else:
        log("[WARN] Briefing heeft problemen:")
        for h in data.get("missing_headings", []):
            log(f"       - Ontbrekende heading: {h}")
        for r in data.get("hard_risks", []):
            log(f"       - Hard risk: {r['match']}")
    return passes


def step_repair_brief(briefing_path: Path, n: int, total: int, prospect: str) -> bool:
    cmd = ["python", str(SCRIPTS_DIR / "repair_brief.py"), "--file", str(briefing_path), "--in-place"]
    return run_cmd(cmd, "repair_brief", n, total, prospect)


def step_discover_pages(briefing_path: Path, company_name: str, out_path: Path,
                        n: int, total: int, prospect: str) -> list[dict] | None:
    cmd = [
        "python", str(SCRIPTS_DIR / "discover_pages.py"),
        "--brief",   str(briefing_path),
        "--company", company_name,
        "--out",     str(out_path),
    ]
    if not run_cmd(cmd, "discover_pages", n, total, prospect):
        return None
    try:
        data = json.loads(out_path.read_text(encoding="utf-8"))
        return data.get("pages", [])
    except Exception as e:
        log(f"[FAIL] Kon pages.json niet lezen: {e}")
        return None


def step_scaffold_nextjs(project_dir: Path, n: int, total: int, prospect: str) -> bool:
    """Maak een nieuwe Next.js app aan als die nog niet bestaat."""
    if (project_dir / "package.json").exists():
        log(f"[INFO] scaffold: overgeslagen ({project_dir.name} bestaat al)")
        return True
    project_dir.parent.mkdir(parents=True, exist_ok=True)

    log(f"\n{'─' * 60}")
    log(f"[STAP {n}/{total}] scaffold_nextjs")
    log(f"{'─' * 60}")
    write_status(running=True, prospect=prospect, step="scaffold_nextjs", step_n=n, total=total)

    t0 = time.monotonic()
    cmd = [
        "npx", "--yes", "create-next-app@latest", project_dir.name,
        "--typescript", "--tailwind", "--app", "--no-git",
        "--src-dir", "--import-alias", "@/*",
        "--no-eslint",
    ]
    process = subprocess.Popen(
        cmd, cwd=str(project_dir.parent),  # aanmaken in OUTPUT_DIR
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    for line in iter(process.stdout.readline, ""):
        log(line.rstrip("\n"))
    process.wait()
    duration = time.monotonic() - t0
    ok = process.returncode == 0
    _step_timings.append({"step": "scaffold_nextjs", "duration_s": round(duration, 1), "ok": ok})
    if not ok:
        log(f"[FAIL] scaffold_nextjs mislukt (exit {process.returncode})")
    log(f"[INFO] Duur: {_fmt_duration(duration)}")
    if not ok:
        return False

    # next.config.ts: static export
    config = (
        'import type { NextConfig } from "next";\n\n'
        'const nextConfig: NextConfig = {\n'
        '  output: "export",\n'
        '  trailingSlash: true,\n'
        '  images: { unoptimized: true },\n'
        '};\n\n'
        'export default nextConfig;\n'
    )
    (project_dir / "next.config.ts").write_text(config, encoding="utf-8")
    log("[OK]  next.config.ts geschreven (output: export)")

    def _run_in_project(cmd: list[str], label: str) -> bool:
        log(f"[INFO] {label}...")
        proc = subprocess.run(
            cmd, cwd=str(project_dir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        for line in proc.stdout.splitlines():
            log(line)
        if proc.returncode != 0:
            log(f"[WARN] {label} mislukt (exit {proc.returncode}) — pipeline gaat door")
        return proc.returncode == 0

    # Alle npm packages in één keer installeren (sneller, geen timing-issues)
    _run_in_project(
        ["npm", "install",
         "lucide-react",
         "@tailwindcss/typography",
         "@tailwindcss/forms"],
        "npm packages installeren (lucide + tailwind plugins)",
    )

    # shadcn/ui — gebruik canary voor Tailwind v4 ondersteuning (Next.js 16)
    shadcn_ok = _run_in_project(
        ["npx", "--yes", "shadcn@canary", "init", "-y", "--base-color", "slate"],
        "shadcn/ui initialiseren (canary voor Tailwind v4)",
    )
    if shadcn_ok:
        _run_in_project(
            ["npx", "--yes", "shadcn@canary", "add", "-y",
             "accordion", "button", "card", "sheet", "badge", "separator"],
            "shadcn componenten toevoegen",
        )
    else:
        # Fallback: maak de ui-map aan met een simpele Button als fallback
        ui_dir = project_dir / "src" / "components" / "ui"
        ui_dir.mkdir(parents=True, exist_ok=True)
        (ui_dir / "button.tsx").write_text(
            '"use client";\n'
            'import { cn } from "@/lib/utils";\n'
            'export function Button({ className, children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { className?: string }) {\n'
            '  return <button className={cn("inline-flex items-center justify-center rounded-md px-4 py-2 text-sm font-medium transition-colors", className)} {...props}>{children}</button>;\n'
            '}\n',
            encoding="utf-8",
        )
        # lib/utils.ts voor cn()
        lib_dir = project_dir / "src" / "lib"
        lib_dir.mkdir(parents=True, exist_ok=True)
        (lib_dir / "utils.ts").write_text(
            'import { type ClassValue, clsx } from "clsx";\nimport { twMerge } from "tailwind-merge";\n'
            'export function cn(...inputs: ClassValue[]) { return twMerge(clsx(inputs)); }\n',
            encoding="utf-8",
        )
        _run_in_project(["npm", "install", "clsx", "tailwind-merge"], "clsx + tailwind-merge (cn util)")
        log("[INFO] shadcn fallback: minimale ui/button.tsx aangemaakt")

    log("[OK]  scaffold compleet: Next.js + Tailwind + Lucide + shadcn/ui")
    return True


def step_build_nextjs(project_dir: Path, n: int, total: int, prospect: str) -> bool:
    """Run npm run build in de Next.js projectdirectory."""
    cmd = ["npm", "run", "build"]
    # run_cmd draait vanuit SCRIPTS_DIR — we willen project_dir
    log(f"\n{'─' * 60}")
    log(f"[STAP {n}/{total}] build_nextjs")
    log(f"{'─' * 60}")
    write_status(running=True, prospect=prospect, step="build_nextjs", step_n=n, total=total)
    t0 = time.monotonic()
    process = subprocess.Popen(
        cmd, cwd=str(project_dir),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    for line in iter(process.stdout.readline, ""):
        log(line.rstrip("\n"))
    process.wait()
    duration = time.monotonic() - t0
    ok = process.returncode == 0
    _step_timings.append({"step": "build_nextjs", "duration_s": round(duration, 1), "ok": ok})
    if not ok:
        log(f"[FAIL] build_nextjs mislukt (exit {process.returncode})")
    log(f"[INFO] Duur: {_fmt_duration(duration)}")
    return ok


def step_generate_unit(briefing_path: Path, company_name: str, unit_key: str,
                        out_path: Path, ref_tsx: Path | None,
                        n: int, total: int,
                        page_slug: str = "", page_title: str = "", page_desc: str = "",
                        image_manifest: Path | None = None,
                        nav_pages: list[str] | None = None) -> bool:
    cmd = [
        "python", str(SCRIPTS_DIR / "generate_site.py"),
        "--brief",   str(briefing_path),
        "--company", company_name,
        "--unit",    unit_key,
        "--out",     str(out_path),
    ]
    if ref_tsx and ref_tsx.exists():
        cmd += ["--ref-tsx", str(ref_tsx)]
    if page_slug:
        cmd += ["--page-slug",  page_slug]
    if page_title:
        cmd += ["--page-title", page_title]
    if page_desc:
        cmd += ["--page-desc",  page_desc]
    if image_manifest and image_manifest.exists():
        cmd += ["--image-manifest", str(image_manifest)]
    if nav_pages:
        cmd += ["--nav-pages", json.dumps(nav_pages)]

    label = f"generate:{page_slug or unit_key}"
    return run_cmd(cmd, label, n, total, company_name)


def step_parse_unit(input_path: Path, out_dir: Path, n: int, total: int, prospect: str) -> bool:
    cmd = [
        "python", str(SCRIPTS_DIR / "parse_generated_site.py"),
        "--input",  str(input_path),
        "--outdir", str(out_dir),
        "--force",
    ]
    return run_cmd(cmd, f"parse:{input_path.stem}", n, total, prospect)


def _run_unit_buffered(
    briefing_path: Path, company_name: str, unit_key: str,
    out_path: Path, ref_tsx: Path | None,
    page_slug: str, page_title: str, page_desc: str,
    image_manifest: Path | None, nav_pages: list[str] | None,
    project_dir: Path,
) -> tuple[bool, str, list[str]]:
    """Genereer + parse één unit in een thread. Returnt (ok, label, lines)."""
    label = page_slug or unit_key
    lines = [f"\n{'─' * 60}", f"[UNIT] {label}", f"{'─' * 60}"]

    gen_cmd = [
        "python", str(SCRIPTS_DIR / "generate_site.py"),
        "--brief",   str(briefing_path),
        "--company", company_name,
        "--unit",    unit_key,
        "--out",     str(out_path),
    ]
    if ref_tsx and ref_tsx.exists():
        gen_cmd += ["--ref-tsx", str(ref_tsx)]
    if page_slug:
        gen_cmd += ["--page-slug", page_slug, "--page-title", page_title, "--page-desc", page_desc]
    if image_manifest and image_manifest.exists():
        gen_cmd += ["--image-manifest", str(image_manifest)]
    if nav_pages:
        gen_cmd += ["--nav-pages", json.dumps(nav_pages)]

    MAX_RETRIES = 2
    for attempt in range(1, MAX_RETRIES + 2):
        proc = subprocess.run(gen_cmd, cwd=str(SCRIPTS_DIR), capture_output=True, text=True)
        lines.extend(proc.stdout.splitlines())
        if proc.stderr.strip():
            lines.append(f"[STDERR] {proc.stderr.strip()[:500]}")
        if proc.returncode == 0:
            break
        if attempt <= MAX_RETRIES:
            lines.append(f"[WARN] generate:{label} mislukt (poging {attempt}) — herprobeert na 10s")
            time.sleep(10)
        else:
            lines.append(f"[FAIL] generate:{label} mislukt na {MAX_RETRIES + 1} pogingen")
            return False, label, lines

    parse_cmd = [
        "python", str(SCRIPTS_DIR / "parse_generated_site.py"),
        "--input",  str(out_path),
        "--outdir", str(project_dir),
        "--force",
    ]
    proc2 = subprocess.run(parse_cmd, cwd=str(SCRIPTS_DIR), capture_output=True, text=True)
    lines.extend(proc2.stdout.splitlines())
    if proc2.stderr.strip():
        lines.append(f"[STDERR] {proc2.stderr.strip()[:500]}")
    if proc2.returncode != 0:
        lines.append(f"[FAIL] parse:{out_path.stem} mislukt")
        return False, label, lines

    lines.append(f"[OK]  {label}: klaar")
    return True, label, lines


def step_validate_site(site_dir: Path, json_out: Path, n: int, total: int, prospect: str) -> bool:
    cmd = [
        "python", str(SCRIPTS_DIR / "validate_generated_site.py"),
        "--site-dir", str(site_dir),
        "--json-out", str(json_out),
    ]
    return run_cmd(cmd, "validate_site", n, total, prospect)


def step_repair_site(site_dir: Path, json_out: Path, n: int, total: int, prospect: str,
                     screenshot_json: Path | None = None) -> bool:
    cmd = [
        "python", str(SCRIPTS_DIR / "repair_generated_site.py"),
        "--site-dir", str(site_dir),
    ]
    if json_out.exists():
        cmd += ["--validation-json", str(json_out)]
    if screenshot_json and screenshot_json.exists():
        cmd += ["--screenshot-json", str(screenshot_json)]
    return run_cmd(cmd, "repair_site", n, total, prospect)


def step_check_content(site_dir: Path, collected_path: Path, company_name: str,
                       n: int, total: int, prospect: str) -> bool:
    cmd = [
        "python", str(SCRIPTS_DIR / "validate_generated_content.py"),
        "--site-dir",       str(site_dir),
        "--collected-path", str(collected_path),
        "--company",        company_name,
    ]
    return run_cmd(cmd, "content_check", n, total, prospect)


def step_screenshot_validate(site_dir: Path, n: int, total: int, prospect: str) -> bool:
    cmd = [
        "python", str(SCRIPTS_DIR / "screenshot_validate.py"),
        "--site-dir", str(site_dir),
    ]
    return run_cmd(cmd, "screenshot_validate", n, total, prospect)


def step_cohesion_pass(site_dir: Path, n: int, total: int, prospect: str) -> bool:
    cmd = [
        "python", str(SCRIPTS_DIR / "cohesion_pass.py"),
        "--site-dir", str(site_dir),
    ]
    return run_cmd(cmd, "cohesion_pass", n, total, prospect)


def step_polish_site(site_dir: Path, company_name: str, n: int, total: int, prospect: str) -> bool:
    cmd = [
        "python", str(SCRIPTS_DIR / "polish_site.py"),
        "--site-dir", str(site_dir),
        "--company",  company_name,
    ]
    return run_cmd(cmd, "polish_site", n, total, prospect)


def step_generate_mail(name: str, n: int, total: int, prospect: str) -> bool:
    cmd = [
        "python", str(SCRIPTS_DIR / "generate_mail.py"),
        "--name", name,
    ]
    return run_cmd(cmd, "generate_mail", n, total, prospect)


# ── Hulpfunctie: bouw de volledige unit-lijst op ──────────────────────────────

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}


def save_image_manifest(collected_path: Path) -> Path:
    """Sla images.json op in collected_path met paden van alle gedownloade afbeeldingen."""
    assets_dir = collected_path / "assets"
    images = []
    if assets_dir.exists():
        for f in sorted(assets_dir.rglob("*")):
            if f.is_file() and f.suffix.lower() in IMAGE_EXTS:
                try:
                    if f.stat().st_size >= 2000:
                        images.append(str(f.relative_to(collected_path)))
                except OSError:
                    pass
    images = images[:40]
    manifest = collected_path / "images.json"
    manifest.write_text(json.dumps(images, ensure_ascii=False), encoding="utf-8")
    log(f"[INFO] {len(images)} afbeeldingen gevonden voor generatie")
    return manifest


def copy_images_to_site(collected_path: Path, site_dir: Path) -> None:
    """Kopieer gedownloade afbeeldingen naar de gegenereerde site."""
    assets_dir = collected_path / "assets"
    if not assets_dir.exists():
        return
    count = 0
    for src in assets_dir.rglob("*"):
        if src.is_file() and src.suffix.lower() in IMAGE_EXTS:
            rel  = src.relative_to(collected_path)
            dest = site_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dest))
            count += 1
    if count:
        log(f"[OK]  {count} afbeeldingen gekopieerd naar site")


def build_units(pages: list[dict]) -> list[tuple]:
    """
    Geeft een lijst van tuples:
      (unit_key, file_stem, page_slug, page_title, page_desc)

    Next.js volgorde: layout (header/footer/globals) → home → subpagina's
    """
    units = [
        ("layout", "layout", "", "", ""),
        ("home",   "home",   "", "", ""),
    ]
    for page in pages:
        # page["file"] = "over-ons.html" → slug = "over-ons"
        slug = page["file"].replace(".html", "").replace("/", "-")
        if slug in ("index", "home"):
            continue  # homepage is de home unit
        units.append(("page", slug, slug, page.get("title", ""), page.get("description", "")))

    return units


def _extract_header_footer(html: str) -> str:
    """Extraheer <header> en <footer> uit homepage HTML als referentie voor subpagina's."""
    header_match = re.search(r'<header[\s>].*?</header>', html, re.DOTALL | re.IGNORECASE)
    footer_match = re.search(r'<footer[\s>].*?</footer>', html, re.DOTALL | re.IGNORECASE)
    parts = []
    if header_match:
        parts.append("<!-- HEADER — gebruik exact deze structuur en class names -->")
        parts.append(header_match.group(0))
    if footer_match:
        parts.append("<!-- FOOTER — gebruik exact deze structuur en class names -->")
        parts.append(footer_match.group(0))
    return "\n\n".join(parts)


def collect_all_html_for_ref(site_dir: Path, max_chars: int = 30000) -> str:
    """Combineer alle gegenereerde HTML bestanden als CSS-referentie voor styles-generatie."""
    parts = []
    total = 0
    for html_file in sorted(site_dir.glob("*.html")):
        try:
            content = html_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(content) > remaining:
            content = content[:remaining] + "\n<!-- afgekapt -->"
        parts.append(f"<!-- === {html_file.name} === -->\n{content}")
        total += len(content)
        if total >= max_chars:
            break
    return "\n\n".join(parts)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Voer de volledige site-factory pipeline uit voor een prospect"
    )
    name_group = parser.add_mutually_exclusive_group(required=True)
    name_group.add_argument("--name", help="Naam van de prospect")
    name_group.add_argument("--auto", action="store_true",
                            help="Verwerk automatisch de volgende openstaande prospect")
    parser.add_argument(
        "--from-step", choices=STEPS, default="collect", metavar="STEP",
        help=f"Begin vanaf deze stap. Keuzes: {', '.join(STEPS)}  (default: collect)"
    )
    parser.add_argument("--force", action="store_true",
                        help="Forceer opnieuw uitvoeren bij al voltooide stappen")
    args = parser.parse_args()

    from_idx  = STEPS.index(args.from_step)
    prospects = load_prospects()

    if args.auto:
        next_name = find_next_prospect(prospects)
        if next_name is None:
            print("[INFO] Geen openstaande prospects in de queue.")
            sys.exit(0)
        args.name = next_name

    try:
        _idx, prospect = find_prospect(prospects, args.name)
    except RuntimeError as e:
        print(f"[FAIL] {e}")
        sys.exit(1)

    company_name = prospect["name"]
    slug         = slugify(company_name)
    project_dir  = OUTPUT_DIR / f"{slug}-next"
    out_dir      = project_dir / "out"
    site_dir     = project_dir  # backwards compat voor stappen die site_dir gebruiken

    LOG_FILE.write_text("", encoding="utf-8")

    global _step_timings, _pipeline_start
    _step_timings  = []
    _pipeline_start = time.monotonic()

    # Voorlopige schatting van total (wordt bijgewerkt na discover_pages)
    total = 10
    n     = 0

    log(f"{'═' * 60}")
    log(f"[START] Pipeline: {company_name}")
    log(f"[INFO]  Slug:       {slug}")
    log(f"[INFO]  Vanaf stap: {args.from_step}")
    log(f"[INFO]  Site-map:   {site_dir}")
    log(f"{'═' * 60}")
    write_status(running=True, prospect=company_name, step="start", step_n=0, total=total)

    # ── collect ───────────────────────────────────────────────────────────────
    if from_idx <= STEPS.index("collect"):
        n += 1
        if prospect.get("status") == "collected" and not args.force:
            log("[INFO] collect: overgeslagen (status=collected)")
        else:
            if not step_collect(company_name, args.force, n, total, company_name):
                write_status(running=False, prospect=company_name, step="collect", result="failed")
                sys.exit(1)
            prospects = load_prospects()
            _idx, prospect = find_prospect(prospects, args.name)

    if prospect.get("status") != "collected":
        log("[FAIL] Prospect is niet gecollect — pipeline gestopt")
        write_status(running=False, prospect=company_name, step="collect", result="failed")
        sys.exit(1)

    if not prospect.get("collected_path"):
        log("[FAIL] collected_path ontbreekt in prospect — pipeline gestopt")
        write_status(running=False, prospect=company_name, step="collect", result="failed")
        sys.exit(1)
    collected_path = Path(prospect["collected_path"])
    if not collected_path.exists():
        log(f"[FAIL] collected_path bestaat niet op schijf: {collected_path}")
        write_status(running=False, prospect=company_name, step="collect", result="failed")
        sys.exit(1)
    briefing_path  = collected_path / "briefing.md"
    pages_path     = collected_path / "pages.json"

    # ── research ──────────────────────────────────────────────────────────────
    if from_idx <= STEPS.index("research"):
        n += 1
        if prospect.get("research_status") == "done" and not args.force:
            log("[INFO] research: overgeslagen (research_status=done)")
        else:
            if not step_research(company_name, args.force, n, total, company_name):
                write_status(running=False, prospect=company_name, step="research", result="failed")
                sys.exit(1)
            prospects = load_prospects()
            _idx, prospect = find_prospect(prospects, args.name)

    # ── brief + validate + repair ─────────────────────────────────────────────
    if from_idx <= STEPS.index("brief"):
        n += 1
        if prospect.get("briefing_status") == "done" and not args.force:
            log("[INFO] brief: overgeslagen (briefing_status=done)")
        else:
            if not step_brief(company_name, args.force, n, total, company_name):
                write_status(running=False, prospect=company_name, step="brief", result="failed")
                sys.exit(1)

        log("\n[INFO] Briefing valideren...")
        if not step_validate_brief(briefing_path):
            n += 1
            log("[INFO] Briefing repareren...")
            if not step_repair_brief(briefing_path, n, total, company_name):
                log("[WARN] repair_brief mislukt — pipeline gaat door met huidige briefing")
            elif not step_validate_brief(briefing_path):
                log("[WARN] Briefing heeft nog kleine problemen na reparatie — pipeline gaat door")

    if not briefing_path.exists():
        log(f"[FAIL] Briefing niet gevonden: {briefing_path}")
        write_status(running=False, prospect=company_name, step="brief", result="failed")
        sys.exit(1)

    # ── generate ──────────────────────────────────────────────────────────────
    if from_idx <= STEPS.index("generate"):
        # Next.js projectmap (broncode) + output (statische export)
        project_dir = OUTPUT_DIR / f"{slug}-next"
        site_dir    = project_dir  # override: site_dir wijst nu naar Next.js project
        out_dir     = project_dir / "out"  # statische export na build

        image_manifest = save_image_manifest(collected_path)

        # Ontdek pagina's
        n += 1
        pages = None
        if pages_path.exists() and not args.force:
            log("[INFO] discover_pages: overgeslagen (pages.json bestaat al)")
            try:
                pages = json.loads(pages_path.read_text(encoding="utf-8")).get("pages", [])
            except Exception as e:
                log(f"[WARN] pages.json onleesbaar ({e}) — opnieuw ontdekken")
        if pages is None:
            pages = step_discover_pages(briefing_path, company_name, pages_path,
                                        n, total, company_name)
            if pages is None:
                write_status(running=False, prospect=company_name, step="discover_pages", result="failed")
                sys.exit(1)

        gen_units  = build_units(pages)
        nav_routes = [""] + [u[2] for u in gen_units if u[0] == "page"]  # "" = home

        layout_unit = [u for u in gen_units if u[0] == "layout"]
        home_unit   = [u for u in gen_units if u[0] == "home"]
        page_units  = [u for u in gen_units if u[0] == "page"]

        total = 6 + 1 + 1 + 1 + len(page_units) + 1 + 1  # scaffold+layout+home+pages+build+validate
        log(f"[INFO] Next.js | {len(page_units)} subpagina's | nav-routes: {nav_routes}")

        # ── Stap 0: scaffold ─────────────────────────────────────────────────
        n += 1
        if not step_scaffold_nextjs(project_dir, n, total, company_name):
            write_status(running=False, prospect=company_name, step="scaffold", result="failed")
            sys.exit(1)

        # Kopieer afbeeldingen naar public/assets/
        copy_images_to_site(collected_path, project_dir / "public")

        # ── Fase 1: layout (header + footer + globals) ───────────────────────
        n += 1
        write_status(running=True, prospect=company_name, step="generate:layout", step_n=n, total=total)
        ok, label, lines = _run_unit_buffered(
            briefing_path, company_name, "layout",
            OUTPUT_DIR / f"{slug}-layout.txt",
            None, "", "", "",
            image_manifest, nav_routes, project_dir,
        )
        for line in lines:
            log(line)
        if not ok:
            write_status(running=False, prospect=company_name, step="generate:layout", result="failed")
            sys.exit(1)

        # ── Fase 2: homepage ─────────────────────────────────────────────────
        n += 1
        write_status(running=True, prospect=company_name, step="generate:home", step_n=n, total=total)
        ok, label, lines = _run_unit_buffered(
            briefing_path, company_name, "home",
            OUTPUT_DIR / f"{slug}-home.txt",
            None, "", "", "",
            image_manifest, nav_routes, project_dir,
        )
        for line in lines:
            log(line)
        if not ok:
            write_status(running=False, prospect=company_name, step="generate:home", result="failed")
            sys.exit(1)

        # Gebruik homepage TSX als referentie voor subpagina's
        home_tsx_path = project_dir / "src" / "app" / "page.tsx"
        ref_tsx = home_tsx_path if home_tsx_path.exists() else None

        # ── Fase 3: subpagina's parallel ─────────────────────────────────────
        failed_units: list[str] = []
        batch_start = time.monotonic()
        write_status(running=True, prospect=company_name, step="generate:parallel", step_n=n, total=total)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(
                    _run_unit_buffered,
                    briefing_path, company_name, "page",
                    OUTPUT_DIR / f"{slug}-{ps}.txt",
                    ref_tsx, ps, pt, pd,
                    image_manifest, nav_routes,
                    project_dir,
                ): ps
                for _, _, ps, pt, pd in page_units
            }
            completed = 0
            for future in as_completed(futures):
                ok, label, lines = future.result()
                completed += 1
                for line in lines:
                    log(line)
                write_status(running=True, prospect=company_name,
                             step=f"generate:{label}", step_n=n + completed, total=total)
                if not ok:
                    failed_units.append(label)

        batch_duration = time.monotonic() - batch_start
        _step_timings.append({
            "step": f"generate:parallel ({len(page_units)} pagina's)",
            "duration_s": round(batch_duration, 1),
            "ok": not failed_units,
        })
        log(f"[INFO] Parallelle batch klaar in {_fmt_duration(batch_duration)}")

        if failed_units:
            log(f"[FAIL] Pagina-units mislukt: {', '.join(failed_units)}")
            write_status(running=False, prospect=company_name, step="generate:parallel", result="failed")
            sys.exit(1)

        n += len(page_units)

        # ── Fase 4: Next.js build ─────────────────────────────────────────────
        n += 1
        if not step_build_nextjs(project_dir, n, total, company_name):
            write_status(running=False, prospect=company_name, step="build_nextjs", result="failed")
            sys.exit(1)

        if not out_dir.exists():
            log(f"[FAIL] /out directory niet gevonden na build: {out_dir}")
            write_status(running=False, prospect=company_name, step="build_nextjs", result="failed")
            sys.exit(1)
        log(f"[OK]  Statische export: {out_dir} ({len(list(out_dir.rglob('*.html')))} HTML-bestanden)")

    # ── validate_site → repair → re-validate ─────────────────────────────────
    # Voor Next.js: validate/repair/polish draaien op de /out directory
    validate_dir = out_dir if (project_dir / "out").exists() else site_dir

    if from_idx <= STEPS.index("validate"):
        n += 1
        json_out = OUTPUT_DIR / f"{slug}-validation.json"
        if not step_validate_site(validate_dir, json_out, n, total, company_name):
            log("[INFO] Site heeft validatiefouten — reparatie uitvoeren...")
            n += 1
            step_repair_site(validate_dir, json_out, n, total, company_name)
            n += 1
            if not step_validate_site(validate_dir, json_out, n, total, company_name):
                log(f"[WARN] Site heeft nog steeds validatiefouten na reparatie. Rapport: {json_out}")
        else:
            n += 1
            step_repair_site(validate_dir, json_out, n, total, company_name)

        # ── Content-check ──────────────────────────────────────────────────────
        n += 1
        step_check_content(validate_dir, collected_path, company_name, n, total, company_name)

        # ── Screenshot-validatie ───────────────────────────────────────────────
        n += 1
        screenshot_json = validate_dir / "screenshot_validation.json"
        step_screenshot_validate(validate_dir, n, total, company_name)

        # ── Screenshot-repair ──────────────────────────────────────────────────
        if screenshot_json.exists():
            try:
                shot_data = json.loads(screenshot_json.read_text(encoding="utf-8"))
                if shot_data.get("total_issues", 0) > 0:
                    n += 1
                    log(f"[INFO] {shot_data['total_issues']} visuele issue(s) — screenshot-repair...")
                    step_repair_site(validate_dir, json_out, n, total, company_name,
                                     screenshot_json=screenshot_json)
                else:
                    log("[INFO] Geen visuele issues — screenshot-repair overgeslagen")
            except Exception as e:
                log(f"[WARN] Kon screenshot_validation.json niet lezen: {e}")

        # ── Cohesion pass ──────────────────────────────────────────────────────
        n += 1
        log("\n[INFO] Cohesion pass uitvoeren...")
        step_cohesion_pass(validate_dir, n, total, company_name)

        # ── Polish ─────────────────────────────────────────────────────────────
        n += 1
        log("\n[INFO] Polish uitvoeren...")
        step_polish_site(validate_dir, company_name, n, total, company_name)

        # ── Outreach-mail genereren ───────────────────────────────────────────
        n += 1
        log("\n[INFO] Outreach-mail genereren...")
        step_generate_mail(company_name, n, total, company_name)

    mark_site_done(company_name)
    save_timings(collected_path, company_name)
    write_status(running=False, prospect=company_name, step="done", result="ok")

    log(f"\n{'═' * 60}")
    log(f"[OK] Pipeline voltooid voor: {company_name}")
    log(f"[OK] Site: {site_dir}")
    log(f"{'═' * 60}")


if __name__ == "__main__":
    main()
