"""
pipeline_utils.py — Gedeelde hulpfuncties voor de site-factory pipeline.

Importeer vanuit alle scripts om duplicatie te vermijden.
"""
import json
import re
import time
from pathlib import Path

from bs4 import BeautifulSoup


# ── Bestandspaden ─────────────────────────────────────────────────────────────

WORKSPACE      = Path("/workspace")
PROSPECTS_FILE = WORKSPACE / "data" / "prospects.json"
DATA_DIR       = WORKSPACE / "data"
OUTPUT_DIR     = WORKSPACE / "output"
SCRIPTS_DIR    = WORKSPACE / "scripts"


# ── Placeholder-patronen (gedeeld door validate_generated_site + validate_generated_content) ──

PLACEHOLDER_PATTERNS_LABELED = [
    (r"\btodo\b",               "TODO"),
    (r"\btktk\b",               "TKTK"),
    (r"\[placeholder\]",        "[placeholder]"),
    (r"\bcoming soon\b",        "coming soon"),
    (r"\binsert text\b",        "insert text"),
    (r"\bvoorbeeldtekst\b",     "voorbeeldtekst"),
    (r"\blorem ipsum\b",        "lorem ipsum"),
]

PLACEHOLDER_PATTERNS_REGEX = [
    re.compile(r'\[BEDRIJFSNAAM\]',    re.IGNORECASE),
    re.compile(r'\[COMPANY\]',         re.IGNORECASE),
    re.compile(r'\[NAAM\]',            re.IGNORECASE),
    re.compile(r'\[PHONE\]',           re.IGNORECASE),
    re.compile(r'\[EMAIL\]',           re.IGNORECASE),
    re.compile(r'\[ADRES\]',           re.IGNORECASE),
    re.compile(r'\{\{[^}]+\}\}'),
    re.compile(r'Lorem\s+ipsum',        re.IGNORECASE),
    re.compile(r'Uw bedrijfsnaam',      re.IGNORECASE),
    re.compile(r'Voer hier',            re.IGNORECASE),
    re.compile(r'Insert company',       re.IGNORECASE),
    re.compile(r'example@example\.com', re.IGNORECASE),
    re.compile(r'info@example\.com',    re.IGNORECASE),
    re.compile(r'www\.example\.com',    re.IGNORECASE),
]


# ── Tekst- en bestandshulpfuncties ────────────────────────────────────────────

def read_text_file(path: Path) -> str:
    """Lees een tekstbestand; geef leeg string terug als het niet bestaat."""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def write_text_file(path: Path, text: str) -> None:
    """Schrijf tekst naar een bestand; maak tussenliggende mappen aan."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_json_file(path: Path, default=None):
    """Lees een JSON-bestand; geef default terug bij ontbreken of parse-fout."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (json.JSONDecodeError, OSError):
        return default


# ── Slugify ───────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    """Zet tekst om naar een URL-veilige slug."""
    text = text.strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


# ── Prospects ─────────────────────────────────────────────────────────────────

def load_prospects() -> list:
    """Laad prospects.json."""
    return json.loads(PROSPECTS_FILE.read_text(encoding="utf-8"))


# ── Tekst-extractie ───────────────────────────────────────────────────────────

def extract_visible_text(html: str) -> str:
    """Extraheer zichtbare tekst uit HTML, strip scripts/style/svg."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


# ── API retry wrapper ─────────────────────────────────────────────────────────

def with_retry(fn, max_retries: int = 4, base_wait: float = 30.0, label: str = ""):
    """
    Voer fn() uit met exponential-backoff retry bij rate-limit of tijdelijke fouten.
    fn mag een generator zijn (streaming) of een gewone call.
    Geeft het resultaat van fn() terug, of raise na max_retries pogingen.
    """
    import random
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except Exception as e:
            err = str(e).lower()
            is_retryable = any(kw in err for kw in (
                "rate_limit", "rate limit", "529", "overloaded",
                "timeout", "connection", "503", "502",
            ))
            if is_retryable and attempt < max_retries:
                wait = base_wait * (2 ** (attempt - 1)) + random.uniform(0, 5)
                tag = f" [{label}]" if label else ""
                print(f"[WARN]{tag} Poging {attempt}/{max_retries} mislukt ({e}) — wacht {wait:.0f}s")
                time.sleep(wait)
                last_exc = e
            else:
                raise
    raise last_exc


# ── Model helper ──────────────────────────────────────────────────────────────

def get_model() -> str:
    """Geef het geconfigureerde Anthropic-model terug."""
    import os
    return os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
