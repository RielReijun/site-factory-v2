"""Mine 'eigen woorden' uit de originele site: taglines, CTA-labels,
section-headings en stats.

Doel: vervang generieke render-copy ('Wat ik voor je doe', 'Online
reserveren') door wat de prospect zelf op z'n eigen site schreef. Geen
LLM. BeautifulSoup voor HTML, regex voor patroon-detectie.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

try:
    from bs4 import BeautifulSoup
    _BS_AVAILABLE = True
except ImportError:
    _BS_AVAILABLE = False


# ── Data ─────────────────────────────────────────────────────────────────────
@dataclass
class Stat:
    """Een geëxtraheerde stat: '30 jaar', '200+ klanten'."""
    value: str
    label: str
    raw: str
    source_line: str = ""


@dataclass
class SourceCopy:
    tagline: str = ""                              # langere zin uit og:description / h1
    cta_labels: list[str] = field(default_factory=list)   # alle CTA-knop teksten
    primary_cta: str = ""                          # beste CTA voor hero/contact
    section_headings: list[str] = field(default_factory=list)  # h2's
    stats: list[Stat] = field(default_factory=list)
    sources_found: list[str] = field(default_factory=list)
    method: str = "extracted"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Helpers ──────────────────────────────────────────────────────────────────
_NAV_HEADING_BLACKLIST = re.compile(
    r"(?i)(menu|navigation|cookie|privacy|toggle|search|footer|header|"
    r"sluiten|sluit|close|skip)"
)
_CTA_KEYWORDS = re.compile(
    r"(?i)\b(boek|reserveer|maak\s+een\s+afspraak|plan\s+(?:je|uw)|vraag\s+(?:offerte|info)|"
    r"contact|bel|app\s+ons|whatsapp|inschrij|aanmeld|bekijk|ontdek|meer\s+info|"
    r"free\s+quote|book|order|aankoop)"
)
_GENERIC_CTA_BLACKLIST = re.compile(
    r"(?i)^(home|over\s*(ons|mij)|menu|cookie|accept|sluiten|close|skip|"
    r"toggle|terug|next|prev|vorige|volgende|x|×|read\s+more|lees\s+meer)$"
)
_STATS_RE = re.compile(
    r"\b(\d{1,4}\+?)\s+"
    r"(jaar(?:s)?|jaren|klanten|projecten|behandelingen|opleidingen|cursussen|"
    r"medewerkers|locaties|tuinen|huizen|panden|specialisten|kenners)"
    r"(?:\s+(?:ervaring|tevreden|gerealiseerd|opgeleverd|service))?",
    re.IGNORECASE,
)

# Voor "jaar" als label moet de zin context-woorden bevatten — anders matchen
# we vaak iemand's leeftijd of een willekeurige duur. "30 jaar in het vak"
# is een echte stat; "29 jaar oud" is een persoonlijk detail.
_YEAR_CONTEXT_RE = re.compile(
    r"(?i)\b(ervaring|werkzaam|in\s+het\s+vak|sinds|gevestigd|bestaan|"
    r"actief\s+(?:al|als)|specialist|trots\s+op|opgericht|bedrijf|salon|"
    r"onderneming|vakman|gipskunstenaar|kapper|hovenier|"
    r"noem\s+(?:ik|mijzelf)|al\s+(?:meer\s+dan\s+)?\d+\s+jaar|"
    r"jaar\s+(?:het|in\s+))"
)
# Sterke leeftijds-indicatie: "ik ben 29 jaar" / "is 29 jaar" / "29 jaar oud"
_AGE_INDICATOR_RE = re.compile(
    r"(?i)\b(?:ben|is|word(?:t)?|wordt|ouder)\s+\d+\s+jaar\b|\b\d+\s+jaar\s+oud\b"
)


# ── Public API ───────────────────────────────────────────────────────────────
def extract_source_copy(raw_html: str, text: str = "") -> SourceCopy:
    if not raw_html or not _BS_AVAILABLE:
        return SourceCopy(method="unavailable" if not _BS_AVAILABLE else "no_html")

    out = SourceCopy()
    try:
        soup = BeautifulSoup(raw_html, "html.parser")
    except Exception:
        return SourceCopy(method="parse_failed")

    # ── 1. Tagline ────────────────────────────────────────────────────────
    # Prefer og:description (meestal door eigenaar zelf gekozen), dan twitter,
    # dan eerste betekenisvolle h1 op homepage.
    def _is_quality_tagline(cand: str) -> bool:
        """Tagline moet betekenisvolle volzin zijn, niet een nav-stripe of
        scraped page-text. Eis min 40 chars (bewust hoger dan 30 om
        'Afspraak maken en vragen & antwoorden' uit te sluiten),
        skip als phone/email/nav-keywords erin staan."""
        if not (40 <= len(cand) <= 240):
            return False
        if _NAV_HEADING_BLACKLIST.search(cand):
            return False
        if re.search(r"\b0\d{1,2}[-\s]?\d{6,}|\+31|tel\.|toggle", cand, re.IGNORECASE):
            return False
        # Moet minstens 5 woorden hebben
        if len(cand.split()) < 5:
            return False
        return True

    og = soup.find("meta", attrs={"property": "og:description"})
    if og and og.get("content"):
        cand = og["content"].strip()
        if _is_quality_tagline(cand):
            out.tagline = cand
            out.sources_found.append("og:description")

    if not out.tagline:
        twitter = soup.find("meta", attrs={"name": "twitter:description"})
        if twitter and twitter.get("content"):
            cand = twitter["content"].strip()
            if _is_quality_tagline(cand):
                out.tagline = cand
                out.sources_found.append("twitter:description")

    # Fallback: meta name=description
    if not out.tagline:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            cand = meta_desc["content"].strip()
            if _is_quality_tagline(cand):
                out.tagline = cand
                out.sources_found.append("meta:description")

    # Laatste fallback: een h2 die als payoff klinkt
    if not out.tagline:
        for h2 in soup.find_all("h2"):
            t = h2.get_text(strip=True)
            if _is_quality_tagline(t):
                if not re.match(r"(?i)^(tarieven|openingstijden|contact|adres)", t):
                    out.tagline = t
                    out.sources_found.append("h2")
                    break

    # ── 2. CTA labels ─────────────────────────────────────────────────────
    cta_found: list[str] = []
    seen_labels: set[str] = set()
    for el in soup.find_all(["button", "a"]):
        text_btn = el.get_text(" ", strip=True)
        if not text_btn or len(text_btn) > 35:
            continue
        if _GENERIC_CTA_BLACKLIST.match(text_btn.strip()):
            continue
        normalized = text_btn.lower().strip()
        if normalized in seen_labels:
            continue

        classes = " ".join(el.get("class", []) or []).lower()
        href = (el.get("href") or "").lower()

        looks_like_cta = (
            "btn" in classes
            or "button" in classes
            or "cta" in classes
            or _CTA_KEYWORDS.search(text_btn)
            or href.startswith("tel:")
            or href.startswith("mailto:")
            or "contact" in href
            or "afspraak" in href
            or "boek" in href
            or "reserve" in href
        )
        if not looks_like_cta:
            continue
        if 4 <= len(text_btn) <= 35:
            seen_labels.add(normalized)
            cta_found.append(text_btn)
    out.cta_labels = cta_found[:15]

    # Pick primary CTA: prefer "boek"/"reserveer"/"vraag offerte"/"plan"
    for label in cta_found:
        l = label.lower()
        if any(kw in l for kw in ("boek nu", "reserveer", "maak een afspraak",
                                   "vraag offerte", "plan een afspraak",
                                   "boek je", "boek uw")):
            out.primary_cta = label
            break

    # ── 3. Section headings (h2's, gefilterd) ─────────────────────────────
    headings: list[str] = []
    seen_h: set[str] = set()
    for h in soup.find_all(["h2", "h3"]):
        t = h.get_text(" ", strip=True)
        if not (3 <= len(t) <= 60):
            continue
        if _NAV_HEADING_BLACKLIST.search(t):
            continue
        # Skip puur numeriek of all-caps forced
        if t.isdigit() or t.isupper():
            continue
        norm = t.lower()
        if norm in seen_h:
            continue
        seen_h.add(norm)
        headings.append(t)
        if len(headings) >= 12:
            break
    out.section_headings = headings

    # ── 4. Stats: '30 jaar', '200+ klanten' ───────────────────────────────
    body_text = text or soup.get_text(" ", strip=True)
    stats: list[Stat] = []
    seen_stat: set[str] = set()
    for line in body_text.splitlines():
        line = line.strip()
        if not line or len(line) > 220:
            continue
        for match in _STATS_RE.finditer(line):
            value = match.group(1)
            label = match.group(2)
            label_norm = label.lower().rstrip("s")
            # Filter false-positives:
            # - jaartallen (1900-2099) die per ongeluk matchen als '2003 tuinen'
            # - rare formats als '000 medewerkers'
            # - te kleine waardes (< 3) tenzij "+"-suffix
            value_clean = value.rstrip("+")
            if not value_clean.isdigit():
                continue
            value_int = int(value_clean)
            if 1900 <= value_int <= 2099:
                continue
            if value_clean.startswith("0"):
                continue
            if value_int < 3 and "+" not in value:
                continue
            # Voor 'jaar'-stats specifiek: eis context-woord OF accepteer
            # bij hogere waardes (16+) waar leeftijds-context uitgesloten is.
            if label_norm == "jaar":
                # Skip als regel duidelijk over leeftijd gaat
                if _AGE_INDICATOR_RE.search(line):
                    continue
                # Eis context-keyword voor lage waardes (<16)
                if value_int < 16 and not _YEAR_CONTEXT_RE.search(line):
                    continue
                # 16-80 jaar = realistische business-tijd
                if 16 <= value_int <= 80:
                    pass  # accepteer
                else:
                    # >80 onrealistisch (geen MKB heeft 100+ jaar ervaring)
                    continue
            # Onrealistisch hoge waardes overslaan ("100 jaar ervaring" mag,
            # "999+ projecten" mag, maar "1234 klanten" zonder context = ruis)
            if value_int > 200 and "+" not in value and label_norm not in {"klanten", "projecten"}:
                continue
            key = f"{value}_{label_norm}"
            if key in seen_stat:
                continue
            seen_stat.add(key)
            stats.append(Stat(
                value=value,
                label=label,
                raw=match.group(0),
                source_line=line[:160],
            ))
            if len(stats) >= 8:
                break
        if len(stats) >= 8:
            break
    out.stats = stats[:4]   # cap op 4 voor render

    return out
