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
RUN_LOG_FILE: Path | None = None

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
        if RUN_LOG_FILE:
            try:
                RUN_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
                with RUN_LOG_FILE.open("a", encoding="utf-8") as f:
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

from prospects_utils import is_queueable, load_prospects, update_prospect  # noqa: E402


def find_prospect(prospects: list, name: str) -> tuple[int, dict]:
    for i, p in enumerate(prospects):
        if p.get("name", "").strip().lower() == name.strip().lower():
            return i, p
    raise RuntimeError(f"Prospect '{name}' niet gevonden")


def find_next_prospect(prospects: list) -> str | None:
    for p in prospects:
        if is_queueable(p):
            return p["name"]
    return None


def mark_site_done(name: str, homepage_only: bool = False) -> None:
    update_prospect(name, status="collected", site_status="done",
                    homepage_only=homepage_only)


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


def step_inventory(slug: str, n: int, total: int, prospect: str) -> bool:
    """Bouw inventory.json uit collected raw data — verbatim feiten."""
    cmd = ["python", str(SCRIPTS_DIR / "inventory.py"), "--slug", slug]
    return run_cmd(cmd, "inventory", n, total, prospect)


def step_quality_gate(slug: str, n: int, total: int, prospect: str) -> bool:
    """Valideer inventory tegen v1-contract. Fail = pipeline stopt."""
    cmd = ["python", str(SCRIPTS_DIR / "quality_gate.py"), "--slug", slug, "--strict", "--quiet"]
    return run_cmd(cmd, "quality_gate", n, total, prospect)


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


def _write_nextjs_config(project_dir: Path, slug: str, for_deploy: bool = False) -> None:
    """Schrijf next.config.ts — met basePath voor preview, zonder voor deploy."""
    base = f"/sites/{slug}" if not for_deploy else ""
    prefix = f"/sites/{slug}" if not for_deploy else ""
    config = (
        'import type { NextConfig } from "next";\n\n'
        'const nextConfig: NextConfig = {\n'
        '  output: "export",\n'
        '  trailingSlash: true,\n'
        '  images: { unoptimized: true },\n'
        + (f'  basePath: "{base}",\n  assetPrefix: "{prefix}",\n' if base else '')
        + '};\n\nexport default nextConfig;\n'
    )
    (project_dir / "next.config.ts").write_text(config, encoding="utf-8")


def step_scaffold_nextjs(project_dir: Path, slug: str, n: int, total: int, prospect: str) -> bool:
    """Maak een nieuwe Next.js app aan als die nog niet bestaat."""
    if (project_dir / "package.json").exists():
        log(f"[INFO] scaffold: overgeslagen ({project_dir.name} bestaat al)")
        _write_nextjs_config(project_dir, slug)  # herschrijf altijd zodat basePath klopt
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

    _write_nextjs_config(project_dir, slug)
    log(f"[OK]  next.config.ts geschreven (basePath: /sites/{slug})")

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

    # Alle npm packages in één keer installeren
    _run_in_project(
        ["npm", "install",
         "lucide-react",
         "daisyui",
         "tailwindcss-animate",
         "@tailwindcss/typography",
         "@tailwindcss/forms",
         "next-sitemap",
         "embla-carousel-react",
         "embla-carousel-autoplay",
         "leaflet",
         "react-leaflet",
         "@types/leaflet"],
        "npm packages installeren",
    )

    # shadcn-stijl componenten direct aanmaken (geen init nodig)
    _run_in_project(["npm", "install", "clsx", "tailwind-merge", "class-variance-authority", "@radix-ui/react-accordion", "@radix-ui/react-separator", "@radix-ui/react-slot"], "Radix UI + utilities")
    _create_ui_components(project_dir)

    # Verwijder scaffold-defaults (page.tsx + layout.tsx) zodat smart-resume
    # ze niet als 'al gegenereerd' aanmerkt. home-unit en layout-unit schrijven
    # hier verse content overheen.
    for fname in ("page.tsx", "layout.tsx"):
        default_file = project_dir / "src" / "app" / fname
        if default_file.exists():
            default_file.unlink()
            log(f"[OK]  scaffold default {fname} verwijderd (unit schrijft nieuwe)")

    log("[OK]  scaffold compleet: Next.js + Tailwind + Lucide + shadcn-stijl components")
    return True


_FAKE_LUCIDE_ICONS = {
    # Social media icons (niet in Lucide)
    "Instagram": "Camera", "Facebook": "Globe", "Twitter": "MessageCircle",
    "Whatsapp": "MessageCircle", "WhatsApp": "MessageCircle", "TikTok": "Video",
    "Pinterest": "Image", "Snapchat": "Camera", "Youtube": "Play",
    "LinkedIn": "Briefcase", "Linkedin": "Globe", "Telegram": "Send",
    # Nederlandse namen (Claude hallucineet soms Nederlands)
    "Telefoon": "Phone", "Telefoonnummer": "Phone",
    "SmartTelefoon": "Smartphone", "SmartPhone": "Smartphone",
    "MegaTelefoon": "Phone", "MobielTelefoon": "Smartphone",
    "Mail": "Mail",  # Mail bestaat wel, maar voor de zekerheid
    "E-mail": "Mail", "Email": "Mail",
    "Adres": "MapPin", "Locatie": "MapPin",
    "Website": "Globe", "Klok": "Clock", "Sterren": "Star",
    "Gebouw": "Building2", "Kantoor": "Building2",
    "Winkelwagen": "ShoppingCart", "Winkeltas": "ShoppingBag",
    "Beveiliging": "Shield", "Sleutel": "Key",
    "Grafiek": "BarChart2", "Statistiek": "BarChart",
}


def _fix_lucide_icons(project_dir: Path) -> None:
    """
    Vervang niet-bestaande Lucide icons door geldige alternatieven.
    Alleen in import-statements — niet in strings/object-keys om
    bijwerkingen zoals eE-mail te voorkomen.
    """
    fixed = 0
    for tsx in (project_dir / "src").rglob("*.tsx"):
        try:
            content = tsx.read_text(encoding="utf-8")
            if "lucide-react" not in content:
                continue
            new = content
            for fake, replacement in _FAKE_LUCIDE_ICONS.items():
                if fake not in new:
                    continue
                # Vervang alleen in lucide-react import-regels
                new = re.sub(
                    rf'(import\s*\{{[^}}]*)\b{re.escape(fake)}\b([^}}]*\}}\s*from\s*["\']lucide-react["\'])',
                    lambda m, r=replacement: m.group(0).replace(fake, r),
                    new,
                )
                # Vervang ook als JSX-component in de code (<Fake ... />)
                new = re.sub(rf'<{re.escape(fake)}(\s|/>)', f'<{replacement}\\1', new)
                new = re.sub(rf'</{re.escape(fake)}>', f'</{replacement}>', new)
                # Vervang ook als identifier in objecten/arrays (icon: Fake, of [Fake,])
                # maar NIET in strings
                new = re.sub(
                    rf'(?<!["\'\w])\b{re.escape(fake)}\b(?!["\'\w])',
                    replacement, new
                )
            # Dedupliceer import namen (vervanging kan duplicaten veroorzaken)
            def _dedup_import(m):
                before, names_str, after = m.group(1), m.group(2), m.group(3)
                names = [n.strip() for n in re.split(r',\s*', names_str) if n.strip()]
                seen: set[str] = set()
                deduped = [n for n in names if not (n in seen or seen.add(n))]
                return before + ", ".join(deduped) + after
            new = re.sub(
                r'(import\s*\{)([^}]+)(\}\s*from\s*["\']lucide-react["\'])',
                _dedup_import, new, flags=re.DOTALL,
            )
            if new != content:
                tsx.write_text(new, encoding="utf-8")
                fixed += 1
        except Exception:
            pass
    if fixed:
        log(f"[OK]  Lucide icon fix: {fixed} bestand(en) gecorrigeerd")


def _patch_daisyui_theme(project_dir: Path, collected_path: Path | None) -> None:
    """Zet data-theme op het <html> element in layout.tsx."""
    theme = "light"
    if collected_path:
        theme_file = collected_path / "theme.json"
        if theme_file.exists():
            try:
                theme = json.loads(theme_file.read_text()).get("theme", "light")
            except Exception:
                pass

    layout = project_dir / "src" / "app" / "layout.tsx"
    if not layout.exists():
        return
    try:
        c = layout.read_text(encoding="utf-8")
        # Vervang of voeg data-theme toe op <html> element
        new = re.sub(
            r'<html([^>]*?)>',
            lambda m: f'<html{m.group(1)} data-theme="{theme}">'
            if f'data-theme' not in m.group(1)
            else re.sub(r'data-theme="[^"]*"', f'data-theme="{theme}"', m.group(0)),
            c, count=1
        )
        if new != c:
            layout.write_text(new, encoding="utf-8")
            log(f"[OK]  layout.tsx: data-theme=\"{theme}\" gezet")
        else:
            log(f"[INFO] layout.tsx: data-theme al aanwezig of <html> niet gevonden")
    except Exception as e:
        log(f"[WARN] _patch_daisyui_theme: {e}")


