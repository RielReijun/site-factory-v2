"""
auto_repair.py — Automatische herstelacties voor pipeline-blockers.

Flow per ronde:
  classify_blockers() → repair_blockers() → rebuild → quality_check

Blocker-types en acties:
  content_missing  → inject uit facts.json in HTML
  missing_page     → regenereer ontbrekende pagina
  visual_issue     → CSS-repair → page-regeneratie → safe-template fallback
  build_failed     → TS-autofix → regenereer mislukt bestand
  validate_failed  → HTML-direct repair
  unknown          → log + skip (build liever dan crashen)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


SCRIPTS_DIR = Path("/workspace/scripts")


# ── Classifier ────────────────────────────────────────────────────────────────

def classify_blocker(blocker: str) -> dict:
    """Geeft {type, details, page} terug voor één blocker-string."""
    b = blocker.lower()

    if "niet gevonden in de gegenereerde site" in b:
        # "Telefoonnummer '...' niet gevonden" / "Bedrijfsnaam '...' niet gevonden"
        if "telefoon" in b:
            return {"type": "content_missing", "field": "phone", "details": blocker}
        if "e-mail" in b or "email" in b:
            return {"type": "content_missing", "field": "email", "details": blocker}
        if "bedrijfsnaam" in b:
            return {"type": "content_missing", "field": "name", "details": blocker}
        return {"type": "content_missing", "field": "unknown", "details": blocker}

    if "ontbreekt in de gegenereerde site" in b:
        # "Verwachte pagina 'over-ons.html' ontbreekt"
        m = re.search(r"'([^']+)'", blocker)
        page = m.group(1).replace(".html", "") if m else ""
        return {"type": "missing_page", "page": page, "details": blocker}

    if any(x in b for x in ["visuele", "contrast", "screenshot", "tekst", "layout", "afgesneden"]):
        return {"type": "visual_issue", "details": blocker}

    if "build" in b or "turbopack" in b or "typescript" in b or "module not found" in b:
        return {"type": "build_failed", "details": blocker}

    if "validate" in b or "niet geslaagd" in b:
        return {"type": "validate_failed", "details": blocker}

    return {"type": "unknown", "details": blocker}


# ── Repair-acties ─────────────────────────────────────────────────────────────

def repair_content_missing(classified: dict, site_dir: Path, collected_path: Path) -> bool:
    """Injecteer ontbrekende contactinfo uit facts.json of structured_data.json in HTML."""
    # Probeer facts.json (nieuw), fallback op structured_data.json (oud)
    facts: dict = {}
    for fname in ("facts.json", "structured_data.json"):
        fpath = collected_path / fname
        if fpath.exists():
            try:
                raw = json.loads(fpath.read_text(encoding="utf-8"))
                if fname == "structured_data.json":
                    contact = raw.get("contact", {})
                    facts = {"phone": contact.get("phone", ""), "email": contact.get("email", "")}
                else:
                    facts = raw
                break
            except Exception:
                continue
    if not facts:
        return False

    field = classified.get("field", "")
    value = facts.get(field, "") or facts.get("phone" if field == "phone" else field, "")
    if not value:
        return False

    # Injecteer als zichtbare tekst in contact-pagina of footer
    injected = 0
    for html_file in sorted(site_dir.rglob("*.html")):
        if html_file.name.startswith("_") or "_next" in str(html_file):
            continue
        content = html_file.read_text(encoding="utf-8", errors="ignore")
        if value in content:
            continue  # al aanwezig

        # Injecteer in <footer> als fallback
        if field == "phone" and "<footer" in content:
            snippet = f'<a href="tel:{re.sub(r"[^+0-9]", "", value)}" class="text-primary">{value}</a>'
            new = content.replace(
                "</footer>",
                f'<!-- auto-injected phone -->{snippet}</footer>',
                1
            )
        elif field == "email" and "<footer" in content:
            snippet = f'<a href="mailto:{value}" class="text-primary">{value}</a>'
            new = content.replace("</footer>", f'<!-- auto-injected email -->{snippet}</footer>', 1)
        else:
            continue

        if new != content:
            html_file.write_text(new, encoding="utf-8")
            injected += 1

    return injected > 0


def repair_missing_page(classified: dict, briefing_path: Path, company_name: str,
                         project_dir: Path, image_manifest: Path | None,
                         nav_routes: list[str]) -> bool:
    """Regenereer een ontbrekende pagina."""
    page_slug = classified.get("page", "")
    if not page_slug:
        return False

    out_path = project_dir.parent / f"{project_dir.name}-repair-{page_slug}.txt"
    cmd = [
        "python", str(SCRIPTS_DIR / "generate_site.py"),
        "--brief",      str(briefing_path),
        "--company",    company_name,
        "--unit",       "page",
        "--page-slug",  page_slug,
        "--out",        str(out_path),
    ]
    if image_manifest and image_manifest.exists():
        cmd += ["--image-manifest", str(image_manifest)]
    if nav_routes:
        cmd += ["--nav-pages", json.dumps(nav_routes)]

    proc = subprocess.run(cmd, cwd=str(SCRIPTS_DIR), capture_output=True, text=True)
    if proc.returncode != 0:
        return False

    # Parse naar project
    parse_cmd = [
        "python", str(SCRIPTS_DIR / "parse_generated_site.py"),
        "--input",  str(out_path),
        "--outdir", str(project_dir),
        "--force",
    ]
    proc2 = subprocess.run(parse_cmd, cwd=str(SCRIPTS_DIR), capture_output=True, text=True)
    return proc2.returncode == 0


def repair_visual_issue(classified: dict, site_dir: Path, screenshot_json: Path | None,
                        briefing_path: Path, company_name: str, project_dir: Path,
                        round_num: int) -> bool:
    """
    Visuele reparatie in drie niveaus:
    1. CSS-injectie (round 1)
    2. Pagina-regeneratie met issue als input (round 2)
    3. Safe template fallback (round 3+)
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")

    if round_num == 1 and screenshot_json and screenshot_json.exists() and api_key:
        # CSS-injectie via bestaande screenshot-repair
        from repair_generated_site import fix_screenshot_issues
        result = fix_screenshot_issues(site_dir, screenshot_json)
        return result > 0

    if round_num == 2 and screenshot_json and screenshot_json.exists():
        # Regenereer pages met visuele issues
        try:
            shot_data = json.loads(screenshot_json.read_text(encoding="utf-8"))
            # Gebruik "route" veld (nieuw) of fallback op page-parsing (oud)
            affected_pages = set()
            for p in shot_data.get("results", []):
                if not p.get("issues"):
                    continue
                route = p.get("route") or p.get("page", "")
                # Normaliseer: verwijder index.html, leading slash, extensie
                route = route.replace("/index.html", "").replace(".html", "").strip("/.")
                if route and route not in ("", "index", "404", "legal", "_not-found"):
                    affected_pages.add(route)
            regenerated = 0
            for page_slug in affected_pages:
                if not page_slug:
                    continue
                out_path = project_dir.parent / f"{project_dir.name}-visual-repair-{page_slug}.txt"
                issues_context = "\n".join(
                    f"- {i}" for p in shot_data["results"]
                    for i in p.get("issues", [])
                    if page_slug in p["page"]
                )
                # Regenereer met visuele issues als extra context
                cmd = [
                    "python", str(SCRIPTS_DIR / "generate_site.py"),
                    "--brief",      str(briefing_path),
                    "--company",    company_name,
                    "--unit",       "page",
                    "--page-slug",  page_slug,
                    "--page-desc",  f"VISUELE PROBLEMEN OM TE VERMIJDEN:\n{issues_context}",
                    "--out",        str(out_path),
                ]
                proc = subprocess.run(cmd, cwd=str(SCRIPTS_DIR), capture_output=True, text=True)
                if proc.returncode == 0:
                    parse_cmd = [
                        "python", str(SCRIPTS_DIR / "parse_generated_site.py"),
                        "--input",  str(out_path),
                        "--outdir", str(project_dir),
                        "--force",
                    ]
                    proc2 = subprocess.run(parse_cmd, cwd=str(SCRIPTS_DIR), capture_output=True, text=True)
                    if proc2.returncode == 0:
                        regenerated += 1
            return regenerated > 0
        except Exception:
            return False

    # Round 3+: safe template fallback — injecteer veilige CSS die visuele problemen elimineert
    _inject_safe_styles(site_dir)
    return True


