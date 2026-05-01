"""v2 Dashboard — overzicht van alle v2 sites + live preview.

Flask-app op poort 8281 (v1's dashboard zit op 8181). Leest
artifacts/v2/_index.json voor prospect-status, serveert per prospect de
gebouwde dist/ als preview, en biedt build-on-demand voor prospects waar
nog geen dist/ van bestaat.

Run via docker:
    docker run --rm \
      -v /home/ryan-poser/site-factory:/workspace \
      -w /workspace \
      -p 8281:8281 \
      site-factory-worker \
      bash -lc 'pip install -q -r /workspace/v2/requirements.txt && \
                python3 /workspace/v2/dashboard/server.py'

Open http://localhost:8281 om alle v2 sites te bekijken.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

PROJECT_DIR = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = PROJECT_DIR / "artifacts" / "v2"
INDEX_PATH = ARTIFACTS_DIR / "_index.json"

app = Flask(__name__)

# Build-status cache: { slug: {"status": "idle"|"building"|"done"|"failed", "log": "..."} }
_BUILD_STATUS: dict[str, dict] = {}
_BUILD_LOCK = threading.Lock()


def _read_index() -> dict:
    if not INDEX_PATH.exists():
        return {"prospects": [], "summary": {}, "generated_at": ""}
    try:
        return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"prospects": [], "summary": {}, "generated_at": ""}


def _has_built_dist(slug: str) -> bool:
    return (ARTIFACTS_DIR / slug / "astro-source" / "dist" / "index.html").exists()


def _has_reference(slug: str) -> bool:
    return (ARTIFACTS_DIR / slug / "_reference.png").exists()


def _enrich_prospect(p: dict) -> dict:
    """Vul runtime-info aan: of dist/ aanwezig is, of een build draait."""
    slug = p["slug"]
    p["has_dist"]      = _has_built_dist(slug)
    p["has_reference"] = _has_reference(slug)
    p["build_status"]  = _BUILD_STATUS.get(slug, {}).get("status", "idle")
    return p


@app.get("/")
def dashboard():
    # Als de browser op "/" landt vanuit een prospect-pagina (klik op Home in
    # nav of footer), respecteer de site-context en redirect terug naar de
    # prospect-home i.p.v. naar het dashboard.
    referer = request.headers.get("Referer", "")
    from urllib.parse import urlparse
    referer_path = urlparse(referer).path or ""
    site_match = re.match(r"^/sites/([^/]+)/", referer_path)
    if site_match:
        return redirect(f"/sites/{site_match.group(1)}/", code=302)

    doc = _read_index()
    prospects = [_enrich_prospect(dict(p)) for p in doc.get("prospects", [])]
    # Sorteer: gerenderd én gate-ok eerst, dan rest, dan failed.
    def _sort_key(p):
        gate_ok = p.get("gate", {}).get("ok", False)
        rendered = p.get("render_status") == "rendered"
        return (
            0 if (gate_ok and rendered) else (1 if rendered else 2),
            -p.get("gate", {}).get("passes", 0),
            p.get("slug", ""),
        )
    prospects.sort(key=_sort_key)

    # Tellingen voor de header
    counts = {
        "total":       len(prospects),
        "ready":       sum(1 for p in prospects if p.get("gate", {}).get("ok") and p.get("render_status") == "rendered"),
        "gate_failed": sum(1 for p in prospects if not p.get("gate", {}).get("ok") and p.get("render_status") == "rendered"),
        "by_archetype": {},
        "with_dist":   sum(1 for p in prospects if p.get("has_dist")),
    }
    for p in prospects:
        a = p.get("archetype", "unknown")
        counts["by_archetype"][a] = counts["by_archetype"].get(a, 0) + 1

    return render_template(
        "dashboard.html",
        prospects=prospects,
        counts=counts,
        generated_at=doc.get("generated_at", ""),
    )


@app.get("/api/index")
def api_index():
    doc = _read_index()
    doc["prospects"] = [_enrich_prospect(dict(p)) for p in doc.get("prospects", [])]
    return jsonify(doc)


@app.get("/api/build-status/<slug>")
def api_build_status(slug: str):
    return jsonify({
        "slug":   slug,
        "status": _BUILD_STATUS.get(slug, {}).get("status", "idle"),
        "log":    _BUILD_STATUS.get(slug, {}).get("log", "")[-2000:],
    })


def _astro_build_in_thread(slug: str) -> None:
    """Achtergrond-build: npm install + npx astro build via docker."""
    source_dir = ARTIFACTS_DIR / slug / "astro-source"
    if not source_dir.exists():
        with _BUILD_LOCK:
            _BUILD_STATUS[slug] = {"status": "failed", "log": f"astro source ontbreekt: {source_dir}"}
        return
    rel = source_dir.relative_to(PROJECT_DIR)
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{PROJECT_DIR}:/workspace",
        "-w", f"/workspace/{rel}",
        "site-factory-worker",
        "bash", "-lc",
        "npm install --silent 2>&1 | tail -3 && npx astro build 2>&1 | tail -8",
    ]
    with _BUILD_LOCK:
        _BUILD_STATUS[slug] = {"status": "building", "log": ""}
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        log = (proc.stdout + proc.stderr)[-4000:]
        ok = proc.returncode == 0 and (source_dir / "dist" / "index.html").exists()
        with _BUILD_LOCK:
            _BUILD_STATUS[slug] = {"status": "done" if ok else "failed", "log": log}
    except subprocess.TimeoutExpired:
        with _BUILD_LOCK:
            _BUILD_STATUS[slug] = {"status": "failed", "log": "build timeout (>240s)"}
    except Exception as exc:
        with _BUILD_LOCK:
            _BUILD_STATUS[slug] = {"status": "failed", "log": f"build exception: {exc}"}


@app.post("/api/build/<slug>")
def api_build(slug: str):
    if not (ARTIFACTS_DIR / slug).exists():
        return jsonify({"ok": False, "error": "onbekende slug"}), 404
    current = _BUILD_STATUS.get(slug, {}).get("status", "idle")
    if current == "building":
        return jsonify({"ok": True, "status": "building", "message": "build al actief"})
    threading.Thread(target=_astro_build_in_thread, args=(slug,), daemon=True).start()
    return jsonify({"ok": True, "status": "building"})


@app.get("/sites/<slug>/")
@app.get("/sites/<slug>/<path:filename>")
def serve_site(slug: str, filename: str = "index.html"):
    """Serve het gebouwde dist/ van een prospect."""
    dist_dir = ARTIFACTS_DIR / slug / "astro-source" / "dist"
    if not dist_dir.exists():
        return (
            f"<h1>Site nog niet gebouwd</h1>"
            f"<p>Klik op 'Bouw site' op het dashboard, of run "
            f"<code>npm install &amp;&amp; npx astro build</code> in "
            f"<code>{dist_dir.parent}</code>.</p>"
            f"<p><a href='/'>← Terug naar dashboard</a></p>",
            404,
        )
    target = dist_dir / filename
    if target.is_dir():
        target = target / "index.html"
    if not target.exists():
        # Probeer trailingslash-style ('/foo' → '/foo/index.html')
        candidate = dist_dir / f"{filename.rstrip('/')}/index.html"
        if candidate.exists():
            target = candidate
        else:
            abort(404)
    return send_from_directory(dist_dir, str(target.relative_to(dist_dir)))


@app.get("/screenshots/<slug>/<path:filename>")
def serve_screenshot(slug: str, filename: str):
    screenshot_dir = ARTIFACTS_DIR / slug / "screenshots"
    if not screenshot_dir.exists():
        abort(404)
    return send_from_directory(screenshot_dir, filename)


@app.get("/reference/<slug>")
def serve_reference(slug: str):
    """Originele website-screenshot (voor side-by-side vergelijk)."""
    target = ARTIFACTS_DIR / slug / "_reference.png"
    if not target.exists():
        abort(404)
    return send_from_directory(ARTIFACTS_DIR / slug, "_reference.png")


# ── Root-relative routing via Referer ────────────────────────────────────────
# Astro produceert HTML met absolute paden — zowel voor assets (/_astro/...,
# /assets/hero.jpg) als voor subpagina-navigatie (/over-mij/, /tarieven/).
# Browser vraagt die op vanaf het dashboard-root i.p.v. /sites/<slug>/...
# Fix: Referer-header parseren om te weten welke prospect bedoeld is.
import re
from urllib.parse import urlparse
from flask import redirect

_REFERER_SLUG_RE = re.compile(r"^/sites/([^/]+)/")
_ROOT_ASSET_PREFIXES = ("_astro/", "assets/", "favicon.")
# Dashboard-eigen routes die we nooit naar prospect-dist mogen redirecten.
_DASHBOARD_PATHS = re.compile(r"^(api|sites|screenshots|health|static)(/|$)")


@app.get("/<path:rootpath>")
def serve_root_relative_via_referer(rootpath: str):
    """Catch-all voor root-relative paths uit een prospect-pagina.

    - Asset-prefixes (/_astro/*, /assets/*, /favicon.*): serveer direct uit
      het dist/-pad van de slug die in de Referer staat.
    - Andere paths (/over-mij/, /tarieven/): 302-redirect naar
      /sites/<slug>/<path> zodat de URL-bar correct meeloopt.
    """
    # Voorkom recursie op dashboard-eigen paths
    if _DASHBOARD_PATHS.match(rootpath):
        abort(404)

    referer = request.headers.get("Referer", "")
    parsed = urlparse(referer)
    match = _REFERER_SLUG_RE.match(parsed.path or "")
    if not match:
        abort(404)
    slug = match.group(1)

    # Asset-prefixes serveren we direct uit de dist (niet redirecten —
    # browser cached relative URLs anders inconsistent).
    if any(rootpath.startswith(p) for p in _ROOT_ASSET_PREFIXES):
        dist_dir = ARTIFACTS_DIR / slug / "astro-source" / "dist"
        target = dist_dir / rootpath
        if not target.exists():
            abort(404)
        return send_from_directory(dist_dir, rootpath)

    # Sub-pagina navigatie: redirect naar /sites/<slug>/<path> zodat de
    # browser-URL klopt en vervolg-clicks weer juiste Referer hebben.
    target_url = f"/sites/{slug}/{rootpath}"
    # Behoud trailing slash zoals Astro die graag heeft (trailingSlash:always)
    if not target_url.endswith("/") and "." not in rootpath.split("/")[-1]:
        target_url += "/"
    return redirect(target_url, code=302)


@app.get("/health")
def health():
    return jsonify({"ok": True, "index_exists": INDEX_PATH.exists()})


if __name__ == "__main__":
    port = int(os.getenv("V2_DASHBOARD_PORT", "8281"))
    print(f"[INFO] v2 dashboard op http://0.0.0.0:{port}")
    print(f"[INFO] index: {INDEX_PATH} (exists={INDEX_PATH.exists()})")
    app.run(host="0.0.0.0", port=port, debug=False)