def _fix_client_components(project_dir: Path) -> None:
    """
    Voeg 'use client' toe aan pages met event handlers of interactieve componenten.
    Verwijder metadata exports uit client components (niet toegestaan in Next.js App Router).
    """
    import re as _re
    app_dir = project_dir / "src" / "app"
    if not app_dir.exists():
        return

    CLIENT_KEYWORDS = [
        "onMouse", "onClick", "onChange", "onSubmit", "onFocus", "onBlur",
        "useState", "useEffect", "useRef", "useCallback", "useMemo",
        "LeafletMap", "GalleryCarousel", "BookingWidget", "Sheet",
    ]
    METADATA_IMPORT_RE = _re.compile(
        r'import (?:type )?\{ [^}]*Metadata[^}]* \} from [^\n]+\n'
    )

    def _strip_metadata_export(text: str) -> str:
        """
        Verwijder 'export const metadata[:Metadata] = { ... }' met brace-balancing
        (regex faalt als het blok op een nieuwe regel eindigt zonder ';').
        """
        m = _re.search(r'export\s+const\s+metadata\s*(?::\s*\w+\s*)?[:=]', text)
        if not m:
            return text
        start = m.start()
        # Stap terug naar de regel-start (incl. eventuele leading newline)
        while start > 0 and text[start - 1] != "\n":
            start -= 1
        # Vind de openende {
        brace = text.find("{", m.end())
        if brace == -1:
            return text
        # Balanceer braces
        depth = 1
        i = brace + 1
        while i < len(text) and depth > 0:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        # Skip optionele ';' en whitespace tot newline
        while i < len(text) and text[i] in " ;\t":
            i += 1
        if i < len(text) and text[i] == "\n":
            i += 1
        return text[:start] + text[i:]

    for tsx in sorted(app_dir.rglob("*/page.tsx")):
        try:
            c = tsx.read_text(encoding="utf-8", errors="ignore")
            orig = c
            is_client = c.startswith('"use client"')

            # Voeg use client toe als interactieve keywords aanwezig zijn
            if not is_client and any(kw in c for kw in CLIENT_KEYWORDS):
                c = '"use client";\n' + c
                is_client = True

            # Verwijder metadata export uit client components
            if is_client and "export const metadata" in c:
                c = _strip_metadata_export(c)
                # Verwijder Metadata import als niet meer gebruikt
                if "Metadata" not in c.replace("import", ""):
                    c = METADATA_IMPORT_RE.sub("", c)

            if c != orig:
                tsx.write_text(c, encoding="utf-8")
        except Exception:
            pass


def _fix_nav_spacing(project_dir: Path) -> None:
    """
    Voeg gap toe aan nav-containers waar Claude dit vergeet.
    Voorkomt aaneengesloten navigatie-items ('HomeOveronsDiensten').
    """
    layout = project_dir / "src" / "app" / "layout.tsx"
    header = project_dir / "src" / "components" / "Header.tsx"
    for tsx_path in [layout, header]:
        if not tsx_path.exists():
            continue
        try:
            c = tsx_path.read_text(encoding="utf-8")
            orig = c
            # Voeg gap-2 toe aan flex nav containers die het missen
            import re as _re
            c = _re.sub(
                r'(className="[^"]*\bflex\b[^"]*\bnav\b[^"]*")(?![^"]*\bgap-)',
                lambda m: m.group(0).replace('"', ' gap-1"', 1),
                c,
            )
            # Specifiek: flex items-center zonder gap in nav context
            c = _re.sub(
                r'(<nav[^>]*className="[^"]*flex[^"]*items-center[^"]*")(?![^"]*gap)',
                lambda m: m.group(0)[:-1] + ' gap-1"',
                c,
            )
            if c != orig:
                tsx_path.write_text(c, encoding="utf-8")
        except Exception:
            pass


def _strip_em_dashes(project_dir: Path) -> None:
    """
    Vervang em-dashes (—) en en-dashes (–) in JSX-tekstinhoud door komma's.
    Laat numerieke ranges (09:00-17:00), import-pijlen of code intact:
    we vervangen alleen ' — ', '— ', ' —', ' – ', '– ', ' –'.
    """
    fixed = 0
    for tsx in (project_dir / "src").rglob("*.tsx"):
        try:
            c = tsx.read_text(encoding="utf-8")
            new = c
            # Spaces rondom of aan een kant: vervang door comma + spatie
            for ch in ("—", "–"):
                new = new.replace(f" {ch} ", ", ")
                new = new.replace(f"{ch} ",  ", ")
                new = new.replace(f" {ch}",  ",")
                new = new.replace(ch, ",")
            if new != c:
                tsx.write_text(new, encoding="utf-8")
                fixed += 1
        except Exception:
            pass
    if fixed:
        log(f"[OK]  Em-dashes vervangen door komma's in {fixed} bestand(en)")


def _fix_icon_as_text(project_dir: Path) -> None:
    """
    Vervang gevallen waar Claude een Lucide-icoonnaam als tekst heeft geschreven,
    bijv. 'Stuur een MessageCircle-bericht' → 'Stuur een WhatsApp-bericht'.
    """
    import re as _re
    ICON_TEXT_RE = _re.compile(
        r'\b(Phone|Mail|MapPin|MessageCircle|ArrowRight|ChevronRight|Globe|ExternalLink)'
        r'[-\s]?(bericht|link|knop|icoon|icon|button)?\b',
        _re.IGNORECASE,
    )
    REPLACEMENTS = {
        "messagecircle": "WhatsApp",
        "phone": "Telefoon",
        "mail": "E-mail",
        "mappin": "Adres",
        "globe": "Website",
        "externallink": "Bekijk",
    }
    for tsx in (project_dir / "src").rglob("*.tsx"):
        try:
            c = tsx.read_text(encoding="utf-8")
            orig = c
            # Alleen aanpassen als er een Nederlandse context-suffix staat
            # (bericht/link/knop/icoon) om code-identifiers intact te laten.
            for pat, repl in REPLACEMENTS.items():
                c = _re.sub(
                    rf'\b{pat}-(?:bericht|link|knop|icoon)\b',
                    repl,
                    c,
                    flags=_re.IGNORECASE,
                )
            if c != orig:
                tsx.write_text(c, encoding="utf-8")
        except Exception:
            pass


def _fix_daisyui_colors(project_dir: Path) -> None:
    """
    Vervang niet-bestaande CSS-klassen door DaisyUI semantische klassen.
    Vangt twee patronen:
    1. CSS-variabelen die niet bestaan: bg-[--color-bg]
    2. Uitgevonden kleurnames die Tailwind niet kent: bg-cream, bg-forest
    """
    import re as _re

    # Uitgevonden kleurnames die Claude soms gebruikt als header-achtergrond
    INVENTED_BG_HEADER = _re.compile(
        r'bg-(?:cream|ivory|white-warm|off-white|sand|light|background|bg|surface|paper|canvas)'
        r'(?:\s|/|\b)',
        _re.IGNORECASE,
    )

    for tsx in (project_dir / "src").rglob("*.tsx"):
        try:
            c = tsx.read_text(encoding="utf-8")
            orig = c
            # Header/nav: transparante of onbekende achtergrond → glassmorphism
            c = c.replace("bg-[--color-bg]", "bg-base-100/95 backdrop-blur-sm")
            c = c.replace("bg-[--color-background]", "bg-base-100/95 backdrop-blur-sm")
            # Header.tsx specifiek: vervang uitgevonden lichte kleurnamen
            if tsx.name == "Header.tsx":
                c = INVENTED_BG_HEADER.sub("bg-base-100/95 backdrop-blur-sm ", c)
            # Footer: primaire kleur met witte tekst → neutral
            c = _re.sub(
                r'bg-\[--color-primary\](\s+)text-white',
                r'bg-neutral\1text-neutral-content', c
            )
            c = _re.sub(
                r'bg-primary(\s+)text-white(?!\s*/)',
                r'bg-neutral\1text-neutral-content', c
            )
            if "neutral-content" in c or tsx.name == "Footer.tsx":
                c = c.replace("text-white/80", "text-neutral-content/80")
                c = c.replace("text-white/60", "text-neutral-content/60")
            if orig != c:
                tsx.write_text(c, encoding="utf-8")
        except Exception:
            pass


def _fix_page_function_names(project_dir: Path) -> None:
    """Fix functienamen die beginnen met een cijfer (bijv. 360TourPage → TourPage360)."""
    import re as _re
    for tsx in (project_dir / "src" / "app").rglob("*.tsx"):
        try:
            c = tsx.read_text(encoding="utf-8")
            fixed = _re.sub(r'function (\d+)(\w+)\(\)', lambda m: f'function {m.group(2)}{m.group(1)}()', c)
            if fixed != c:
                tsx.write_text(fixed, encoding="utf-8")
                log(f"[OK]  {tsx.name}: functienaam gecorrigeerd (begon met cijfer)")
        except Exception:
            pass


