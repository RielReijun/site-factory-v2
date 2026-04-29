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

BRIDGE_URL = os.getenv("BRIDGE_URL", "http://172.31.0.1:8182")
SCRIPTS_DIR = Path(__file__).resolve().parent
POLL_INTERVAL_S = int(os.getenv("AUTO_RESUME_POLL_S", "300"))   # 5 min
MAX_HOURS       = int(os.getenv("AUTO_RESUME_MAX_HOURS", "8"))


def _bridge_works() -> tuple[bool, str]:
    """True als de bridge een tiny prompt kan beantwoorden zonder usage error."""
    try:
        r = requests.post(
            f"{BRIDGE_URL}/api/claude",
            json={"prompt": "ok"},
            timeout=60,
        )
        data = r.json()
        err = (data.get("error") or "").lower()
        # Bekende rate-limit/usage error patterns
        is_limit = any(s in err for s in (
            "credit balance", "usage limit", "rate limit",
            "5-hour", "limit reached", "out of",
        ))
        if data.get("error"):
            return False, data["error"]
        return True, "ok"
    except Exception as e:
        return False, str(e)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="Prospect-naam")
    parser.add_argument("--from-step", default="generate",
                        help="Vanaf welke stap hervatten (default: generate)")
    args = parser.parse_args()

    deadline = time.time() + MAX_HOURS * 3600
    print(f"[INFO] auto_resume gestart voor: {args.name}")
    print(f"[INFO] Poll-interval: {POLL_INTERVAL_S}s, deadline: {MAX_HOURS}h")

    while time.time() < deadline:
        ok, msg = _bridge_works()
        if ok:
            print(f"[OK]  Bridge antwoordt — pipeline hervatten voor {args.name}")
            cmd = [
                "python", str(SCRIPTS_DIR / "run_pipeline.py"),
                "--name",      args.name,
                "--from-step", args.from_step,
                "--force",
            ]
            proc = subprocess.run(cmd, cwd=str(SCRIPTS_DIR))
            if proc.returncode == 0:
                print(f"[OK]  Pipeline voltooid voor {args.name}")
                return 0
            print(f"[WARN] Pipeline returnde exit {proc.returncode} — opnieuw wachten")
        else:
            print(f"[INFO] Bridge nog geblokkeerd: {msg[:80]}")

        remaining = int((deadline - time.time()) / 60)
        print(f"[INFO] Wacht {POLL_INTERVAL_S}s — nog {remaining}min binnen deadline")
        time.sleep(POLL_INTERVAL_S)

    print(f"[FAIL] Deadline van {MAX_HOURS}h verstreken zonder succes")
    return 1


if __name__ == "__main__":
    sys.exit(main())
