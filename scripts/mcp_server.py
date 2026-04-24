#!/usr/bin/env python3
"""
mcp_server.py — MCP-server voor de Site Factory pipeline.

Geeft Claude Code (VS Code + bridge) directe tools om prospects te beheren
en de pipeline te besturen zonder handmatige docker-commandos.

Tools:
  list_prospects      — overzicht van alle prospects
  add_prospect        — voeg nieuwe prospect toe
  run_pipeline        — start pipeline op de achtergrond
  get_pipeline_status — lees huidige pipeline-status
  get_pipeline_log    — lees recente pipeline-log
  reactivate_prospect — reactiveer getrashte prospect
  get_prospect_details— gedetailleerde info over één prospect
"""
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DATA_DIR       = Path(__file__).parent.parent / "data"
PROSPECTS_FILE = DATA_DIR / "prospects.json"
STATUS_FILE    = DATA_DIR / "pipeline_status.json"
LOG_FILE       = DATA_DIR / "pipeline.log"

mcp = FastMCP("site-factory")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load() -> list:
    return json.loads(PROSPECTS_FILE.read_text(encoding="utf-8"))


def _save(prospects: list) -> None:
    payload = json.dumps(prospects, indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), prefix=".prospects_tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, str(PROSPECTS_FILE))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def list_prospects() -> str:
    """Geef een overzicht van alle prospects en hun pipeline-status."""
    prospects = _load()
    lines = []
    for p in prospects:
        name   = p.get("name", "?")
        url    = p.get("url", "")
        status = p.get("status", "unknown")

        if status == "trashed":
            lines.append(f"- {name} [{status}]")
            continue

        stages = []
        if status == "collected":
            stages.append("collect✓")
        if p.get("research_status") == "done":
            stages.append("research✓")
        if p.get("briefing_status") == "done":
            stages.append("brief✓")
        if p.get("site_status") == "done":
            stages.append("site✓")
        if p.get("deploy_status") == "done":
            stages.append("deploy✓")

        stage_str = " ".join(stages) if stages else "nieuw"
        lines.append(f"- {name} ({url})  [{stage_str}]")

    return f"{len(prospects)} prospects totaal:\n" + "\n".join(lines)


@mcp.tool()
def add_prospect(name: str, url: str, reference_url: str = "") -> str:
    """
    Voeg een nieuwe prospect toe aan de pipeline.

    Args:
        name:          Naam van het bedrijf (bijv. 'Kapsalon De Knip')
        url:           Website URL (bijv. 'https://www.kapsalondeknik.nl')
        reference_url: Optionele URL van een referentiesite voor designinspiratie
    """
    prospects = _load()
    existing = {p["name"].strip().lower() for p in prospects}

    if name.strip().lower() in existing:
        return f"Prospect '{name}' bestaat al. Gebruik list_prospects om de status te zien."

    entry: dict = {
        "name":     name,
        "url":      url,
        "status":   "new",
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    if reference_url:
        entry["reference_url"] = reference_url

    prospects.append(entry)
    _save(prospects)
    return f"Prospect '{name}' toegevoegd ({url})."


@mcp.tool()
def run_pipeline(name: str, from_step: str = "collect") -> str:
    """
    Start de volledige pipeline voor een prospect op de achtergrond.

    Args:
        name:      Naam van de prospect zoals die in de lijst staat
        from_step: Startpunt: collect, research, brief, generate of validate
    """
    prospects = _load()
    found = any(p["name"].strip().lower() == name.strip().lower() for p in prospects)
    if not found:
        return f"Prospect '{name}' niet gevonden. Gebruik list_prospects om de exacte naam te zien."

    cmd = [
        "docker", "exec", "-d", "site-factory-worker",
        "python", "/workspace/scripts/run_pipeline.py",
        "--name", name, "--from-step", from_step,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return f"Pipeline gestart voor '{name}' (vanaf stap: {from_step}). Gebruik get_pipeline_log om de voortgang te volgen."
    return f"Fout bij starten pipeline: {result.stderr.strip() or result.stdout.strip()}"


@mcp.tool()
def get_pipeline_status() -> str:
    """Lees de huidige status van de pipeline (welke prospect, welke stap, actief of niet)."""
    if not STATUS_FILE.exists():
        return "Geen pipeline-statusbestand gevonden — pipeline is nog nooit gestart."
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        if not data.get("running"):
            result  = data.get("result", "")
            updated = data.get("updated_at", "?")
            return f"Pipeline staat stil.\nLaatste run: {data.get('prospect', '?')} — {result or 'klaar'}\nBijgewerkt: {updated}"
        return (
            f"Pipeline actief: {data.get('prospect', '?')}\n"
            f"Stap {data.get('step_n', '?')}/{data.get('total', '?')}: {data.get('step', '?')}\n"
            f"Bijgewerkt: {data.get('updated_at', '?')}"
        )
    except Exception as e:
        return f"Fout bij lezen status: {e}"


@mcp.tool()
def get_pipeline_log(lines: int = 40) -> str:
    """
    Lees de laatste regels van het pipeline-log.

    Args:
        lines: Aantal regels om te tonen (default 40, max 200)
    """
    if not LOG_FILE.exists():
        return "Geen logbestand gevonden."
    try:
        content   = LOG_FILE.read_text(encoding="utf-8", errors="ignore")
        log_lines = content.splitlines()
        n         = min(lines, 200)
        return "\n".join(log_lines[-n:])
    except Exception as e:
        return f"Fout bij lezen log: {e}"


@mcp.tool()
def reactivate_prospect(name: str, keep_collected: bool = False) -> str:
    """
    Reactiveer een getrashte of gestopte prospect.

    Args:
        name:           Naam van de prospect
        keep_collected: True = bewaar collect-data en start vanaf research.
                        False (default) = volledige reset, start opnieuw.
    """
    prospects = _load()
    for p in prospects:
        if p["name"].strip().lower() == name.strip().lower():
            old_status = p.get("status", "unknown")
            p.pop("trashed_at", None)
            p.pop("trash_path", None)
            p["added_at"] = datetime.now(timezone.utc).isoformat()

            if keep_collected and p.get("collected_path"):
                p["status"] = "collected"
                for field in ["research_status", "research_path",
                               "briefing_status", "briefing_path",
                               "site_status", "mail_status", "mail_path",
                               "deploy_status", "github_url", "cloudflare_url"]:
                    p.pop(field, None)
                _save(prospects)
                return f"Prospect '{name}' gereactiveerd met bestaande collect-data (was: {old_status}). Pipeline start vanaf research."
            else:
                p["status"] = "new"
                for field in ["collected_path", "research_status", "research_path",
                               "briefing_status", "briefing_path", "site_status",
                               "mail_status", "mail_path", "deploy_status",
                               "github_url", "cloudflare_url"]:
                    p.pop(field, None)
                _save(prospects)
                return f"Prospect '{name}' volledig gereset (was: {old_status}). Pipeline start opnieuw."
    return f"Prospect '{name}' niet gevonden."


@mcp.tool()
def get_prospect_details(name: str) -> str:
    """
    Geef gedetailleerde informatie over één prospect.

    Args:
        name: Naam van de prospect
    """
    prospects = _load()
    for p in prospects:
        if p["name"].strip().lower() == name.strip().lower():
            return json.dumps(p, indent=2, ensure_ascii=False)
    return f"Prospect '{name}' niet gevonden."


if __name__ == "__main__":
    mcp.run()