def _fix_header_pathname(project_dir: Path) -> None:
    """
    Voeg een 'mounted' guard toe aan Header.tsx voor usePathname().
    In static Next.js exports rendert de server met pathname=null —
    client rendert met echte pathname. Deze mismatch kan React-hydration
    breken, waardoor useState/onClick niet werkt (mobiel menu kapot).

    Fix: mounted=false op server, true na eerste client-render.
    Active-state check alleen als mounted=true → geen hydration mismatch.
    """
    header = project_dir / "src" / "components" / "Header.tsx"
    if not header.exists():
        return
    try:
        c = header.read_text(encoding="utf-8")
        if "usePathname" not in c or "mounted" in c:
            return

        # Voeg mounted state toe na de bestaande useState/usePathname
        c = c.replace(
            "const pathname = usePathname();",
            "const pathname = usePathname();\n  const [mounted, setMounted] = useState(false);\n  useEffect(() => { setMounted(true); }, []);",
        )
        # Vervang isActive door een versie die altijd callable is en
        # mounted gebruikt om hydration mismatch te voorkomen.
        # Let op: raak de aanroepen (isActive(href)) NIET aan — alleen de definitie.
        c = re.sub(
            r'(const isActive\s*=\s*)(?:mounted\s*\?\s*[^\n:]+:\s*false;?|'
            r'\(href[^)]*\)\s*=>\s*[^\n;]+(?:\n\s+[^\n;]+;)?)',
            r'const isActive = (href: string) => mounted && (href === "/" ? (pathname ?? "") === "/" : pathname?.startsWith(href));',
            c,
        )
        header.write_text(c, encoding="utf-8")
        log("[OK]  Header.tsx: mounted guard toegevoegd voor usePathname (fix mobiel menu)")
    except Exception as e:
        log(f"[WARN] _fix_header_pathname: {e}")


def _fix_layout_tsx(project_dir: Path) -> None:
    """
    Repareer veelvoorkomende syntax-problemen in layout.tsx:
    - Kapotte className met backtick/template literal remnants
    - darkMode: ['class'] → darkMode: 'class' in tailwind.config.ts
    """
    layout = project_dir / "src" / "app" / "layout.tsx"
    if layout.exists():
        try:
            c = layout.read_text(encoding="utf-8")
            # Fix backtick remnants in JSX attributes
            fixed = re.sub(r'className="[^"]*`\}?>', 'className="">', c)
            fixed = re.sub(r'className=\{[^}]*`[^}]*\}>', 'className="">', fixed)
            if fixed != c:
                layout.write_text(fixed, encoding="utf-8")
                log("[OK]  layout.tsx: className syntax gerepareerd")
        except Exception:
            pass

    # Fix ongeldige Google Font weights — vervang alle 500/600/800 door 400
    layout = project_dir / "src" / "app" / "layout.tsx"
    if layout.exists():
        try:
            c = layout.read_text(encoding="utf-8")
            fixed = re.sub(r'"(500|600|800)"', '"400"', c)
            fixed = re.sub(r',\s*["\'](?:500|600|800)["\']', '', fixed)
            if fixed != c:
                layout.write_text(fixed, encoding="utf-8")
                log("[OK]  layout.tsx: ongeldige font weights verwijderd (500/600/800→400)")
        except Exception:
            pass

    # Fix darkMode config in tailwind.config.ts
    tc = project_dir / "tailwind.config.ts"
    if tc.exists():
        try:
            c = tc.read_text(encoding="utf-8")
            fixed = re.sub(r'darkMode:\s*\[["\']class["\']\]', 'darkMode: "class"', c)
            if fixed != c:
                tc.write_text(fixed, encoding="utf-8")
                log("[OK]  tailwind.config.ts: darkMode syntax gerepareerd")
        except Exception:
            pass


_INVALID_STYLE_PROPS = re.compile(
    r',\s*(?:divideColor|divideWidth|divideOpacity|divideStyle|'
    r'ringColor|ringWidth|ringOpacity|ringOffsetColor|ringOffsetWidth|'
    r'gradientColorStops|placeholderColor|placeholderOpacity)\s*:[^,}]+'
)


def _fix_invalid_style_props(project_dir: Path) -> None:
    """Verwijder Tailwind class names die per ongeluk als inline CSS properties gebruikt worden."""
    fixed = 0
    for tsx in (project_dir / "src").rglob("*.tsx"):
        try:
            content = tsx.read_text(encoding="utf-8")
            new = _INVALID_STYLE_PROPS.sub("", content)
            if new != content:
                tsx.write_text(new, encoding="utf-8")
                fixed += 1
        except Exception:
            pass
    if fixed:
        log(f"[OK]  TypeScript fix: {fixed} bestand(en) met ongeldige style-props gerepareerd")


def _fix_globals_css(project_dir: Path, collected_path: Path | None = None) -> None:
    """
    Vervang Tailwind v3 syntax door v4, activeer DaisyUI en injecteer merkkleur
    uit de briefing als CSS-variabelen.
    """
    css_path = project_dir / "src" / "app" / "globals.css"
    if not css_path.exists():
        return

    css = css_path.read_text(encoding="utf-8")

    # Vervang v3 directives door v4 import (één keer, bovenaan)
    has_import = "@import" in css and "tailwindcss" in css
    has_v3     = "@tailwind base" in css or "@tailwind components" in css or "@tailwind utilities" in css

    # Genereer volledig kleurenpalet — brand_colors.json (Playwright) heeft voorrang op briefing
    brand_css = ""
    if collected_path:
        from color_utils import extract_colors_from_briefing, generate_palette, palette_to_css
        primary = secondary = None

        # 1. Probeer brand_colors.json (Playwright computed styles — betrouwbaarder)
        brand_colors_file = collected_path / "brand_colors.json"
        if brand_colors_file.exists():
            try:
                bc = json.loads(brand_colors_file.read_text(encoding="utf-8"))
                primary   = bc.get("primary")
                secondary = bc.get("secondary")
                if primary:
                    log(f"[OK]  globals.css: brand-kleur uit Playwright: {primary}")
            except Exception as e:
                log(f"[WARN] brand_colors.json lezen mislukt: {e}")

        # 2. Fallback: extraheer uit briefing.md
        if not primary:
            briefing_file = collected_path / "briefing.md"
            if briefing_file.exists():
                try:
                    brief = briefing_file.read_text(encoding="utf-8", errors="ignore")
                    primary, secondary = extract_colors_from_briefing(brief)
                    if primary:
                        log(f"[OK]  globals.css: kleur uit briefing: {primary}")
                except Exception as e:
                    log(f"[WARN] kleurenpalet genereren mislukt: {e}")

        if primary:
            try:
                palette   = generate_palette(primary, secondary)
                brand_css = "\n" + palette_to_css(palette)
            except Exception as e:
                log(f"[WARN] palette genereren mislukt: {e}")

    header = '@import "tailwindcss";\n@plugin "daisyui";\n@plugin "@tailwindcss/typography";\n@plugin "@tailwindcss/forms";\n\n'

    if has_v3 and not has_import:
        css = re.sub(r'@tailwind\s+\w+;\s*\n?', '', css)
        css = header + css.lstrip() + brand_css
        css_path.write_text(css, encoding="utf-8")
        log("[OK]  globals.css: v3→v4 + DaisyUI + merkkleur")
    elif not has_v3 and not has_import:
        css_path.write_text(header + css + brand_css, encoding="utf-8")
        log("[OK]  globals.css: Tailwind v4 + DaisyUI + merkkleur")
    elif "daisyui" not in css:
        css = css.replace('@import "tailwindcss";',
                          '@import "tailwindcss";\n@plugin "daisyui";')
        css_path.write_text(css + brand_css, encoding="utf-8")
        log("[OK]  globals.css: DaisyUI + merkkleur toegevoegd")


