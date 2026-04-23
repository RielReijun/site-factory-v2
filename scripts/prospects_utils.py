"""
prospects_utils.py — Thread- en process-safe lees/schrijf hulpfuncties voor prospects.json.

Gebruikt fcntl.flock voor exclusieve bestandsvergrendeling zodat parallelle
pipeline-runs elkaar niet overschrijven.
Schrijft via tmp-bestand + atomic rename zodat een crash nooit een corrupt JSON achterlaat.
"""
import fcntl
import json
import os
import tempfile
from pathlib import Path

PROSPECTS_FILE = Path("/workspace/data/prospects.json")


def load_prospects() -> list:
    """Lees prospects.json (zonder lock — alleen voor lezen)."""
    return json.loads(PROSPECTS_FILE.read_text(encoding="utf-8"))


def _atomic_write(path: Path, data: list) -> None:
    """Schrijf JSON naar path via een tmp-bestand + atomic rename (crash-safe)."""
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    dir_    = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_), prefix=".prospects_tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tf:
            tf.write(payload)
        os.replace(tmp_path, str(path))  # atomic op Linux
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def save_prospects(prospects: list) -> None:
    """Schrijf prospects.json met exclusieve lock en atomic rename."""
    with open(PROSPECTS_FILE, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            _atomic_write(PROSPECTS_FILE, prospects)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def update_prospect(name: str, **fields) -> None:
    """
    Atomische read-modify-write: update velden van één prospect op naam.
    Leest de huidige staat op het moment van schrijven zodat parallelle updates
    van andere prospects niet verloren gaan. Schrijft crash-safe via tmp+rename.
    """
    name_lower = name.strip().lower()
    with open(PROSPECTS_FILE, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            prospects = json.loads(f.read())
            for i, p in enumerate(prospects):
                if p.get("name", "").strip().lower() == name_lower:
                    prospects[i].update(fields)
                    break
            _atomic_write(PROSPECTS_FILE, prospects)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
