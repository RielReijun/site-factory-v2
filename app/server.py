"""
server.py — Site Factory dashboard server.

Serveert:
  GET  /                          → dashboard
  GET  /api/prospects             → JSON lijst van alle prospects
  POST /api/add                   → voeg nieuwe prospect toe
  GET  /sites/<slug>/             → gegenereerde site (index.html)
  GET  /sites/<slug>/<path>       → statische bestanden van gegenereerde site
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests as _requests
from flask import Flask, Response, abort, jsonify, render_template, request, send_from_directory, stream_with_context


PROSPECTS_FILE = Path("/workspace/data/prospects.json")
OUTPUT_DIR     = Path("/workspace/output")
TEMPLATES_DIR  = Path(__file__).parent / "templates"
SCRIPTS_DIR    = Path("/workspace/scripts")
LOG_FILE       = Path("/workspace/data/pipeline.log")
STATUS_FILE    = Path("/workspace/data/pipeline_status.json")
TRASH_DIR      = Path("/workspace/data/trash")
TRASH_DAYS     = 14

ANTHROPIC_ADMIN_KEY  = os.environ.get("ANTHROPIC_ADMIN_KEY", "")
ADMIN_API_BASE       = "https://api.anthropic.com/v1"
_usage_cache: dict   = {}          # {"ts": float, "data": dict}
USAGE_CACHE_TTL      = 60          # seconden

# Prijzen in USD per 1 miljoen tokens
PRICING = {
    "claude-opus-4-7":          {"input": 15.0,  "output": 75.0},
    "claude-sonnet-4-6":        {"input":  3.0,  "output": 15.0},
    "claude-sonnet-4-5":        {"input":  3.0,  "output": 15.0},
    "claude-haiku-4-5":         {"input":  0.80, "output":  4.0},
    "claude-haiku-4-5-20251001":{"input":  0.80, "output":  4.0},
}
_DEFAULT_PRICING = {"input": 3.0, "output": 15.0}

app = Flask(__name__, template_folder=str(TEMPLATES_DIR))


# ── Helpers ───────────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    return name.strip("-")


def load_prospects() -> list:
    if not PROSPECTS_FILE.exists():
        return []
    return json.loads(PROSPECTS_FILE.read_text(encoding="utf-8"))


def save_prospects(prospects: list) -> None:
    PROSPECTS_FILE.write_text(
        json.dumps(prospects, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    p = PRICING.get(model, _DEFAULT_PRICING)
    return (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000


def _read_meta(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_prospect_costs(p: dict) -> list[dict]:
    costs = []
    collected_path = p.get("collected_path")
    slug = slugify(p.get("name", ""))

    if collected_path:
        cp = Path(collected_path)

        for fname, label in [
            ("research_meta.json",  "Research"),
            ("briefing_meta.json",  "Brief"),
            ("repair_meta.json",    "Brief repareren"),
        ]:
            m = _read_meta(cp / fname)
            if not m:
                continue
            model   = m.get("model", "")
            usage   = m.get("usage", {})
            in_tok  = usage.get("input_tokens")  or 0
            out_tok = usage.get("output_tokens") or 0
            costs.append({
                "step":          label,
                "model":         model,
                "input_tokens":  in_tok,
                "output_tokens": out_tok,
                "cost":          calc_cost(model, in_tok, out_tok),
            })

    # Bepaal of het een Next.js site is — dan oude HTML-units overslaan
    is_nextjs    = (OUTPUT_DIR / f"{slug}-next" / "out" / "index.html").exists()
    html_units   = {"home-html", "styles", "scripts"}  # alleen in oude HTML-pipeline

    for meta_file in sorted(OUTPUT_DIR.glob(f"{slug}-*.meta.json")):
        unit = meta_file.name.replace(f"{slug}-", "", 1).replace(".meta.json", "")
        # Skip HTML-units als we een Next.js site hebben
        if is_nextjs and unit in html_units:
            continue
        m = _read_meta(meta_file)
        if not m:
            continue
        model   = m.get("model", "")
        usage   = m.get("usage", {})
        in_tok  = usage.get("input_tokens")  or 0
        out_tok = usage.get("output_tokens") or 0
        costs.append({
            "step":          f"Genereer: {unit}",
            "model":         model,
            "input_tokens":  in_tok,
            "output_tokens": out_tok,
            "cost":          calc_cost(model, in_tok, out_tok),
        })

    return costs


def _find_site_dir(slug: str) -> Path:
    """Zoek de gegenereerde site-map: Next.js /out heeft voorrang, dan klassieke -site."""
    nextjs_out = OUTPUT_DIR / f"{slug}-next" / "out"
    if nextjs_out.is_dir() and (nextjs_out / "index.html").exists():
        return nextjs_out
    return OUTPUT_DIR / f"{slug}-site"


def enrich_prospect(p: dict) -> dict:
    """Voeg afgeleide velden toe voor het dashboard."""
    slug     = slugify(p.get("name", ""))
    site_dir = _find_site_dir(slug)

    stages = {
        "collect":  p.get("status") == "collected",
        "research": p.get("research_status") == "done",
        "brief":    p.get("briefing_status") == "done",
        "generate": site_dir.is_dir() and (site_dir / "index.html").exists(),
        "validate": p.get("site_status") == "done",
    }

    # Kosten samenvatten voor kaartweergave
    costs      = get_prospect_costs(p)
    total_cost = sum(c["cost"] for c in costs)
    # Next.js /out heeft pagina's in subdirs (over-ons/index.html) — gebruik rglob
    if site_dir.is_dir():
        page_count = len([
            f for f in site_dir.rglob("index.html")
            if "_next" not in str(f) and "_not-found" not in str(f)
        ])
    else:
        page_count = 0

    # Readiness score — lees content_validation.json en screenshot_validation.json
    readiness: dict = {}
    if site_dir.exists():
        for val_file, key in [
            ("content_validation.json", "content"),
            ("screenshot_validation.json", "visual"),
        ]:
            vf = site_dir / val_file
            if vf.exists():
                try:
                    vdata = json.loads(vf.read_text(encoding="utf-8"))
                    if key == "content":
                        readiness["content"] = "pass" if vdata.get("ready", vdata.get("ok")) else "fail"
                        readiness["content_critical"] = vdata.get("critical", [])
                    elif key == "visual":
                        readiness["visual"] = "pass" if vdata.get("total_issues", 1) == 0 else "warn"
                        readiness["visual_issues"] = vdata.get("total_issues", 0)
                except Exception:
                    pass
        readiness["build"]  = "pass" if (site_dir / "index.html").exists() else "fail"
        readiness["routes"] = page_count
        # review_status uit prospects.json
        readiness["review"] = p.get("review_status", "pending")

    return {
        **p,
        "slug":           slug,
        "site_url":       f"/sites/{slug}/" if stages["generate"] else None,
        "stages":         stages,
        "all_done":       all(stages.values()),
        "failed":         p.get("status") == "failed",
        "deploy_status":  p.get("deploy_status", ""),
        "github_url":     p.get("github_url", ""),
        "cloudflare_url": p.get("cloudflare_url", ""),
        "total_cost":     round(total_cost, 4),
        "page_count":     page_count,
        "readiness":      readiness,
        "needs_review":   p.get("review_status") == "needs_review",
    }


# ── Anthropic Admin API ───────────────────────────────────────────────────────

def _fetch_usage_from_api() -> dict:
    """Haal kosten op via Anthropic Admin API. Geeft lege dict terug bij fout."""
    if not ANTHROPIC_ADMIN_KEY:
        return {"error": "ANTHROPIC_ADMIN_KEY niet geconfigureerd"}

    now         = datetime.now(timezone.utc)
    today       = now.date().isoformat()                            # max ending_at
    yesterday   = (now.date() - timedelta(days=1)).isoformat()     # recentste complete dag
    month_start = now.date().replace(day=1).isoformat()

    headers = {
        "x-api-key":         ANTHROPIC_ADMIN_KEY,
        "anthropic-version": "2023-06-01",
    }

    def _get_cost(start: str, end: str) -> float:
        """Haal gecombineerde kosten op voor een datumbereik (inclusief paginering)."""
        total  = 0.0
        params: dict = {"starting_at": start, "ending_at": end}
        while True:
            r = _requests.get(
                f"{ADMIN_API_BASE}/organizations/cost_report",
                headers=headers,
                params=params,
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            for bucket in data.get("data", []):
                for result in bucket.get("results", []):
                    total += float(result.get("amount", 0) or 0)
            if not data.get("has_more"):
                break
            params = {"starting_at": start, "ending_at": end, "page": data["next_page"]}
        return total / 100.0     # API geeft USD-centen terug

    try:
        yesterday_cost = _get_cost(yesterday, today)
        month_cost     = _get_cost(month_start, today)
        return {
            "today":       yesterday_cost,   # recentste complete dag
            "today_label": yesterday,
            "month":       month_cost,
            "month_label": f"{month_start} t/m {yesterday}",
            "as_of":       now.strftime("%H:%M"),
            "error":       None,
        }
    except Exception as e:
        return {"error": str(e)}


def get_usage(force: bool = False) -> dict:
    """Geeft gecachte usage terug (max USAGE_CACHE_TTL seconden oud)."""
    now = time.time()
    if not force and _usage_cache.get("ts") and now - _usage_cache["ts"] < USAGE_CACHE_TTL:
        return _usage_cache["data"]
    data = _fetch_usage_from_api()
    _usage_cache["ts"]   = now
    _usage_cache["data"] = data
    return data


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def dashboard():
    return render_template("dashboard.html")


@app.get("/api/prospects")
def api_prospects():
    prospects = [enrich_prospect(p) for p in load_prospects() if p.get("status") != "trashed"]
    return jsonify(prospects)


@app.post("/api/add")
def api_add():
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    url  = data.get("url",  "").strip()

    if not name:
        return jsonify({"ok": False, "error": "Naam is verplicht"}), 400
    if not url:
        return jsonify({"ok": False, "error": "URL is verplicht"}), 400
    if not re.match(r"^https?://", url):
        return jsonify({"ok": False, "error": "URL moet beginnen met http:// of https://"}), 400

    prospects = load_prospects()

    for p in prospects:
        if p.get("name", "").strip().lower() == name.lower():
            return jsonify({"ok": False, "error": f"'{name}' staat al in de lijst"}), 409
        if p.get("url", "").strip().rstrip("/") == url.rstrip("/"):
            return jsonify({"ok": False, "error": f"URL al in gebruik door '{p['name']}'"}), 409

    prospects.append({
        "name":   name,
        "url":    url,
        "status": "pending",
        "added_at": datetime.now(timezone.utc).isoformat(),
    })
    save_prospects(prospects)

    return jsonify({"ok": True, "name": name, "url": url})


@app.get("/api/status")
def api_status():
    if not STATUS_FILE.exists():
        return jsonify({"running": False, "prospect": "", "step": "", "step_n": 0, "total": 0})
    try:
        return jsonify(json.loads(STATUS_FILE.read_text(encoding="utf-8")))
    except Exception:
        return jsonify({"running": False})


@app.get("/api/logs")
def api_logs():
    """SSE stream: stuur de laatste 100 regels en tail daarna nieuwe regels."""
    def generate():
        # Stuur bestaande regels eerst
        if LOG_FILE.exists():
            lines = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
            for line in lines[-100:]:
                yield f"data: {line}\n\n"
        else:
            yield "data: [INFO] Nog geen pipeline gestart\n\n"

        # Tail nieuwe regels
        with LOG_FILE.open("a+", encoding="utf-8") as f:
            f.seek(0, 2)  # ga naar einde
            while True:
                line = f.readline()
                if line:
                    yield f"data: {line.rstrip()}\n\n"
                else:
                    yield ": heartbeat\n\n"
                    time.sleep(0.3)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/prospects/<slug>/brief")
def api_brief(slug):
    for p in load_prospects():
        if slugify(p.get("name", "")) == slug:
            path = Path(p["collected_path"]) / "briefing.md" if p.get("collected_path") else None
            if not path or not path.exists():
                return jsonify({"content": None, "error": "Briefing nog niet gegenereerd"})
            return jsonify({"content": path.read_text(encoding="utf-8", errors="ignore")})
    abort(404)


@app.get("/api/prospects/<slug>/research")
def api_research(slug):
    for p in load_prospects():
        if slugify(p.get("name", "")) == slug:
            path = Path(p["collected_path"]) / "research.md" if p.get("collected_path") else None
            if not path or not path.exists():
                return jsonify({"content": None, "error": "Research nog niet gegenereerd"})
            return jsonify({"content": path.read_text(encoding="utf-8", errors="ignore")})
    abort(404)


@app.get("/api/prospects/<slug>/pages")
def api_pages(slug):
    for p in load_prospects():
        if slugify(p.get("name", "")) != slug:
            continue
        site_dir       = _find_site_dir(slug)
        is_nextjs      = (OUTPUT_DIR / f"{slug}-next" / "out" / "index.html").exists()
        collected_path = p.get("collected_path")

        pages = []
        overflow_pages = []
        if collected_path:
            pages_file = Path(collected_path) / "pages.json"
            if pages_file.exists():
                try:
                    data           = json.loads(pages_file.read_text(encoding="utf-8"))
                    pages          = data.get("pages", [])
                    overflow_pages = data.get("overflow_pages", [])
                except Exception:
                    pass

        def _page_label(file: str) -> str:
            """Toon route (/over-ons/) voor Next.js, bestandsnaam voor HTML."""
            if not is_nextjs:
                return file
            slug_part = file.replace(".html", "")
            return "/" if slug_part == "index" else f"/{slug_part}/"

        def _page_exists(file: str) -> bool:
            if is_nextjs:
                slug_part = file.replace(".html", "")
                route_dir = site_dir / slug_part
                return (site_dir / "index.html").exists() if slug_part == "index" \
                       else (route_dir / "index.html").exists()
            return (site_dir / file).exists()

        all_pages = [{"file": "index.html", "title": "Homepage", "description": "Hoofdpagina"}] + pages
        result = []
        for page in all_pages:
            result.append({
                **page,
                "file":   _page_label(page["file"]),
                "exists": _page_exists(page["file"]),
            })

        overflow_result = []
        for page in overflow_pages:
            overflow_result.append({
                **page,
                "exists": (site_dir / page["file"]).exists(),
            })

        return jsonify({"pages": result, "overflow_pages": overflow_result, "site_exists": site_dir.exists()})
    abort(404)


@app.post("/api/prospects/<slug>/generate-page")
def api_generate_page(slug):
    data       = request.get_json(silent=True) or {}
    page_file  = data.get("file",        "").strip()
    page_title = data.get("title",       "").strip()
    page_desc  = data.get("description", "").strip()

    if not page_file:
        return jsonify({"ok": False, "error": "file is verplicht"}), 400

    prospects = load_prospects()
    for p in prospects:
        if slugify(p.get("name", "")) != slug:
            continue

        collected_path = p.get("collected_path")
        if not collected_path:
            return jsonify({"ok": False, "error": "Prospect niet gecollect"}), 400

        briefing_path  = Path(collected_path) / "briefing.md"
        image_manifest = Path(collected_path) / "images.json"
        pages_json     = Path(collected_path) / "pages.json"
        site_dir       = OUTPUT_DIR / f"{slug}-site"
        stem           = page_file.replace(".html", "").replace("/", "-")
        out_path       = OUTPUT_DIR / f"{slug}-{stem}.txt"
        unit           = "home_html" if page_file == "index.html" else "page"

        if not briefing_path.exists():
            return jsonify({"ok": False, "error": "Briefing niet gevonden"}), 404

        # Lees nav-pagina's uit pages.json als die beschikbaar is
        nav_pages_json = None
        if pages_json.exists():
            try:
                discovered = json.loads(pages_json.read_text(encoding="utf-8")).get("pages", [])
                nav_list = ["index.html"] + [pg["file"] for pg in discovered if pg["file"] != "index.html"]
                nav_pages_json = json.dumps(nav_list)
            except Exception:
                pass

        company_name = p["name"]

        # Header/footer referentie voor consistente subpagina's
        header_footer_ref_path = site_dir / "_header_footer_ref.html"
        index_html_path        = site_dir / "index.html"

        def run_bg(cp=collected_path, bp=briefing_path, im=image_manifest,
                   sd=site_dir, op=out_path, u=unit, pf=page_file, pt=page_title,
                   pd=page_desc, cn=company_name, np=nav_pages_json,
                   hf_ref=header_footer_ref_path, idx=index_html_path):
            def llog(msg):
                with LOG_FILE.open("a", encoding="utf-8") as lf:
                    lf.write(msg + "\n")

            llog(f"\n{'─'*60}")
            llog(f"[STAP] generate-page: {pf}")
            llog(f"{'─'*60}")

            gen_cmd = [
                "python", str(SCRIPTS_DIR / "generate_site.py"),
                "--brief",   str(bp),
                "--company", cn,
                "--unit",    u,
                "--out",     str(op),
            ]
            if u == "page":
                gen_cmd += ["--page-file", pf, "--page-title", pt, "--page-desc", pd]
                # Geef header/footer mee voor consistente class names
                if hf_ref.exists():
                    gen_cmd += ["--ref-html", str(hf_ref)]
            elif u in ("styles", "scripts") and idx.exists():
                gen_cmd += ["--ref-html", str(idx)]
            if im.exists():
                gen_cmd += ["--image-manifest", str(im)]
            if np:
                gen_cmd += ["--nav-pages", np]

            proc = subprocess.Popen(
                gen_cmd, cwd=str(SCRIPTS_DIR),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            for line in proc.stdout:
                llog(line.rstrip())
            proc.wait()

            if proc.returncode != 0:
                llog(f"[FAIL] Generatie van {pf} mislukt")
                return

            parse_cmd = [
                "python", str(SCRIPTS_DIR / "parse_generated_site.py"),
                "--input",  str(op),
                "--outdir", str(sd),
                "--force",
            ]
            proc2 = subprocess.Popen(
                parse_cmd, cwd=str(SCRIPTS_DIR),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            for line in proc2.stdout:
                llog(line.rstrip())
            proc2.wait()

            if proc2.returncode == 0:
                llog(f"[OK]  {pf} gegenereerd en klaar")
            else:
                llog(f"[FAIL] Parsen van {pf} mislukt")

        threading.Thread(target=run_bg, daemon=True).start()
        return jsonify({"ok": True})

    abort(404)


@app.get("/api/prospects/<slug>/costs")
def api_costs(slug):
    for p in load_prospects():
        if slugify(p.get("name", "")) != slug:
            continue
        costs = get_prospect_costs(p)
        total = sum(c["cost"] for c in costs)
        timings: list[dict] = []
        total_duration_s: float | None = None
        cp = p.get("collected_path")
        if cp:
            t = _read_meta(Path(cp) / "timings.json")
            if t:
                timings          = t.get("steps", [])
                total_duration_s = t.get("total_duration_s")
        return jsonify({"costs": costs, "total": total,
                        "timings": timings, "total_duration_s": total_duration_s})
    abort(404)


@app.get("/api/prospects/<slug>/log")
def api_prospect_log(slug):
    """Geeft de volledige inhoud van pipeline.log terug als plain text."""
    if not LOG_FILE.exists():
        return "Nog geen log beschikbaar.", 200, {"Content-Type": "text/plain; charset=utf-8"}
    content = LOG_FILE.read_text(encoding="utf-8", errors="ignore")
    return content, 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.get("/api/prospects/<slug>/report")
def api_report(slug):
    """Genereert een downloadbare HTML-rapport voor een prospect."""
    import html as html_lib
    from datetime import timezone

    for p in load_prospects():
        if slugify(p.get("name", "")) != slug:
            continue

        name = p.get("name", slug)
        url  = p.get("url", "")
        now  = datetime.now(timezone.utc).strftime("%d-%m-%Y %H:%M UTC")

        # Brief
        brief_md = ""
        cp = p.get("collected_path")
        if cp:
            bp = Path(cp) / "briefing.md"
            if bp.exists():
                brief_md = bp.read_text(encoding="utf-8", errors="ignore")

        # Kosten + timings
        costs = get_prospect_costs(p)
        total_cost = sum(c["cost"] for c in costs)
        timings: list[dict] = []
        total_duration_s: float | None = None
        if cp:
            t = _read_meta(Path(cp) / "timings.json")
            if t:
                timings          = t.get("steps", [])
                total_duration_s = t.get("total_duration_s")

        # Pagina's
        pages: list[dict] = []
        overflow_pages: list[dict] = []
        if cp:
            pf = Path(cp) / "pages.json"
            if pf.exists():
                try:
                    d = json.loads(pf.read_text(encoding="utf-8"))
                    pages          = d.get("pages", [])
                    overflow_pages = d.get("overflow_pages", [])
                except Exception:
                    pass

        site_dir = OUTPUT_DIR / f"{slug}-site"

        def fmt_dur(s):
            if s is None: return "–"
            s = int(s)
            return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"

        def esc(s):
            return html_lib.escape(str(s))

        # Kosten-tabel
        cost_rows = "".join(
            f"<tr><td>{esc(c['step'])}</td><td>{esc(c['model'])}</td>"
            f"<td class='num'>{c['input_tokens']:,}</td>"
            f"<td class='num'>{c['output_tokens']:,}</td>"
            f"<td class='num'>${c['cost']:.4f}</td></tr>"
            for c in costs
        )
        cost_total_row = f"<tr class='total'><td colspan='4'><strong>Totaal</strong></td><td class='num'><strong>${total_cost:.4f}</strong></td></tr>"

        # Timings-tabel
        timing_rows = "".join(
            f"<tr><td>{esc(t['step'])}</td>"
            f"<td class='num'>{fmt_dur(t.get('duration_s'))}</td>"
            f"<td>{'✓' if t.get('ok') else '✗'}</td></tr>"
            for t in timings
        )

        # Paginalijst
        page_rows = "".join(
            f"<tr><td><code>{esc(pg['file'])}</code></td><td>{esc(pg['title'])}</td>"
            f"<td>{esc(pg.get('description',''))}</td>"
            f"<td>{'✓' if (site_dir / pg['file']).exists() else '○'}</td></tr>"
            for pg in ([{"file": "index.html", "title": "Homepage", "description": "Hoofdpagina"}] + pages)
        )
        overflow_rows = "".join(
            f"<tr class='overflow'><td><code>{esc(pg['file'])}</code></td><td>{esc(pg['title'])}</td>"
            f"<td>{esc(pg.get('description',''))}</td><td>optioneel</td></tr>"
            for pg in overflow_pages
        )

        # Brief als HTML (eenvoudige markdown → HTML conversie)
        import re as _re
        brief_html = ""
        for line in brief_md.splitlines():
            if line.startswith("# "):   brief_html += f"<h1>{esc(line[2:])}</h1>"
            elif line.startswith("## "): brief_html += f"<h2>{esc(line[3:])}</h2>"
            elif line.startswith("### "): brief_html += f"<h3>{esc(line[4:])}</h3>"
            elif line.startswith("- "):  brief_html += f"<li>{esc(line[2:])}</li>"
            elif line.strip() == "":    brief_html += "<br>"
            else:                       brief_html += f"<p>{esc(line)}</p>"

        html = f"""<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<title>Pipeline rapport — {esc(name)}</title>