def _inject_safe_styles(site_dir: Path) -> None:
    """Injecteer veilige CSS-fallbacks die veelvoorkomende visuele problemen oplossen."""
    safe_css = """
/* auto-repair: safe-template fallback */
/* Geen tekst over drukke achtergrondafbeeldingen */
section[style*="background-image"] { background-image: none !important; background-color: oklch(var(--b2)); }
[class*="hero"][style*="background-image"] { background-image: none !important; background-color: oklch(var(--b3)); }
/* Altijd leesbare tekst */
h1, h2, h3, .font-heading { color: oklch(var(--bc)) !important; }
/* Cards altijd leesbaar */
.card, [class*="card"] { background-color: oklch(var(--b1)); color: oklch(var(--bc)); }
/* CTA-knoppen altijd zichtbaar */
.btn-primary, [class*="btn-primary"] { background-color: oklch(var(--p)); color: oklch(var(--pc)) !important; }
"""
    style_block = f"<style>{safe_css}</style>"
    for html_file in sorted(site_dir.rglob("*.html")):
        if html_file.name.startswith("_") or "_next" in str(html_file):
            continue
        content = html_file.read_text(encoding="utf-8", errors="ignore")
        if "safe-template fallback" in content:
            continue
        new = re.sub(r"(</head>)", style_block + r"\n\1", content, flags=re.IGNORECASE, count=1)
        if new != content:
            html_file.write_text(new, encoding="utf-8")


