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


def step_generate_unit(briefing_path: Path, company_name: str, unit_key: str,
                        out_path: Path, ref_html: Path | None, ref_css: Path | None,
                        n: int, total: int,
                        page_file: str = "", page_title: str = "", page_desc: str = "",
                        image_manifest: Path | None = None,
                        nav_pages: list[str] | None = None) -> bool:
    cmd = [
        "python", str(SCRIPTS_DIR / "generate_site.py"),
        "--brief",   str(briefing_path),
        "--company", company_name,
        "--unit",    unit_key,
        "--out",     str(out_path),
    ]
    if ref_html and ref_html.exists():
        cmd += ["--ref-html", str(ref_html)]
    if ref_css and ref_css.exists():
        cmd += ["--ref-css", str(ref_css)]
    if page_file:
        cmd += ["--page-file",  page_file]
    if page_title:
        cmd += ["--page-title", page_title]
    if page_desc:
        cmd += ["--page-desc",  page_desc]
    if image_manifest and image_manifest.exists():
        cmd += ["--image-manifest", str(image_manifest)]
    if nav_pages:
        cmd += ["--nav-pages", json.dumps(nav_pages)]

    label = f"generate:{page_file or unit_key}"
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
    out_path: Path, ref_html: Path | None, ref_css: Path | None,
    page_file: str, page_title: str, page_desc: str,
    image_manifest: Path | None, nav_pages: list[str] | None,
    site_dir: Path,
) -> tuple[bool, str, list[str]]:
    """Genereer + parse één unit in een thread. Buffers output, returnt (ok, label, lines)."""
    label = page_file or unit_key
    lines = [f"\n{'─' * 60}", f"[UNIT] {label}", f"{'─' * 60}"]

    gen_cmd = [
        "python", str(SCRIPTS_DIR / "generate_site.py"),
        "--brief",   str(briefing_path),
        "--company", company_name,
        "--unit",    unit_key,
        "--out",     str(out_path),
    ]
    if ref_html and ref_html.exists():
        gen_cmd += ["--ref-html", str(ref_html)]
    if ref_css and ref_css.exists():
        gen_cmd += ["--ref-css", str(ref_css)]
    if page_file:
        gen_cmd += ["--page-file", page_file, "--page-title", page_title, "--page-desc", page_desc]
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
            lines.append(f"[WARN] generate:{label} mislukt (poging {attempt}/{MAX_RETRIES + 1}) — herprobeert na 10s")
            time.sleep(10)
        else:
            lines.append(f"[FAIL] generate:{label} mislukt na {MAX_RETRIES + 1} pogingen (exit {proc.returncode})")
            return False, label, lines

    parse_cmd = [
        "python", str(SCRIPTS_DIR / "parse_generated_site.py"),
        "--input",  str(out_path),
        "--outdir", str(site_dir),
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


def step_repair_site(site_dir: Path, json_out: Path, n: int, total: int, prospect: str) -> bool:
    cmd = [
        "python", str(SCRIPTS_DIR / "repair_generated_site.py"),
        "--site-dir", str(site_dir),
    ]
    if json_out.exists():
        cmd += ["--validation-json", str(json_out)]
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
      (unit_key, file_stem, page_file, page_title, page_desc)

    Volgorde: home_html → alle page-units → styles → scripts
    Styles komt na alle HTML zodat alle class names beschikbaar zijn als CSS-referentie.
    """
    units = [("home_html", "home-html", "", "", "")]

    for page in pages:
        stem = page["file"].replace(".html", "").replace("/", "-")
        units.append(("page", stem, page["file"], page.get("title", ""), page.get("description", "")))

    units.append(("styles",  "styles",  "", "", ""))
    units.append(("scripts", "scripts", "", "", ""))

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
    site_dir     = OUTPUT_DIR / f"{slug}-site"

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
        css_dir       = collected_path / "assets" / "css"
        ref_css_files = sorted(css_dir.glob("*.css")) if css_dir.exists() else []
        ref_css_path  = ref_css_files[0] if ref_css_files else None

        # Sla afbeeldingen manifest op
        image_manifest = save_image_manifest(collected_path)

        # Ontdek pagina's (of gebruik bestaande pages.json)
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

        gen_units = build_units(pages)
        nav_pages = [p["file"] for p in pages] if pages else []
        nav_pages_with_home = ["index.html"] + [f for f in nav_pages if f != "index.html"]

        home_units  = [(uk, fs, pf, pt, pd) for uk, fs, pf, pt, pd in gen_units if uk == "home_html"]
        page_units  = [(uk, fs, pf, pt, pd) for uk, fs, pf, pt, pd in gen_units if uk == "page"]
        asset_units = [(uk, fs, pf, pt, pd) for uk, fs, pf, pt, pd in gen_units
                       if uk not in ("home_html", "page")]

        total = 6 + len(home_units) + len(page_units) + len(asset_units) * 2 + 1
        log(f"[INFO] Fase 1: homepage | Fase 2: {len(page_units)} pagina's parallel | Fase 3: styles + scripts")
        log(f"[INFO] Nav-pagina's: {nav_pages_with_home}")

        # ── Fase 1: homepage alleen (bron voor class names + header/footer) ──
        write_status(running=True, prospect=company_name, step="generate:home", step_n=n, total=total)
        ok, label, lines = _run_unit_buffered(
            briefing_path, company_name, "home_html",
            OUTPUT_DIR / f"{slug}-home-html.txt",
            None, None, "", "", "",
            image_manifest, nav_pages_with_home, site_dir,
        )
        for line in lines:
            log(line)
        n += 1
        if not ok:
            log("[FAIL] Homepage generatie mislukt")
            write_status(running=False, prospect=company_name, step="generate:home", result="failed")
            sys.exit(1)

        # Extraheer header + footer uit index.html als referentie voor subpagina's
        index_html_path = site_dir / "index.html"
        ref_path = site_dir / "_header_footer_ref.html"  # altijd gedefinieerd
        header_footer_ref = ""
        if index_html_path.exists():
            homepage_html = index_html_path.read_text(encoding="utf-8", errors="ignore")
            header_footer_ref = _extract_header_footer(homepage_html)
            if header_footer_ref:
                ref_path.write_text(header_footer_ref, encoding="utf-8")
                log(f"[INFO] Header/footer referentie opgeslagen ({len(header_footer_ref)} tekens)")
        if not header_footer_ref:
            log("[WARN] Geen header/footer gevonden in homepage — subpagina's krijgen geen structuurreferentie")

        # ── Fase 2: subpagina's parallel met homepage als referentie ─────────
        failed_units: list[str] = []
        batch_start = time.monotonic()
        # Stuur de volledige homepage als referentie naar subpagina's zodat ze dezelfde
        # class names hergebruiken voor vergelijkbare componenten. De CSS is gebouwd op
        # homepage-classes — als subpagina's die ook gebruiken, werkt de CSS automatisch.
        ref_html_for_pages = index_html_path if index_html_path.exists() else None

        write_status(running=True, prospect=company_name, step="generate:parallel", step_n=n, total=total)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(
                    _run_unit_buffered,
                    briefing_path, company_name, uk,
                    OUTPUT_DIR / f"{slug}-{fs}.txt",
                    ref_html_for_pages, None,
                    pf, pt, pd,
                    image_manifest, nav_pages_with_home,
                    site_dir,
                ): (uk, fs, pf)
                for uk, fs, pf, pt, pd in page_units
            }
            completed = 0
            for future in as_completed(futures):
                ok, label, lines = future.result()
                completed += 1
                for line in lines:
                    log(line)
                write_status(running=True, prospect=company_name,
                             step=f"generate:{label}",
                             step_n=n + completed, total=total)
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

        # ── Fase 2: styles + scripts sequentieel (styles heeft alle HTML nodig) ─
        for unit_key, file_stem, page_file, page_title, page_desc in asset_units:
            n += 1
            out_path = OUTPUT_DIR / f"{slug}-{file_stem}.txt"

            if unit_key == "styles":
                # Gebruik index.html als primaire HTML-referentie (zet het ontwerp)
                index_html_path = site_dir / "index.html"
                ref_html = index_html_path if index_html_path.exists() else None
                ref_css  = ref_css_path
                # Extraheer alle unieke class names uit ALLE gegenereerde pagina's
                # State/utility classes die geen eigen CSS-blok nodig hebben
                _SKIP_CLASSES = {
                    "active", "inactive", "open", "closed", "hidden", "visible",
                    "disabled", "selected", "checked", "loading", "loaded",
                    "is-active", "is-open", "is-hidden", "is-visible",
                    "no-js", "js", "sr-only", "clearfix", "container",
                }
                # Subpagina's hergebruiken nu dezelfde class names als de homepage, dus
                # het totaal aantal unieke classes over alle pagina's blijft beperkt.
                # We verzamelen uit alle gegenereerde pagina's — zo dekt de CSS alles af.
                all_classes = set()
                for html_file in sorted(site_dir.glob("*.html")):
                    if html_file.name.startswith("_"):
                        continue
                    try:
                        html_content = html_file.read_text(encoding="utf-8", errors="ignore")
                        for m in re.finditer(r'class="([^"]+)"', html_content):
                            for cls in m.group(1).split():
                                if cls not in _SKIP_CLASSES and not cls.startswith("js-"):
                                    all_classes.add(cls)
                    except Exception:
                        pass
                class_list_str = "\n".join(f"- .{c}" for c in sorted(all_classes))
                page_desc = class_list_str
                log(f"[INFO] styles: {len(all_classes)} unieke CSS-klassen over alle pagina's")
            elif unit_key == "scripts":
                # Scripts ziet index.html zodat het exact de juiste class names en IDs gebruikt
                index_html_path = site_dir / "index.html"
                ref_html = index_html_path if index_html_path.exists() else None
                ref_css  = None
            else:
                ref_html = None
                ref_css  = None

            if not step_generate_unit(briefing_path, company_name, unit_key,
                                       out_path, ref_html, ref_css, n, total,
                                       page_file=page_file, page_title=page_title, page_desc=page_desc,
                                       image_manifest=image_manifest,
                                       nav_pages=nav_pages_with_home):
                write_status(running=False, prospect=company_name,
                             step=f"generate:{unit_key}", result="failed")
                sys.exit(1)

            n += 1
            if not step_parse_unit(out_path, site_dir, n, total, company_name):
                write_status(running=False, prospect=company_name,
                             step=f"parse:{file_stem}", result="failed")
                sys.exit(1)

        # Kopieer originele afbeeldingen naar gegenereerde site
        copy_images_to_site(collected_path, site_dir)

    # ── validate_site → repair → re-validate ─────────────────────────────────
    if from_idx <= STEPS.index("validate"):
        n += 1
        json_out = OUTPUT_DIR / f"{slug}-validation.json"
        if not step_validate_site(site_dir, json_out, n, total, company_name):
            log("[INFO] Site heeft validatiefouten — reparatie uitvoeren...")
            n += 1
            step_repair_site(site_dir, json_out, n, total, company_name)
            n += 1
            if not step_validate_site(site_dir, json_out, n, total, company_name):
                log(f"[WARN] Site heeft nog steeds validatiefouten na reparatie. Rapport: {json_out}")
                # Niet afbreken — warnings zijn acceptabel, de site is bruikbaar
        else:
            # Validatie geslaagd — toch reparatie uitvoeren voor site-brede fixes
            # (hamburger, sr-only, footerYear, ref-files) die de validator niet dekt
            n += 1
            step_repair_site(site_dir, json_out, n, total, company_name)

        # ── Content-check: bedrijfsnaam, contact, placeholders, paginastructuur ──
        n += 1
        step_check_content(site_dir, collected_path, company_name, n, total, company_name)

        # ── Screenshot-validatie: visuele problemen detecteren via Claude Vision ──
        n += 1
        step_screenshot_validate(site_dir, n, total, company_name)

        # ── Polish: dedupliceer pagina's + herstel header-consistentie + demo-banner ──
        n += 1
        log("\n[INFO] Polish uitvoeren (deduplicatie + header-consistentie + demo-banner)...")
        step_polish_site(site_dir, company_name, n, total, company_name)

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
