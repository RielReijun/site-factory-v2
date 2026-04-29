#!/usr/bin/env python3
"""
auto_resume.py — Detecteer wanneer de Claude Max bridge weer beschikbaar is en
hervat dan de pipeline voor een prospect.

Gebruik:
  python auto_resume.py --name "Plastica Thermoforming"

Werking:
  1. Pingt elke 5 min de bridge met een tiny prompt
  2. Als die OK antwoordt (geen usage-limit error): start de pipeline met
     --from-step generate. Smart-resume in run_pipeline.py slaat al-bestaande
     page-units automatisch over, dus resume kost alleen tijd voor de pages
     die nog ontbraken.
  3. Stopt zodra de pipeline succesvol klaar is, of na MAX_HOURS uur wachten.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

SCRIPTS_DIR = Path(__file__).resolve().parent
POLL_INTERVAL_S = int(os.getenv("AUTO_RESUME_POLL_S", "300"))   # 5 min
MAX_HOURS       = int(os.getenv("AUTO_RESUME_MAX_HOURS", "8"))

# Detecteer host vs container — host gebruikt localhost, container gateway IP
IN_CONTAINER = Path("/.dockerenv").exists() or Path("/workspace").exists()
DEFAULT_BRIDGE = "http://172.31.0.1:8182" if IN_CONTAINER else "http://localhost:8182"
BRIDGE_URL = os.getenv("BRIDGE_URL", DEFAULT_BRIDGE)
PROJECT_DIR = SCRIPTS_DIR.parent  # site-factory root op host


def _bridge_works() -> tuple[bool, str]:
    """True als de bridge een tiny prompt kan beantwoorden zonder usage error."""
    try:
        r = requests.post(
            f"{BRIDGE_URL}/api/claude",
            json={"prompt": "ok"},
            timeout=60,
        )
        data = r.json()
        if data.get("error"):
            return False, data["error"]
        return True, "ok"
    except Exception as e:
        return False, str(e)


def _pipeline_running() -> bool:
    """Check of er een worker-container actief is met run_pipeline.py."""
    try:
        out = subprocess.run(
            ["docker", "ps", "--filter", "name=site-factory-worker-run",
             "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5,
        )
        return bool(out.stdout.strip())
    except Exception:
        return False


def _site_done(name: str) -> bool:
    """Check of de prospect al volledig is."""
    import json
    candidates = [
        Path("/workspace/data/prospects.json"),  # in worker container
        Path(__file__).resolve().parent.parent / "data" / "prospects.json",  # on host
    ]
    for prospects_file in candidates:
        if not prospects_file.exists():
            continue
        try:
            ps = json.loads(prospects_file.read_text(encoding="utf-8"))
            for p in ps:
                if p.get("name") == name:
                    return p.get("site_status") == "done" and p.get("review_status") == "done"
            return False
        except Exception:
            continue
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="Prospect-naam")
    parser.add_argument("--from-step", default="generate",
                        help="Vanaf welke stap hervatten (default: generate)")
    args = parser.parse_args()

    deadline = time.time() + MAX_HOURS * 3600
    print(f"[INFO] auto_resume gestart voor: {args.name}")
    print(f"[INFO] Poll-interval: {POLL_INTERVAL_S}s, deadline: {MAX_HOURS}h")
    print(f"[INFO] Wacht alleen actief in als (a) er geen pipeline al draait, "
          f"(b) site nog niet 'done' is, (c) de bridge werkt.")

    while time.time() < deadline:
        # Stop als de site inmiddels al af is (oorspronkelijke run was gewoon gelukt)
        if _site_done(args.name):
            print(f"[OK]  Site '{args.name}' is al 'done' — auto_resume klaar")
            return 0

        # Niet ingrijpen als de pipeline nu draait (oorspronkelijke run is bezig)
        if _pipeline_running():
            remaining = int((deadline - time.time()) / 60)
            print(f"[INFO] Pipeline draait al, ik wacht. Nog {remaining}min binnen deadline.")
            time.sleep(POLL_INTERVAL_S)
            continue

        # Geen pipeline + nog niet done: kans om over te nemen mits bridge werkt
        ok, msg = _bridge_works()
        if not ok:
            print(f"[INFO] Bridge geblokkeerd: {msg[:80]}, wacht {POLL_INTERVAL_S}s")
            time.sleep(POLL_INTERVAL_S)
            continue

        print(f"[OK]  Bridge antwoordt + geen pipeline draait, start hervatting voor {args.name}")
        if IN_CONTAINER:
            cmd = [
                "python", str(SCRIPTS_DIR / "run_pipeline.py"),
                "--name",      args.name,
                "--from-step", args.from_step,
                "--force",
            ]
            proc = subprocess.run(cmd, cwd=str(SCRIPTS_DIR))
        else:
            # Op host: gebruik docker compose om de pipeline in worker te draaien
            cmd = [
                "docker", "compose", "run", "--rm",
                "-e", "USE_CLAUDE_MAX=true",
                "worker", "python", "/workspace/scripts/run_pipeline.py",
                "--name",      args.name,
                "--from-step", args.from_step,
                "--force",
            ]
            proc = subprocess.run(cmd, cwd=str(PROJECT_DIR))
        if proc.returncode == 0 and _site_done(args.name):
            print(f"[OK]  Pipeline voltooid voor {args.name}")
            return 0
        print(f"[WARN] Pipeline returnde exit {proc.returncode}, opnieuw proberen")
        time.sleep(POLL_INTERVAL_S)

    print(f"[FAIL] Deadline van {MAX_HOURS}h verstreken zonder succes")
    return 1


if __name__ == "__main__":
    sys.exit(main())