def _create_ui_components(project_dir: Path) -> None:
    """Schrijf essentiële shadcn-compatibele UI-componenten direct naar src/components/ui/."""
    src_dir = project_dir / "src"
    lib_dir = src_dir / "lib"
    ui_dir  = src_dir / "components" / "ui"
    lib_dir.mkdir(parents=True, exist_ok=True)
    ui_dir.mkdir(parents=True, exist_ok=True)

    (lib_dir / "utils.ts").write_text(
        'import { type ClassValue, clsx } from "clsx";\n'
        'import { twMerge } from "tailwind-merge";\n'
        'export function cn(...inputs: ClassValue[]) { return twMerge(clsx(inputs)); }\n',
        encoding="utf-8",
    )

    (ui_dir / "button.tsx").write_text(
        '"use client";\n'
        'import * as React from "react";\n'
        'import { Slot } from "@radix-ui/react-slot";\n'
        'import { cva, type VariantProps } from "class-variance-authority";\n'
        'import { cn } from "@/lib/utils";\n\n'
        'const buttonVariants = cva(\n'
        '  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 disabled:pointer-events-none disabled:opacity-50",\n'
        '  { variants: { variant: {\n'
        '      default: "bg-primary text-primary-foreground shadow hover:bg-primary/90",\n'
        '      outline: "border border-input bg-background hover:bg-accent hover:text-accent-foreground",\n'
        '      ghost: "hover:bg-accent hover:text-accent-foreground",\n'
        '      link: "text-primary underline-offset-4 hover:underline",\n'
        '    }, size: { default: "h-9 px-4 py-2", sm: "h-8 rounded-md px-3 text-xs", lg: "h-10 rounded-md px-8", icon: "h-9 w-9" },\n'
        '  }, defaultVariants: { variant: "default", size: "default" } }\n'
        ');\n\n'
        'export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> { asChild?: boolean; }\n'
        'const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild = false, ...props }, ref) => {\n'
        '  const Comp = asChild ? Slot : "button";\n'
        '  return <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />;\n'
        '});\n'
        'Button.displayName = "Button";\n'
        'export { Button, buttonVariants };\n',
        encoding="utf-8",
    )

    (ui_dir / "card.tsx").write_text(
        'import * as React from "react";\nimport { cn } from "@/lib/utils";\n\n'
        'const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(({ className, ...props }, ref) => (\n'
        '  <div ref={ref} className={cn("rounded-xl border bg-card text-card-foreground shadow", className)} {...props} />\n));\n'
        'Card.displayName = "Card";\n\n'
        'const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(({ className, ...props }, ref) => (\n'
        '  <div ref={ref} className={cn("flex flex-col space-y-1.5 p-6", className)} {...props} />\n));\n'
        'CardHeader.displayName = "CardHeader";\n\n'
        'const CardTitle = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(({ className, ...props }, ref) => (\n'
        '  <div ref={ref} className={cn("font-semibold leading-none tracking-tight", className)} {...props} />\n));\n'
        'CardTitle.displayName = "CardTitle";\n\n'
        'const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(({ className, ...props }, ref) => (\n'
        '  <div ref={ref} className={cn("p-6 pt-0", className)} {...props} />\n));\n'
        'CardContent.displayName = "CardContent";\n\n'
        'export { Card, CardHeader, CardTitle, CardContent };\n',
        encoding="utf-8",
    )

    (ui_dir / "badge.tsx").write_text(
        'import * as React from "react";\nimport { cva, type VariantProps } from "class-variance-authority";\nimport { cn } from "@/lib/utils";\n\n'
        'const badgeVariants = cva("inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold transition-colors",\n'
        '  { variants: { variant: {\n'
        '    default: "border-transparent bg-primary text-primary-foreground shadow hover:bg-primary/80",\n'
        '    secondary: "border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80",\n'
        '    outline: "text-foreground",\n'
        '  }}, defaultVariants: { variant: "default" }}\n'
        ');\n\n'
        'export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}\n'
        'function Badge({ className, variant, ...props }: BadgeProps) {\n'
        '  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;\n'
        '}\nexport { Badge, badgeVariants };\n',
        encoding="utf-8",
    )

    (ui_dir / "accordion.tsx").write_text(
        '"use client";\n'
        'import * as React from "react";\n'
        'import * as AccordionPrimitive from "@radix-ui/react-accordion";\n'
        'import { ChevronDown } from "lucide-react";\n'
        'import { cn } from "@/lib/utils";\n\n'
        'const Accordion = AccordionPrimitive.Root;\n\n'
        'const AccordionItem = React.forwardRef<React.ElementRef<typeof AccordionPrimitive.Item>, React.ComponentPropsWithoutRef<typeof AccordionPrimitive.Item>>(({ className, ...props }, ref) => (\n'
        '  <AccordionPrimitive.Item ref={ref} className={cn("border-b", className)} {...props} />\n));\n'
        'AccordionItem.displayName = "AccordionItem";\n\n'
        'const AccordionTrigger = React.forwardRef<React.ElementRef<typeof AccordionPrimitive.Trigger>, React.ComponentPropsWithoutRef<typeof AccordionPrimitive.Trigger>>(({ className, children, ...props }, ref) => (\n'
        '  <AccordionPrimitive.Header className="flex">\n'
        '    <AccordionPrimitive.Trigger ref={ref} className={cn("flex flex-1 items-center justify-between py-4 text-sm font-medium transition-all hover:underline [&[data-state=open]>svg]:rotate-180", className)} {...props}>\n'
        '      {children}\n'
        '      <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200" />\n'
        '    </AccordionPrimitive.Trigger>\n'
        '  </AccordionPrimitive.Header>\n));\n'
        'AccordionTrigger.displayName = AccordionPrimitive.Trigger.displayName;\n\n'
        'const AccordionContent = React.forwardRef<React.ElementRef<typeof AccordionPrimitive.Content>, React.ComponentPropsWithoutRef<typeof AccordionPrimitive.Content>>(({ className, children, ...props }, ref) => (\n'
        '  <AccordionPrimitive.Content ref={ref} className="overflow-hidden text-sm data-[state=closed]:animate-accordion-close data-[state=open]:animate-accordion-open" {...props}>\n'
        '    <div className={cn("pb-4 pt-0", className)}>{children}</div>\n'
        '  </AccordionPrimitive.Content>\n));\n'
        'AccordionContent.displayName = AccordionPrimitive.Content.displayName;\n\n'
        'export { Accordion, AccordionItem, AccordionTrigger, AccordionContent };\n',
        encoding="utf-8",
    )

    (ui_dir / "separator.tsx").write_text(
        '"use client";\n'
        'import * as React from "react";\n'
        'import * as SeparatorPrimitive from "@radix-ui/react-separator";\n'
        'import { cn } from "@/lib/utils";\n\n'
        'const Separator = React.forwardRef<React.ElementRef<typeof SeparatorPrimitive.Root>, React.ComponentPropsWithoutRef<typeof SeparatorPrimitive.Root>>(\n'
        '  ({ className, orientation = "horizontal", decorative = true, ...props }, ref) => (\n'
        '    <SeparatorPrimitive.Root ref={ref} decorative={decorative} orientation={orientation}\n'
        '      className={cn("shrink-0 bg-border", orientation === "horizontal" ? "h-[1px] w-full" : "h-full w-[1px]", className)} {...props} />\n'
        '  )\n);\n'
        'Separator.displayName = SeparatorPrimitive.Root.displayName;\n'
        'export { Separator };\n',
        encoding="utf-8",
    )

    (ui_dir / "sheet.tsx").write_text(
        '"use client";\n'
        'import * as React from "react";\n'
        'import { createPortal } from "react-dom";\n'
        'import { Slot } from "@radix-ui/react-slot";\n'
        'import { X } from "lucide-react";\n'
        'import { cn } from "@/lib/utils";\n\n'
        'const SheetContext = React.createContext<{\n'
        '  open: boolean;\n'
        '  onOpen?: () => void;\n'
        '  onClose?: () => void;\n'
        '}>({ open: false });\n\n'
        'interface SheetProps { open?: boolean; onOpenChange?: (open: boolean) => void; children?: React.ReactNode; }\n'
        'const Sheet = ({ open = false, onOpenChange, children }: SheetProps) => (\n'
        '  <SheetContext.Provider value={{\n'
        '    open,\n'
        '    onOpen: () => onOpenChange?.(true),\n'
        '    onClose: () => onOpenChange?.(false),\n'
        '  }}>\n'
        '    {children}\n'
        '  </SheetContext.Provider>\n'
        ');\n\n'
        'interface SheetTriggerProps extends React.ButtonHTMLAttributes<HTMLButtonElement> { asChild?: boolean; }\n'
        'const SheetTrigger = React.forwardRef<HTMLButtonElement, SheetTriggerProps>(({ asChild, className, onClick, ...props }, ref) => {\n'
        '  const { onOpen } = React.useContext(SheetContext);\n'
        '  const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => { onOpen?.(); onClick?.(e); };\n'
        '  const Comp = asChild ? Slot : "button";\n'
        '  return <Comp ref={ref as any} className={cn("", className)} onClick={handleClick} {...props} />;\n'
        '});\n'
        'SheetTrigger.displayName = "SheetTrigger";\n\n'
        'interface SheetContentProps extends React.HTMLAttributes<HTMLDivElement> { side?: "left" | "right"; onClose?: () => void; }\n'
        'const SheetContent = React.forwardRef<HTMLDivElement, SheetContentProps>(({ className, children, side = "right", onClose, ...props }, ref) => {\n'
        '  const { open, onClose: ctxClose } = React.useContext(SheetContext);\n'
        '  const handleClose = onClose ?? ctxClose;\n'
        '  const [mounted, setMounted] = React.useState(false);\n'
        '  React.useEffect(() => { setMounted(true); }, []);\n'
        '  if (!open || !mounted) return null;\n'
        '  // Portal naar document.body zodat ancestor backdrop-blur/transform geen\n'
        '  // containing block maakt die de fixed positionering breekt.\n'
        '  return createPortal(\n'
        '    <>\n'
        '      <div className="fixed inset-0 z-40 bg-black/50" onClick={handleClose} />\n'
        '      <div ref={ref} className={cn("fixed inset-y-0 z-50 flex flex-col bg-background shadow-xl w-3/4 max-w-sm", side === "right" ? "right-0" : "left-0", className)} {...props}>\n'
        '        <button onClick={handleClose} className="absolute right-4 top-4 opacity-70 hover:opacity-100"><X className="h-4 w-4" /></button>\n'
        '        {children}\n'
        '      </div>\n'
        '    </>,\n'
        '    document.body,\n'
        '  );\n'
        '});\n'
        'SheetContent.displayName = "SheetContent";\n\n'
        'const SheetHeader = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (\n'
        '  <div className={cn("flex flex-col space-y-2 p-6", className)} {...props} />\n);\n\n'
        'export { Sheet, SheetTrigger, SheetContent, SheetHeader };\n',
        encoding="utf-8",
    )

    # ── Embla Carousel component ──────────────────────────────────────────────
    (src_dir / "components" / "GalleryCarousel.tsx").write_text(
        '"use client";\n'
        'import useEmblaCarousel from "embla-carousel-react";\n'
        'import Autoplay from "embla-carousel-autoplay";\n\n'
        'interface Props { images: { src: string; alt?: string }[] }\n\n'
        'export default function GalleryCarousel({ images }: Props) {\n'
        '  const [emblaRef] = useEmblaCarousel({ loop: true }, [Autoplay({ delay: 3500 })]);\n'
        '  return (\n'
        '    <div className="overflow-hidden rounded-xl" ref={emblaRef}>\n'
        '      <div className="flex">\n'
        '        {images.map((img, i) => (\n'
        '          <div key={i} className="flex-none w-full md:w-1/2 lg:w-1/3 pl-3 first:pl-0">\n'
        '            <img src={img.src} alt={img.alt ?? ""} className="w-full aspect-square object-cover rounded-lg" />\n'
        '          </div>\n'
        '        ))}\n'
        '      </div>\n'
        '    </div>\n'
        '  );\n'
        '}\n',
        encoding="utf-8",
    )

    # ── Leaflet Map component (dynamic import — geen SSR) ─────────────────────
    (src_dir / "components" / "LeafletMap.tsx").write_text(
        '"use client";\n'
        'import { useEffect, useRef } from "react";\n\n'
        'interface Props { address: string; lat?: number; lng?: number; zoom?: number }\n\n'
        'export default function LeafletMap({ address, lat = 52.3676, lng = 4.9041, zoom = 15 }: Props) {\n'
        '  const mapRef = useRef<HTMLDivElement>(null);\n'
        '  useEffect(() => {\n'
        '    if (!mapRef.current) return;\n'
        '    import("leaflet").then((L) => {\n'
        '      import("leaflet/dist/leaflet.css");\n'
        '      if ((mapRef.current as any)._leaflet_id) return;\n'
        '      const map = L.map(mapRef.current!, { scrollWheelZoom: false });\n'
        '      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {\n'
        '        attribution: "© OpenStreetMap"\n'
        '      }).addTo(map);\n'
        '      map.setView([lat, lng], zoom);\n'
        '      L.marker([lat, lng]).addTo(map).bindPopup(address);\n'
        '    });\n'
        '  }, [lat, lng, zoom, address]);\n'
        '  return <div ref={mapRef} className="w-full h-64 rounded-xl z-10" />;\n'
        '}\n',
        encoding="utf-8",
    )

    # ── Booking widget (Calendly/Treatwell iframe) ────────────────────────────
    (src_dir / "components" / "BookingWidget.tsx").write_text(
        '"use client";\n'
        'interface Props { url: string; title?: string }\n\n'
        'export default function BookingWidget({ url, title = "Maak een afspraak" }: Props) {\n'
        '  return (\n'
        '    <section className="py-16 bg-base-200">\n'
        '      <div className="container mx-auto px-4 text-center">\n'
        '        <h2 className="font-heading text-3xl font-bold mb-6">{title}</h2>\n'
        '        <div className="max-w-3xl mx-auto rounded-xl overflow-hidden shadow-xl">\n'
        '          <iframe src={url} width="100%" height="700" frameBorder="0"\n'
        '            className="w-full" title={title} />\n'
        '        </div>\n'
        '      </div>\n'
        '    </section>\n'
        '  );\n'
        '}\n',
        encoding="utf-8",
    )

    log("[OK]  UI + carousel + map + booking componenten aangemaakt")

    # ── next-sitemap config ───────────────────────────────────────────────────
    (project_dir / "next-sitemap.config.js").write_text(
        '/** @type {import("next-sitemap").IConfig} */\n'
        'module.exports = {\n'
        '  siteUrl: process.env.SITE_URL || "https://example.com",\n'
        '  generateRobotsTxt: true,\n'
        '  outDir: "out",\n'
        '  trailingSlash: true,\n'
        '};\n',
        encoding="utf-8",
    )

    # Voeg postbuild script toe aan package.json
    pkg_path = project_dir / "package.json"
    if pkg_path.exists():
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
            pkg.setdefault("scripts", {})["postbuild"] = "next-sitemap"
            pkg_path.write_text(json.dumps(pkg, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


def step_build_nextjs(project_dir: Path, n: int, total: int, prospect: str) -> bool:
    """Run TypeScript check + npm run build in de Next.js projectdirectory."""
    log(f"\n{'─' * 60}")
    log(f"[STAP {n}/{total}] build_nextjs")
    log(f"{'─' * 60}")
    write_status(running=True, prospect=prospect, step="build_nextjs", step_n=n, total=total)

    # Verwijder basePath voor de build — anders genereert Next.js alleen index.html
    # en RSC .txt voor subpagina's i.p.v. volledige HTML-export.
    slug = project_dir.name.replace("-next", "")
    _write_nextjs_config(project_dir, slug, for_deploy=True)

    # TypeScript pre-check: vang fouten vroeg op zonder volledige build
    ts_proc = subprocess.run(
        ["npx", "tsc", "--noEmit", "--skipLibCheck"],
        cwd=str(project_dir), capture_output=True, text=True,
    )
    if ts_proc.returncode != 0:
        ts_errors = ts_proc.stdout + ts_proc.stderr
        log("[WARN] TypeScript fouten gevonden — probeer auto-fix voor build")
        for line in ts_errors.splitlines()[:10]:
            log(f"  {line}")
        # _fix_lucide_icons al gedaan, maar run nog een keer voor zekerheid
        _fix_lucide_icons(project_dir)
        # Fix invalid inline CSS properties (bijv. divideColor)
        _fix_invalid_style_props(project_dir)

    cmd = ["npm", "run", "build"]
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
        log(f"[WARN] build_nextjs mislukt — probeer auto-fixes en bouw opnieuw")
        _fix_lucide_icons(project_dir)
        _fix_invalid_style_props(project_dir)
        _fix_layout_tsx(project_dir)

        proc2 = subprocess.Popen(
            cmd, cwd=str(project_dir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        for line in iter(proc2.stdout.readline, ""):
            log(line.rstrip("\n"))
        proc2.wait()
        ok = proc2.returncode == 0
        if ok:
            log("[OK]  build_nextjs geslaagd na auto-fix")
        else:
            log(f"[FAIL] build_nextjs mislukt ook na auto-fix (exit {proc2.returncode})")

    # Geen URL-rewrite: dashboard server redirect /<path>/ → /sites/<slug>/<path>/
    # via Referer header (anders breekt React hydration door URL-mismatch).

    log(f"[INFO] Duur: {_fmt_duration(duration)}")
    return ok


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
    logo_path: str = "",
    no_backend: bool = False,
) -> tuple[bool, str, list[str]]:
    """Genereer + parse één unit in een thread. Returnt (ok, label, lines)."""
    label = page_slug or unit_key
    lines = [f"\n{'─' * 60}", f"[UNIT] {label}", f"{'─' * 60}"]

    # Smart resume: skip alle units waarvan de output al bestaat. Dat geldt voor
    # page-units (eigen subdir/page.tsx) maar ook voor layout en home, omdat
    # regenerate van die twee bij usage-druk soms een andere format produceert
    # (zonder ===FILE: markers) waardoor de parser faalt en we juist iets
    # werkends overschrijven met niets. Override met FORCE_REGEN=true.
    #
    # BELANGRIJK: niet skippen als het bestand de Next.js scaffold-default is
    # (dat is het geval na een verse scaffold maar voor de home-unit gedraaid
    # heeft). Detect via signature-strings die alleen in de boilerplate staan.
    SCAFFOLD_MARKERS = (
        "To get started, edit the page.tsx",
        '"/next.svg"',
        "Vercel Logo",
        "alt=\"Next.js logo\"",
        "Create Next App",                 # default layout.tsx title
        "Generated by create next app",    # default layout.tsx description
        "Geist({",                          # default font in scaffold layout
        "Geist_Mono({",
    )
    force_regen = os.getenv("FORCE_REGEN", "").lower() in ("true", "1", "yes")
    if not force_regen:
        check_tsx = None
        if unit_key == "page" and page_slug:
            check_tsx = project_dir / "src" / "app" / page_slug / "page.tsx"
        elif unit_key == "home":
            check_tsx = project_dir / "src" / "app" / "page.tsx"
        elif unit_key == "layout":
            check_tsx = project_dir / "src" / "app" / "layout.tsx"
        if check_tsx and check_tsx.exists() and check_tsx.stat().st_size > 500:
            try:
                content = check_tsx.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                content = ""
            is_scaffold = any(m in content for m in SCAFFOLD_MARKERS)
            if not is_scaffold:
                lines.append(f"[SKIP] {label}: bestaat al ({check_tsx.stat().st_size} bytes)")
                return True, label, lines
            lines.append(f"[INFO] {label}: bestaande TSX is scaffold-default, regenereren")

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
    if logo_path:
        gen_cmd += ["--logo-path", logo_path]
    if no_backend:
        gen_cmd += ["--no-backend"]

    MAX_RETRIES = 2
    for attempt in range(1, MAX_RETRIES + 2):
        _t0 = time.monotonic()
        log(f"[INFO] {label}: Claude aan het genereren...")
        proc = subprocess.run(gen_cmd, cwd=str(SCRIPTS_DIR), capture_output=True, text=True)
        elapsed_total = int(time.monotonic() - _t0)

        lines.extend(proc.stdout.splitlines())
        if proc.stderr.strip():
            lines.append(f"[STDERR] {proc.stderr.strip()[:500]}")

        if proc.returncode == 0:
            # Toon eerste zinvolle gegenereerde regel als preview
            preview = ""
            for l in proc.stdout.splitlines():
                stripped = l.strip()
                if stripped.startswith(("export ", "const ", "function ", "import ", "<")):
                    preview = stripped[:80]
                    break
            lines.append(
                f"[OK]  {label}: {elapsed_total}s"
                + (f" — {preview}..." if preview else "")
            )
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


def step_polish_site(site_dir: Path, company_name: str, n: int, total: int,
                     prospect: str, collected_path: Path | None = None) -> bool:
    cmd = [
        "python", str(SCRIPTS_DIR / "polish_site.py"),
        "--site-dir", str(site_dir),
        "--company",  company_name,
    ]
    if collected_path:
        cmd += ["--collected-path", str(collected_path)]
    return run_cmd(cmd, "polish_site", n, total, prospect)


def step_generate_mail(name: str, n: int, total: int, prospect: str) -> bool:
    cmd = [
        "python", str(SCRIPTS_DIR / "generate_mail.py"),
        "--name", name,
    ]
    return run_cmd(cmd, "generate_mail", n, total, prospect)


def _take_new_screenshot(site_dir: Path, out_path: Path) -> None:
    """Maak een Playwright screenshot van de gegenereerde homepage."""
    import socket as _socket
    with _socket.socket() as s:
        s.bind(("", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen(
        ["python3", "-m", "http.server", str(port), "--directory", str(site_dir)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(0.8)
    try:
        script = (
            "from playwright.sync_api import sync_playwright\n"
            "with sync_playwright() as p:\n"
            "    b=p.chromium.launch()\n"
            f"    pg=b.new_page(viewport={{'width':1280,'height':720}})\n"
            f"    pg.goto('http://127.0.0.1:{port}/',wait_until='domcontentloaded',timeout=15000)\n"
            "    pg.wait_for_timeout(1500)\n"
            f"    pg.screenshot(path='{str(out_path)}')\n"
            "    b.close()\n"
        )
        res = subprocess.run(["python3", "-c", script], capture_output=True, text=True, timeout=60)
        if res.returncode == 0:
            log(f"[OK]  Nieuwe-site screenshot: {out_path.name}")
        else:
            log(f"[WARN] Screenshot mislukt: {res.stderr[:200]}")
    except Exception as e:
        log(f"[WARN] Screenshot mislukt: {e}")
    finally:
        server.terminate()


def step_inject_paywall(site_dir: Path, n: int, total: int, prospect: str,
                        original_screenshot: Path | None = None,
                        new_screenshot: Path | None = None) -> bool:
    cmd = [
        "python", str(SCRIPTS_DIR / "inject_paywall.py"),
        "--site-dir", str(site_dir),
    ]
    if original_screenshot and original_screenshot.exists():
        cmd += ["--original-screenshot", str(original_screenshot)]
    if new_screenshot and new_screenshot.exists():
        cmd += ["--new-screenshot", str(new_screenshot)]
    return run_cmd(cmd, "inject_paywall", n, total, prospect)


def step_fetch_stock_photos(data_slug: str, company_name: str,
                            n: int, total: int, prospect: str) -> bool:
    cmd = [
        "python", str(SCRIPTS_DIR / "fetch_stock_photos.py"),
        "--slug",    data_slug,
        "--company", company_name,
    ]
    return run_cmd(cmd, "fetch_stock_photos", n, total, prospect)


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


# _extract_header_footer en collect_all_html_for_ref verwijderd:
# waren bedoeld voor HTML-pipeline (subpagina's kregen homepage-header als referentie).
# In Next.js is de layout gedeeld via src/app/layout.tsx — niet nodig meer.


# ── API-kostenschatting ─────────────────────────────────────────────���─────────

_PRICE     = {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30}
_USD_TO_EUR = 0.92


def _log_api_cost_estimate(collected_path: Path, project_dir: Path) -> None:
    """Lees alle *.meta.json en bereken geschatte API-kosten voor deze run."""
    slug_prefix = project_dir.name.replace("-next", "")
    all_meta = (
        list(Path("/workspace/output").glob(f"{slug_prefix}*.meta.json"))
        + list(collected_path.glob("*.meta.json"))
    )

    input_tok = output_tok = cache_write = cache_read = 0
    for mf in all_meta:
        try:
            u = json.loads(mf.read_text(encoding="utf-8")).get("usage") or {}
            input_tok   += u.get("input_tokens",                0) or 0
            output_tok  += u.get("output_tokens",               0) or 0
            cache_write += u.get("cache_creation_input_tokens", 0) or 0
            cache_read  += u.get("cache_read_input_tokens",     0) or 0
        except Exception:
            pass

    if not (input_tok or output_tok):
        return

    cost_usd = (
        input_tok   / 1_000_000 * _PRICE["input"]
        + output_tok  / 1_000_000 * _PRICE["output"]
        + cache_write / 1_000_000 * _PRICE["cache_write"]
        + cache_read  / 1_000_000 * _PRICE["cache_read"]
    )
    cost_eur = cost_usd * _USD_TO_EUR
    ok = cost_eur < 0.30

    log(f"\n{'─' * 60}")
    log(f"[INFO] API-kostenschatting (claude-sonnet-4-6):")
    log(f"       Input:       {input_tok:>8,} tokens  × $3.00/M  = ${input_tok/1e6*3:.4f}")
    log(f"       Output:      {output_tok:>8,} tokens  × $15.00/M = ${output_tok/1e6*15:.4f}")
    if cache_write:
        log(f"       Cache write: {cache_write:>8,} tokens  × $3.75/M  = ${cache_write/1e6*3.75:.4f}")
    if cache_read:
        log(f"       Cache read:  {cache_read:>8,} tokens  × $0.30/M  = ${cache_read/1e6*0.30:.4f}")
    log(f"       {'─' * 42}")
    log(f"       Totaal:  ${cost_usd:.4f}  ≈  €{cost_eur:.4f}")
    tag = "[OK] " if ok else "[WARN]"
    log(f"       {tag} {'Onder' if ok else 'BOVEN'} €0.30 limiet  (€{cost_eur:.3f} / site)")
    log(f"{'─' * 60}\n")


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
    parser.add_argument("--homepage-only", action="store_true",
                        help="Genereer alleen de homepage + paywall (geen subpagina's, geen research)")
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

    global _step_timings, _pipeline_start, RUN_LOG_FILE
    _step_timings  = []
    _pipeline_start = time.monotonic()
    RUN_LOG_FILE = Path("/workspace/data/run_logs") / f"{slug}.log"
    RUN_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    RUN_LOG_FILE.write_text("", encoding="utf-8")

    # Voorlopige schatting van total (wordt bijgewerkt na discover_pages)
    total = 10
    n     = 0

    log(f"{'═' * 60}")
    log(f"[START] Pipeline: {company_name}")
    log(f"[INFO]  Slug:       {slug}")
    log(f"[INFO]  Vanaf stap: {args.from_step}")
    log(f"[INFO]  Site-map:   {site_dir}")
    log(f"[INFO]  Run-log:    {RUN_LOG_FILE}")
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
        if args.homepage_only:
            log("[INFO] research: overgeslagen (--homepage-only mode — bespaar ~€0.10)")
        elif prospect.get("research_status") == "done" and not args.force:
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

    # ── inventory + quality gate ──────────────────────────────────────────────
    # Deterministische extractie van bron-feiten (prijzen, contact, signatures,
    # pages_content). Drives plan_sections + generate_site + content-validatie.
    # Gebruik de data-dir naam als slug (collect.py slugifyt op domain).
    data_slug = collected_path.name
    n += 1
    if not step_inventory(data_slug, n, total, company_name):
        log("[WARN] inventory mislukt — pipeline gaat door zonder inventory.json")
    n += 1
    if not step_quality_gate(data_slug, n, total, company_name):
        log("[WARN] quality_gate gaf fails — pipeline gaat door, controleer quality_report.json")

    # ── generate ──────────────────────────────────────────────────────────────
    if from_idx <= STEPS.index("generate"):
        # Next.js projectmap (broncode) + output (statische export)
        project_dir = OUTPUT_DIR / f"{slug}-next"
        site_dir    = project_dir  # override: site_dir wijst nu naar Next.js project
        out_dir     = project_dir / "out"  # statische export na build

        image_manifest = save_image_manifest(collected_path)

        # Stockfoto's ophalen als er te weinig afbeeldingen zijn (vereist PEXELS_API_KEY)
        if os.getenv("PEXELS_API_KEY"):
            n += 1
            step_fetch_stock_photos(data_slug, company_name, n, total, company_name)
            image_manifest = save_image_manifest(collected_path)  # hermaak manifest met stockfoto's
        else:
            log("[INFO] PEXELS_API_KEY niet ingesteld — stockfoto-fallback overgeslagen")

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

        if args.homepage_only:
            # nav_routes uit pages.json (niet uit gen_units) zodat de header
            # alle subpagina's toont, ook al worden ze niet gegenereerd.
            nav_routes = [""] + [p["file"].replace(".html", "") for p in pages]
            page_units = []
            log(f"[INFO] homepage-only | nav-routes: {nav_routes}")
        else:
            nav_routes = [""] + [u[2] for u in gen_units if u[0] == "page"]
            page_units = [u for u in gen_units if u[0] == "page"]
            log(f"[INFO] Next.js | {len(page_units)} subpagina's | nav-routes: {nav_routes}")

        layout_unit = [u for u in gen_units if u[0] == "layout"]
        home_unit   = [u for u in gen_units if u[0] == "home"]

        total = 6 + 1 + 1 + 1 + len(page_units) + 1 + 1  # scaffold+layout+home+pages+build+validate

        # ── Stap 0: scaffold ─────────────────────────────────────────────────
        n += 1
        if not step_scaffold_nextjs(project_dir, slug, n, total, company_name):
            write_status(running=False, prospect=company_name, step="scaffold", result="failed")
            sys.exit(1)

        # Kopieer afbeeldingen naar public/assets/
        copy_images_to_site(collected_path, project_dir / "public")

        # Logo kopiëren naar public/logo.* (bekende locatie voor layout-prompt)
        logo_src = None
        sd_path  = collected_path / "structured_data.json"
        if sd_path.exists():
            try:
                sd = json.loads(sd_path.read_text(encoding="utf-8"))
                logo_rel = sd.get("logo")
                if logo_rel:
                    logo_src = collected_path / logo_rel
            except Exception:
                pass
        if logo_src and logo_src.exists():
            logo_dest = project_dir / "public" / f"logo{logo_src.suffix}"
            import shutil as _shutil
            _shutil.copy2(str(logo_src), str(logo_dest))
            log(f"[OK]  Logo gekopieerd naar public/logo{logo_src.suffix}")

        # Detecteer logo-pad voor layout-prompt
        logo_path = ""
        for ext in (".png", ".svg", ".jpg", ".jpeg", ".webp", ".gif"):
            if (project_dir / "public" / f"logo{ext}").exists():
                logo_path = f"logo{ext}"
                log(f"[INFO] Logo gevonden: {logo_path}")
                break

        # ── Fase 1: layout (header + footer + globals) ───────────────────────
        n += 1
        write_status(running=True, prospect=company_name, step="generate:layout", step_n=n, total=total)
        ok, label, lines = _run_unit_buffered(
            briefing_path, company_name, "layout",
            OUTPUT_DIR / f"{slug}-layout.txt",
            None, "", "", "",
            image_manifest, nav_routes, project_dir,
            logo_path=logo_path,
        )
        for line in lines:
            log(line)
        if not ok:
            write_status(running=False, prospect=company_name, step="generate:layout", result="failed")
            sys.exit(1)

        # Patch globals.css na layout-generatie (Tailwind v3→v4 + DaisyUI + merkkleur)
        _fix_globals_css(project_dir, collected_path)

        # Fix hallucinated Lucide icons die niet bestaan
        _fix_lucide_icons(project_dir)

        # Fix veelvoorkomende layout.tsx syntax-problemen
        _fix_layout_tsx(project_dir)

        # Patch data-theme in layout.tsx op basis van het gekozen DaisyUI-theme
        _patch_daisyui_theme(project_dir, collected_path)

        # ── Fase 2: homepage — vrije TSX-generatie door Claude ───────────────
        n += 1
        write_status(running=True, prospect=company_name, step="generate:home", step_n=n, total=total)
        ok, label, lines = _run_unit_buffered(
            briefing_path, company_name, "home",
            OUTPUT_DIR / f"{slug}-home.txt",
            None, "", "", "",
            image_manifest, nav_routes, project_dir,
            no_backend=args.homepage_only,
        )
        for line in lines:
            log(line)
        if not ok:
            write_status(running=False, prospect=company_name, step="generate:home", result="failed")
            sys.exit(1)

        # Homepage TSX als referentie voor subpagina's
        home_tsx = project_dir / "src" / "app" / "page.tsx"
        ref_tsx  = home_tsx if home_tsx.exists() else None

        # ── Fase 3: subpagina's parallel — vrije TSX-generatie door Claude ───
        failed_units: list[str] = []
        if not page_units:
            log("[INFO] generate:parallel overgeslagen (homepage-only mode)")
        else:
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

        # Nog een keer Lucide icons fixen, pages worden na layout gegenereerd
        _fix_lucide_icons(project_dir)
        _fix_page_function_names(project_dir)
        _fix_daisyui_colors(project_dir)
        _fix_icon_as_text(project_dir)
        _fix_nav_spacing(project_dir)
        _fix_client_components(project_dir)
        _fix_header_pathname(project_dir)
        _strip_em_dashes(project_dir)

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

        # next-sitemap: genereer sitemap.xml en robots.txt
        # Gebruik de prospect-URL als siteUrl (beter dan example.com)
        site_url = prospect.get("url", "").rstrip("/")
        site_url_file = collected_path / "site_url.txt"
        if site_url_file.exists():
            site_url = site_url_file.read_text(encoding="utf-8").strip() or site_url
        if site_url:
            env = {**os.environ, "SITE_URL": site_url}
            sm = subprocess.run(["npx", "next-sitemap"], cwd=str(project_dir),
                                capture_output=True, text=True, env=env)
            if sm.returncode == 0:
                sitemap = out_dir / "sitemap.xml"
                log(f"[OK]  sitemap.xml gegenereerd voor {site_url}" if sitemap.exists() else "[WARN] next-sitemap liep maar sitemap.xml niet gevonden")

    # ── validate_site → repair → re-validate ─────────────────────────────────
    # validate/repair/polish draaien op de /out directory (statische HTML-export)
    validate_dir = out_dir if out_dir.exists() else project_dir

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

        # ── Content-check — kritieke issues blokkeren mark_site_done ──────────
        n += 1
        if args.homepage_only:
            log("[INFO] content_check: overgeslagen (homepage-only mode — geen subpagina's verwacht)")
            content_ok = True
        else:
            content_ok = step_check_content(validate_dir, collected_path, company_name, n, total, company_name)
            if not content_ok:
                log("[WARN] Content-check heeft kritieke issues — auto_repair wordt getriggerd")
                update_prospect(company_name, ready_for_review=False, content_issues=True)

        # ── Screenshot-validatie ───────────────────────────────────────────────
        n += 1
        screenshot_json = validate_dir / "screenshot_validation.json"
        step_screenshot_validate(validate_dir, n, total, company_name)

        # ── Screenshot-repair + hervalidatie ──────────────────────────────────
        if screenshot_json.exists():
            try:
                shot_data = json.loads(screenshot_json.read_text(encoding="utf-8"))
                if shot_data.get("total_issues", 0) > 0:
                    n += 1
                    log(f"[INFO] {shot_data['total_issues']} visuele issue(s) — screenshot-repair...")
                    step_repair_site(validate_dir, json_out, n, total, company_name,
                                     screenshot_json=screenshot_json)
                    # Hervalideer na repair — weet of de fix werkte
                    n += 1
                    log("[INFO] Screenshot hervalidatie na repair...")
                    shot_bak = validate_dir / "screenshot_validation_pre_repair.json"
                    if screenshot_json.exists():
                        import shutil as _sh
                        _sh.copy2(str(screenshot_json), str(shot_bak))
                    step_screenshot_validate(validate_dir, n, total, company_name)
                else:
                    log("[INFO] Geen visuele issues — screenshot-repair overgeslagen")
            except Exception as e:
                log(f"[WARN] Kon screenshot_validation.json niet lezen: {e}")

        # ── Cohesion pass ──────────────────────────────────────────────────────
        n += 1
        if args.homepage_only:
            log("[INFO] cohesion_pass: overgeslagen (homepage-only mode — geen subpagina's)")
        else:
            log("\n[INFO] Cohesion pass uitvoeren...")
            step_cohesion_pass(validate_dir, n, total, company_name)

        # ── Polish ─────────────────────────────────────────────────────────────
        n += 1
        log("\n[INFO] Polish uitvoeren...")
        step_polish_site(validate_dir, company_name, n, total, company_name, collected_path)

        # ── Paywall injecteren (homepage-only mode) ───────────────────────────
        if args.homepage_only:
            n += 1
            log("\n[INFO] Paywall injecteren...")
            # Maak eerst screenshot van de nieuwe site
            new_shot = project_dir / "screenshot_homepage.png"
            if not new_shot.exists():
                _take_new_screenshot(validate_dir, new_shot)
            orig_shot = collected_path / "original_screenshot.png"
            step_inject_paywall(validate_dir, n, total, company_name,
                                original_screenshot=orig_shot,
                                new_screenshot=new_shot)

        # ── Outreach-mail genereren ───────────────────────────────────────────
        n += 1
        log("\n[INFO] Outreach-mail genereren...")
        step_generate_mail(company_name, n, total, company_name)

    # Final-fase: timings + status pas NADAT alles klaar is
    try:
        save_timings(collected_path, company_name)
    except Exception as e:
        log(f"[WARN] Timings opslaan mislukt: {e}")

    # ── API-kostenschatting (geldt ook als Max gebruikt werd — toont wat API zou kosten) ──
    _log_api_cost_estimate(collected_path, OUTPUT_DIR / f"{slug}-next")

    # ── Repair-and-retry loop — max 2 rondes ──────────────────────────────────
    MAX_REPAIR_ROUNDS = 3  # round 1: CSS, round 2: page-regen, round 3: safe fallback

    def _build_quality(vdir: Path, jout: Path) -> dict:
        """Bouw quality_report op basis van alle validatie-outputs."""
        q: dict = {"prospect": company_name, "checks": {}, "ready": True, "blockers": []}
        homepage_only = args.homepage_only

        q["checks"]["build"] = "pass" if (vdir / "index.html").exists() else "fail"
        if q["checks"]["build"] == "fail":
            q["blockers"].append("Build mislukt — geen index.html")

        if jout.exists():
            try:
                vdata = json.loads(jout.read_text(encoding="utf-8"))
                passed = vdata.get("status") == "PASS"
                q["checks"]["validate"] = "pass" if passed else "fail"
                if not passed:
                    fails = [i["message"] for i in vdata.get("issues", []) if i.get("level") == "FAIL"]
                    # Prefix zodat classify_blocker() altijd "validate_failed" herkent
                    q["blockers"].extend(
                        [f"validate_failed: {m}" for m in fails[:3]]
                        or ["validate_failed: validate_generated_site niet geslaagd"]
                    )
            except Exception:
                q["checks"]["validate"] = "unknown"
        else:
            q["checks"]["validate"] = "not_run"

        cv = vdir / "content_validation.json"
        if homepage_only:
            q["checks"]["content"] = "skip"  # subpagina's bestaan niet intentioneel
        elif cv.exists():
            try:
                cvd = json.loads(cv.read_text(encoding="utf-8"))
                q["checks"]["content"] = "pass" if cvd.get("ready") else "fail"
                if cvd.get("critical"):
                    q["blockers"].extend(cvd["critical"])
            except Exception:
                q["checks"]["content"] = "unknown"
        else:
            q["checks"]["content"] = "not_run"
            q["blockers"].append("Content-check niet uitgevoerd")

        sj = vdir / "screenshot_validation.json"
        if sj.exists():
            try:
                sd = json.loads(sj.read_text(encoding="utf-8"))
                issues = sd.get("total_issues", 0)
                q["checks"]["visual"] = "pass" if issues == 0 else "fail"
                q["visual_issues"] = issues
                if issues > 0:
                    all_i = [i for p in sd.get("results", []) for i in p.get("issues", [])]
                    q["blockers"].extend(all_i[:3])
                    if len(all_i) > 3:
                        q["blockers"].append(f"... en {len(all_i) - 3} andere visuele issue(s)")
            except Exception:
                q["checks"]["visual"] = "unknown"
        else:
            q["checks"]["visual"] = "not_run"
            q["blockers"].append("Screenshot-validatie niet uitgevoerd")

        q["ready"] = len(q["blockers"]) == 0
        return q

    # ── Repair-and-retry: max 2 rondes, daarna auto_failed ────────────────────
    from auto_repair import run_repair_cycle

    shot_json    = validate_dir / "screenshot_validation.json"
    image_manifest_path = collected_path / "images.json"

    for repair_round in range(MAX_REPAIR_ROUNDS + 1):
        quality = _build_quality(validate_dir, json_out)

        # Sla quality report op
        try:
            if validate_dir.exists():
                (validate_dir / "quality_report.json").write_text(
                    json.dumps(quality, indent=2, ensure_ascii=False), encoding="utf-8"
                )
        except Exception:
            pass

        if quality["ready"]:
            log(f"[OK]  Quality gate geslaagd (ronde {repair_round}) — site klaar")
            break

        if repair_round >= MAX_REPAIR_ROUNDS:
            log(f"[FAIL] Quality gate na {MAX_REPAIR_ROUNDS} repair-rondes nog steeds niet geslaagd")
            log(f"       Blockers: {quality['blockers'][:3]}")
            break

        log(f"\n[INFO] Auto-repair ronde {repair_round + 1}/{MAX_REPAIR_ROUNDS}")
        log(f"       {len(quality['blockers'])} blocker(s) te repareren")
        update_prospect(company_name, site_status="auto_repairing")
        write_status(running=True, prospect=company_name,
                     step=f"auto_repair_round_{repair_round + 1}", step_n=n, total=total)

        # Nav-routes ophalen voor page-regeneratie
        nav_routes_repair: list[str] = []
        if pages_path.exists():
            try:
                pd = json.loads(pages_path.read_text(encoding="utf-8"))
                nav_routes_repair = [""] + [p["file"].replace(".html", "") for p in pd.get("pages", [])]
            except Exception:
                pass

        repair_result = run_repair_cycle(
            blockers=quality["blockers"],
            site_dir=validate_dir,
            collected_path=collected_path,
            project_dir=project_dir,
            briefing_path=briefing_path,
            company_name=company_name,
            image_manifest=image_manifest_path if image_manifest_path.exists() else None,
            nav_routes=nav_routes_repair,
            round_num=repair_round + 1,
            screenshot_json=shot_json if shot_json.exists() else None,
            log_fn=log,
        )
        actions_done = len([a for a in repair_result["actions"] if a["ok"]])
        log(f"[INFO] Repair ronde {repair_round + 1}: {actions_done}/{len(repair_result['actions'])} actie(s) geslaagd")

        # ── Correcte hervalidatie-volgorde ────────────────────────────────────
        # 1. Rebuild als TSX gewijzigd (tsx_regen) — EERST, anders stale /out
        if repair_result["needs_rebuild"]:
            n += 1
            log("[INFO] Rebuild na TSX-wijziging...")
            if not step_build_nextjs(project_dir, n, total, company_name):
                log("[WARN] Rebuild mislukt na repair — quality check op huidige /out")

        # 2. Validate site opnieuw (leest /out na eventuele rebuild)
        n += 1
        step_validate_site(validate_dir, json_out, n, total, company_name)

        # 3. Content-check opnieuw (kijkt naar /out na rebuild)
        n += 1
        step_check_content(validate_dir, collected_path, company_name, n, total, company_name)

        # 4. Screenshot opnieuw als visuele actie was ondernomen
        if repair_result["needs_screenshot"]:
            n += 1
            log("[INFO] Screenshot hervalidatie na visuele repair...")
            step_screenshot_validate(validate_dir, n, total, company_name)

    # ── Definitieve status ─────────────────────────────────────────────────────
    ready = quality["ready"]
    if ready:
        mark_site_done(company_name, homepage_only=args.homepage_only)
        update_prospect(company_name, review_status="ready")
        log("[OK]  Pipeline voltooid — site verkoopbaar")
    else:
        log("[FAIL] Site niet automatisch herstelbaar — auto_failed")
        update_prospect(
            company_name,
            status="collected",
            site_status="auto_failed",
            review_status="auto_failed",
        )

    write_status(running=False, prospect=company_name, step="done",
                 result="ready" if ready else "auto_failed")

    log(f"\n{'═' * 60}")
    log(f"[OK] Pipeline voltooid voor: {company_name}")
    log(f"[OK] Site: {site_dir}")
    log(f"{'═' * 60}")


if __name__ == "__main__":
    main()