<style>
  body {{ font-family: system-ui, sans-serif; font-size: 14px; color: #1a1a2e; max-width: 960px; margin: 0 auto; padding: 40px 32px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  h2 {{ font-size: 16px; margin: 32px 0 10px; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px; color: #2d3748; }}
  h3 {{ font-size: 14px; margin: 16px 0 6px; color: #4a5568; }}
  .meta {{ color: #718096; font-size: 13px; margin-bottom: 32px; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 16px; font-size: 13px; }}
  th {{ background: #f7fafc; text-align: left; padding: 8px 10px; border-bottom: 2px solid #e2e8f0; font-size: 12px; text-transform: uppercase; letter-spacing: .5px; color: #718096; }}
  td {{ padding: 7px 10px; border-bottom: 1px solid #edf2f7; vertical-align: top; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  tr.total td {{ background: #f7fafc; font-weight: 600; }}
  tr.overflow td {{ color: #a0aec0; }}
  li {{ margin-left: 20px; margin-bottom: 2px; }}
  code {{ background: #edf2f7; padding: 1px 5px; border-radius: 3px; font-size: 12px; }}
  .brief-box {{ background: #f7fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px 24px; line-height: 1.7; }}
  .brief-box h1 {{ font-size: 18px; margin-bottom: 8px; }}
  .brief-box h2 {{ font-size: 14px; border: none; margin: 20px 0 6px; color: #2d3748; }}
  .brief-box h3 {{ font-size: 13px; }}
  .brief-box p, .brief-box li {{ font-size: 13px; color: #4a5568; }}
  @media print {{
    body {{ padding: 20px; }}
    h2 {{ page-break-after: avoid; }}
    table {{ page-break-inside: avoid; }}
  }}
</style>
</head>
<body>
<h1>Pipeline rapport — {esc(name)}</h1>
<div class="meta">{esc(url)} &nbsp;·&nbsp; Gegenereerd op {now}</div>

<h2>Kosten per stap</h2>
<table>
  <thead><tr><th>Stap</th><th>Model</th><th class="num">Input</th><th class="num">Output</th><th class="num">Kosten</th></tr></thead>
  <tbody>{cost_rows}{cost_total_row}</tbody>
</table>

<h2>Tijdsduur per stap{f" &nbsp;<small style='font-weight:400;color:#718096'>Totaal: {fmt_dur(total_duration_s)}</small>" if total_duration_s else ""}</h2>
<table>
  <thead><tr><th>Stap</th><th class="num">Duur</th><th>Status</th></tr></thead>
  <tbody>{timing_rows or "<tr><td colspan='3' style='color:#a0aec0'>Geen timings beschikbaar</td></tr>"}</tbody>
</table>

<h2>Pagina's</h2>
<table>
  <thead><tr><th>Bestand</th><th>Titel</th><th>Beschrijving</th><th>Status</th></tr></thead>
  <tbody>{page_rows}{overflow_rows}</tbody>
</table>

<h2>Briefing</h2>
<div class="brief-box">{brief_html or "<p style='color:#a0aec0'>Geen briefing beschikbaar</p>"}</div>

</body>
</html>"""

        return html, 200, {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Disposition": f'attachment; filename="rapport-{slug}.html"',
        }
    abort(404)


@app.post("/api/prospects/<slug>/delete-permanent")
def api_delete_permanent(slug):
    prospects = load_prospects()
    for i, p in enumerate(prospects):
        if slugify(p.get("name", "")) != slug:
            continue
        if p.get("status") != "trashed":
            return jsonify({"ok": False, "error": "Niet in prullenbak"}), 400

        trash_path = p.get("trash_path", "")
        if trash_path and Path(trash_path).exists():
            shutil.rmtree(trash_path, ignore_errors=True)

        prospects.pop(i)
        save_prospects(prospects)
        return jsonify({"ok": True})

    abort(404)


def _trash_one(slug: str) -> str:
    """Core trash-logica; geeft 'ok' of een foutmelding terug."""
    prospects = load_prospects()
    for i, p in enumerate(prospects):
        if slugify(p.get("name", "")) != slug:
            continue
        if p.get("status") == "trashed":
            return "al in prullenbak"
        now        = datetime.now(timezone.utc)
        trash_name = f"{slug}_{now.strftime('%Y%m%dT%H%M%S')}"
        trash_path = TRASH_DIR / trash_name
        trash_path.mkdir(parents=True, exist_ok=True)
        collected_path = p.get("collected_path")
        if collected_path and Path(collected_path).exists():
            shutil.move(collected_path, str(trash_path / "collected"))
        site_dir = OUTPUT_DIR / f"{slug}-site"
        if site_dir.exists():
            shutil.move(str(site_dir), str(trash_path / "site"))
        raw_files = (
            list(OUTPUT_DIR.glob(f"{slug}-*.txt"))
            + list(OUTPUT_DIR.glob(f"{slug}-*.meta.json"))
            + list(OUTPUT_DIR.glob(f"{slug}-validation.json"))
        )
        if raw_files:
            raw_dir = trash_path / "raw"
            raw_dir.mkdir(exist_ok=True)
            for f in raw_files:
                shutil.move(str(f), str(raw_dir / f.name))
        p["status"]     = "trashed"
        p["trashed_at"] = now.isoformat()
        p["trash_path"] = str(trash_path)
        save_prospects(prospects)
        return "ok"
    return "niet gevonden"


@app.post("/api/bulk-trash")
def api_bulk_trash():
    """Verplaats meerdere prospects tegelijk naar de prullenbak."""
    slugs = (request.get_json(silent=True) or {}).get("slugs", [])
    if not slugs:
        return jsonify({"ok": False, "error": "Geen slugs opgegeven"}), 400
    results = {slug: _trash_one(slug) for slug in slugs}
    return jsonify({"ok": True, "results": results})


@app.post("/api/prospects/<slug>/trash")
def api_trash_prospect(slug):
    prospects = load_prospects()
    for i, p in enumerate(prospects):
        if slugify(p.get("name", "")) != slug:
            continue
        if p.get("status") == "trashed":
            return jsonify({"ok": False, "error": "Al in prullenbak"}), 400

        now        = datetime.now(timezone.utc)
        trash_name = f"{slug}_{now.strftime('%Y%m%dT%H%M%S')}"
        trash_path = TRASH_DIR / trash_name
        trash_path.mkdir(parents=True, exist_ok=True)

        # Verplaats collected directory
        collected_path = p.get("collected_path")
        if collected_path and Path(collected_path).exists():
            shutil.move(collected_path, str(trash_path / "collected"))

        # Verplaats gegenereerde site
        site_dir = OUTPUT_DIR / f"{slug}-site"
        if site_dir.exists():
            shutil.move(str(site_dir), str(trash_path / "site"))

        # Verplaats losse output-bestanden
        raw_files = (
            list(OUTPUT_DIR.glob(f"{slug}-*.txt"))
            + list(OUTPUT_DIR.glob(f"{slug}-*.meta.json"))
            + list(OUTPUT_DIR.glob(f"{slug}-validation.json"))
        )
        if raw_files:
            raw_dir = trash_path / "raw_files"
            raw_dir.mkdir(exist_ok=True)
            for f in raw_files:
                shutil.move(str(f), str(raw_dir / f.name))

        # Sla manifest op
        manifest = {
            "prospect":   dict(p),
            "trashed_at": now.isoformat(),
            "expires_at": (now + timedelta(days=TRASH_DAYS)).isoformat(),
            "original_paths": {
                "collected_path": collected_path,
                "site_dir":       str(OUTPUT_DIR / f"{slug}-site"),
            },
        }
        (trash_path / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Vervang prospect door stub
        prospects[i] = {
            "name":       p["name"],
            "url":        p.get("url", ""),
            "status":     "trashed",
            "trashed_at": now.isoformat(),
            "trash_path": str(trash_path),
        }
        save_prospects(prospects)
        return jsonify({"ok": True})

    abort(404)


@app.post("/api/prospects/<slug>/restore")
def api_restore_prospect(slug):
    prospects = load_prospects()
    for i, p in enumerate(prospects):
        if slugify(p.get("name", "")) != slug:
            continue
        if p.get("status") != "trashed":
            return jsonify({"ok": False, "error": "Niet in prullenbak"}), 400

        trash_path = Path(p["trash_path"])
        if not trash_path.exists():
            return jsonify({"ok": False, "error": "Trash-map niet gevonden op schijf"}), 404

        manifest   = json.loads((trash_path / "manifest.json").read_text(encoding="utf-8"))
        orig_paths = manifest["original_paths"]

        # Zet collected terug
        collected_trash = trash_path / "collected"
        if collected_trash.exists() and orig_paths.get("collected_path"):
            dest = Path(orig_paths["collected_path"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(collected_trash), str(dest))

        # Zet site terug
        site_trash = trash_path / "site"
        if site_trash.exists():
            shutil.move(str(site_trash), str(OUTPUT_DIR / f"{slug}-site"))

        # Zet losse bestanden terug
        raw_trash = trash_path / "raw_files"
        if raw_trash.exists():
            for f in raw_trash.iterdir():
                shutil.move(str(f), str(OUTPUT_DIR / f.name))

        # Ruim trash-map op
        shutil.rmtree(str(trash_path), ignore_errors=True)

        # Herstel originele prospect
        prospects[i] = manifest["prospect"]
        save_prospects(prospects)
        return jsonify({"ok": True})

    abort(404)


@app.get("/api/trash")
def api_trash_list():
    """Geef prullenbak-items terug en verwijder verlopen items automatisch."""
    prospects = load_prospects()
    now       = datetime.now(timezone.utc)
    trashed   = []
    changed   = False

    for i, p in enumerate(prospects):
        if p.get("status") != "trashed":
            continue

        trashed_at = p.get("trashed_at", "")
        try:
            trashed_dt = datetime.fromisoformat(trashed_at)
            expires_dt = trashed_dt + timedelta(days=TRASH_DAYS)
            days_left  = max(0, (expires_dt - now).days)
            expired    = now >= expires_dt
        except ValueError:
            days_left, expired = 0, True

        if expired:
            # Permanent verwijderen
            trash_path = p.get("trash_path", "")
            if trash_path and Path(trash_path).exists():
                shutil.rmtree(trash_path, ignore_errors=True)
            prospects[i] = None   # markeer voor verwijdering
            changed = True
            continue

        trashed.append({
            "name":       p["name"],
            "url":        p.get("url", ""),
            "slug":       slugify(p.get("name", "")),
            "trashed_at": trashed_at,
            "days_left":  days_left,
        })

    if changed:
        save_prospects([p for p in prospects if p is not None])

    return jsonify(trashed)


@app.post("/api/prospects/<slug>/deploy")
def api_deploy(slug):
    """Start een deploy naar GitHub (+ optioneel Cloudflare Pages) als achtergrondtaak."""
    prospects = load_prospects()
    for i, p in enumerate(prospects):
        if slugify(p.get("name", "")) != slug:
            continue

        site_dir = OUTPUT_DIR / f"{slug}-site"
        if not (site_dir / "index.html").exists():
            return jsonify({"ok": False, "error": "Site nog niet gegenereerd"}), 400

        if p.get("deploy_status") == "running":
            return jsonify({"ok": False, "error": "Deploy is al bezig"}), 409

        # Markeer als running
        prospects[i]["deploy_status"] = "running"
        save_prospects(prospects)

        company_name = p["name"]

        def run_deploy(sl=slug, cn=company_name, sd=site_dir, idx=i):
            def llog(msg):
                with LOG_FILE.open("a", encoding="utf-8") as lf:
                    lf.write(msg + "\n")

            llog(f"\n{'─'*60}")
            llog(f"[STAP] deploy: {cn}")
            llog(f"{'─'*60}")

            cmd = [
                "python", str(SCRIPTS_DIR / "deploy.py"),
                "--slug",     sl,
                "--company",  cn,
                "--site-dir", str(sd),
            ]
            proc = subprocess.Popen(
                cmd, cwd=str(SCRIPTS_DIR),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                env={**os.environ},
            )
            output_lines = []
            for line in proc.stdout:
                llog(line.rstrip())
                output_lines.append(line)
            proc.wait()

            all_output = "".join(output_lines)

            # Parseer ===DEPLOY_RESULT=== blok
            github_url    = ""
            cloudflare_url = ""
            try:
                start = all_output.index("===DEPLOY_RESULT===") + len("===DEPLOY_RESULT===")
                end   = all_output.index("===END_DEPLOY_RESULT===")
                result_json = json.loads(all_output[start:end].strip())
                github_url     = result_json.get("github_url",     "")
                cloudflare_url = result_json.get("cloudflare_url", "")
            except (ValueError, json.JSONDecodeError):
                pass

            ps = load_prospects()
            for j, pp in enumerate(ps):
                if slugify(pp.get("name", "")) == sl:
                    if proc.returncode == 0:
                        ps[j]["deploy_status"]  = "done"
                        ps[j]["github_url"]      = github_url
                        ps[j]["cloudflare_url"]  = cloudflare_url
                        llog(f"[OK]  Deploy klaar voor {cn}")
                    else:
                        ps[j]["deploy_status"] = "failed"
                        llog(f"[FAIL] Deploy mislukt voor {cn}")
                    break
            save_prospects(ps)

        threading.Thread(target=run_deploy, daemon=True).start()
        return jsonify({"ok": True})

    abort(404)


@app.get("/api/prospects/<slug>/deploy-status")
def api_deploy_status(slug):
    for p in load_prospects():
        if slugify(p.get("name", "")) == slug:
            return jsonify({
                "deploy_status":  p.get("deploy_status",  ""),
                "github_url":     p.get("github_url",     ""),
                "cloudflare_url": p.get("cloudflare_url", ""),
            })
    abort(404)


@app.post("/api/verify-deploys")
def api_verify_deploys():
    """Ping GitHub + Cloudflare Pages voor elke prospect met site_status=done.
    Fixt ook dubbele .pages.dev in cloudflare_url."""
    github_token = os.environ.get("GITHUB_TOKEN", "")
    github_user  = os.environ.get("GITHUB_USERNAME", "")
    gh_headers   = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
    } if github_token else {}

    ps = load_prospects()
    fixed = []

    for i, p in enumerate(ps):
        if p.get("site_status") != "done":
            continue

        changed = False
        slug = slugify(p.get("name", ""))

        # Fix dubbele .pages.dev in cloudflare_url
        cf = p.get("cloudflare_url", "") or ""
        if ".pages.dev.pages.dev" in cf:
            cf = cf.replace(".pages.dev.pages.dev", ".pages.dev")
            ps[i]["cloudflare_url"] = cf
            changed = True

        # Ping GitHub repo
        if p.get("deploy_status") != "done" and github_token and github_user:
            repo_name = f"site-{slug}"
            try:
                r = _requests.get(
                    f"https://api.github.com/repos/{github_user}/{repo_name}",
                    headers=gh_headers, timeout=8,
                )
                if r.status_code == 200:
                    ps[i]["deploy_status"] = "done"
                    if not ps[i].get("github_url"):
                        ps[i]["github_url"] = r.json().get("html_url", "")
                    changed = True
            except Exception:
                pass

        # Ping Cloudflare Pages URL (afleiden uit slug als niet opgeslagen)
        cf_url = ps[i].get("cloudflare_url", "") or ""
        if not cf_url:
            cf_url = f"https://site-{slug}.pages.dev"
        try:
            r = _requests.head(cf_url, timeout=8, allow_redirects=True)
            if r.status_code < 400:
                if ps[i].get("cloudflare_url") != cf_url:
                    ps[i]["cloudflare_url"] = cf_url
                    changed = True
        except Exception:
            pass

        if changed:
            fixed.append(p.get("name"))

    if fixed:
        save_prospects(ps)

    return jsonify({"ok": True, "fixed": fixed})


@app.get("/api/prospects/<slug>/mail")
def api_get_mail(slug):
    for p in load_prospects():
        if slugify(p.get("name", "")) == slug:
            mail_path = p.get("mail_path", "")
            if mail_path and Path(mail_path).exists():
                return jsonify({"ok": True, "mail": Path(mail_path).read_text(encoding="utf-8")})
            return jsonify({"ok": False, "error": "Mail nog niet gegenereerd"})
    abort(404)


@app.get("/api/usage")
def api_usage():
    force = request.args.get("force") == "1"
    return jsonify(get_usage(force=force))


@app.post("/api/usage/snapshot")
def api_usage_snapshot():
    """Sla huidige usage op als snapshot (voor before/after vergelijking)."""
    data  = get_usage(force=True)
    label = request.get_json(silent=True, force=True) or {}
    snap  = {
        **data,
        "label":      label.get("label", "Snapshot"),
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
    }
    snap_file = Path("/workspace/data/usage_snapshot.json")
    snap_file.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    return jsonify({"ok": True, "snapshot": snap})


@app.get("/api/usage/snapshot")
def api_usage_snapshot_get():
    snap_file = Path("/workspace/data/usage_snapshot.json")
    if not snap_file.exists():
        return jsonify(None)
    try:
        return jsonify(json.loads(snap_file.read_text(encoding="utf-8")))
    except Exception:
        return jsonify(None)


BRIDGE_URL = "http://host.docker.internal:8182"


@app.post("/api/chat")
def api_chat():
    """Stuur een bericht naar de lokale claude-bridge en stream het antwoord terug."""
    data    = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"error": "Geen bericht"}), 400

    def _stream():
        try:
            with _requests.post(
                f"{BRIDGE_URL}/chat",
                json={"message": message},
                stream=True,
                timeout=130,
            ) as r:
                for chunk in r.iter_content(chunk_size=None):
                    if chunk:
                        yield chunk
        except _requests.exceptions.ConnectionError:
            yield b"data: " + json.dumps({"error": "Bridge niet bereikbaar. Start bridge/bridge.py op de host."}).encode() + b"\n\ndata: [DONE]\n\n"
        except Exception as e:
            yield b"data: " + json.dumps({"error": str(e)}).encode() + b"\n\ndata: [DONE]\n\n"

    return Response(
        _stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/assets/<path:filename>")
def serve_root_assets(filename):
    """Afbeeldingen in Next.js public/ worden gevraagd vanaf /assets/ (root-relatief)."""
    referer = request.headers.get("Referer", "")
    m = re.search(r"/sites/([^/?#]+)", referer)
    if m:
        slug     = m.group(1)
        site_dir = _find_site_dir(slug)
        # Kijk in de project public/ map (naast /out)
        project_dir = site_dir.parent if site_dir.name == "out" else site_dir
        asset = project_dir / "public" / "assets" / filename
        if asset.exists():
            return send_from_directory(str(project_dir / "public" / "assets"), filename)
        # Fallback: in /out zelf
        asset2 = site_dir / "assets" / filename
        if asset2.exists():
            return send_from_directory(str(site_dir / "assets"), filename)
    abort(404)


@app.get("/_next/<path:filename>")
def serve_nextjs_root_assets(filename):
    """
    Next.js static export vraagt assets op van /_next/ (root-relatief).
    Gebruik de Referer header om te bepalen welke site de assets nodig heeft.
    """
    referer = request.headers.get("Referer", "")
    m = re.search(r"/sites/([^/?#]+)", referer)
    if m:
        slug     = m.group(1)
        site_dir = _find_site_dir(slug)
        asset    = site_dir / "_next" / filename
        if asset.exists():
            return send_from_directory(str(site_dir / "_next"), filename)
    abort(404)


@app.get("/sites/<slug>/")
def serve_site_index(slug):
    site_dir = _find_site_dir(slug)
    if not site_dir.exists():
        abort(404)
    return send_from_directory(str(site_dir), "index.html")


@app.get("/sites/<slug>/<path:filename>")
def serve_site_file(slug, filename):
    site_dir = _find_site_dir(slug)
    if not site_dir.exists():
        abort(404)
    # Next.js static export: routes zijn mappen met index.html erin
    target = site_dir / filename.rstrip("/")
    if target.is_dir() and (target / "index.html").exists():
        return send_from_directory(str(target), "index.html")
    return send_from_directory(str(site_dir), filename)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8181
    print(f"[INFO] Dashboard: http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