# ── Repair cycle ──────────────────────────────────────────────────────────────

def run_repair_cycle(
    blockers: list[str],
    site_dir: Path,
    collected_path: Path,
    project_dir: Path,
    briefing_path: Path,
    company_name: str,
    image_manifest: Path | None,
    nav_routes: list[str],
    round_num: int,
    screenshot_json: Path | None = None,
    log_fn=print,
) -> dict:
    """
    Voer één repair-ronde uit voor de opgegeven blockers.

    Geeft terug:
      {
        "actions": [{"type": ..., "action_kind": ..., "ok": bool}],
        "needs_rebuild": bool,   # TSX gewijzigd → rebuild vereist
        "needs_screenshot": bool # visuele actie → screenshot herhalen
      }

    action_kind:
      "tsx_regen"   — TSX-bestand gewijzigd, rebuild vereist
      "html_patch"  — HTML direct gepatcht, geen rebuild
      "css_patch"   — CSS geïnjecteerd in /out HTML, geen rebuild
      "safe_fallback" — veilige CSS in /out HTML, geen rebuild
    """
    result: dict = {
        "actions": [],
        "needs_rebuild": False,
        "needs_screenshot": False,
    }
    classified = [classify_blocker(b) for b in blockers]

    for item in classified:
        btype = item["type"]
        log_fn(f"[INFO] auto_repair: {btype} — {item['details'][:80]}")
        action: dict = {"type": btype, "details": item["details"], "action_kind": "unknown", "ok": False}

        if btype == "content_missing":
            ok = repair_content_missing(item, site_dir, collected_path)
            action["action_kind"] = "html_patch"
            action["ok"] = ok
            log_fn(f"  → content_missing: {'gefixed (html_patch)' if ok else 'mislukt'}")

        elif btype == "missing_page":
            ok = repair_missing_page(item, briefing_path, company_name,
                                     project_dir, image_manifest, nav_routes)
            action["action_kind"] = "tsx_regen"
            action["ok"] = ok
            if ok:
                result["needs_rebuild"] = True
            log_fn(f"  → missing_page '{item.get('page')}': {'tsx_regen' if ok else 'mislukt'}")

        elif btype == "visual_issue":
            ok = repair_visual_issue(item, site_dir, screenshot_json,
                                     briefing_path, company_name, project_dir, round_num)
            # Round 1 = css_patch, round 2 = tsx_regen (page regen), round 3+ = safe_fallback
            if round_num == 1:
                action["action_kind"] = "css_patch"
            elif round_num == 2:
                action["action_kind"] = "tsx_regen"
                if ok:
                    result["needs_rebuild"] = True
            else:
                action["action_kind"] = "safe_fallback"
            action["ok"] = ok
            result["needs_screenshot"] = True
            log_fn(f"  → visual_issue ({action['action_kind']}): {'ok' if ok else 'mislukt'}")

        elif btype == "validate_failed":
            # Voer HTML-directe repair uit via repair_generated_site
            try:
                import subprocess as _sp
                r = _sp.run(
                    ["python", str(SCRIPTS_DIR / "repair_generated_site.py"),
                     "--site-dir", str(site_dir), "--passes", "1"],
                    capture_output=True, text=True, cwd=str(SCRIPTS_DIR),
                )
                ok = r.returncode == 0
                action["action_kind"] = "html_patch"
                action["ok"] = ok
                log_fn(f"  → validate_failed: repair_generated_site {'ok' if ok else 'mislukt'}")
            except Exception as e:
                log_fn(f"  → validate_failed repair exception: {e}")

        elif btype == "build_failed":
            action["action_kind"] = "pipeline_autofix"
            action["ok"] = False
            log_fn(f"  → build_failed: pipeline auto-fix handelt dit af bij rebuild")

        else:
            log_fn(f"  → unknown blocker: geen actie")

        result["actions"].append(action)

    return result
