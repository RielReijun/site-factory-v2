"""Content inventory voor v2.

Bouwt uit de v1 collected data (text.txt, raw.html, pages.json,
structured_data.json, meta.json, images.json) een gestructureerde dataset met
alles wat we *zeker weten* uit de bron, met provenance per veld.

Dit is een proof-of-concept: doel is bewijzen dat de raw crawl al rijk genoeg
is om een quality gate op te draaien. Geen archetype-binding, geen LLM.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .source_copy import SourceCopy, extract_source_copy
from .typography import Typography, extract_typography
from .visual_dna import VisualDNA, extract_visual_dna
from .voice_profile import VoiceProfile, extract_voice_profile


# ── Types ────────────────────────────────────────────────────────────────────
@dataclass
class Provenance:
    source: str          # "text.txt:524" of "structured_data.json:contact.phone"
    confidence: str      # "high", "medium", "low"


@dataclass
class PriceItem:
    label: str
    amount: str          # "€36,50"
    duration_min: int | None
    category: str
    source: Provenance


@dataclass
class Treatment:
    name: str
    source: Provenance


@dataclass
class BrandMention:
    name: str
    occurrences: int
    source: Provenance


@dataclass
class HoursRow:
    day: str
    range: str
    source: Provenance


@dataclass
class Review:
    quote: str
    attribution: str
    source: Provenance


@dataclass
class Signature:
    """Een verbatim zin uit de bron die de stem van het bedrijf vangt.

    Niet hertaald, niet samengevat. Bedoeld om de hero, about-sectie of CTA
    te vullen met taal die de prospect zelf herkent.
    """
    quote: str
    score: int
    page: str
    source: Provenance


@dataclass
class ContactInfo:
    phone: str = ""
    phone_display: str = ""
    email: str = ""
    address: str = ""
    booking_methods: list[str] = field(default_factory=list)
    source: Provenance | None = None


@dataclass
class PageInfo:
    file: str
    title: str
    description: str
    in_briefing: bool


@dataclass
class PageContent:
    """Verbatim body-content voor één pagina, geextraheerd uit text.txt.

    Vervangt de generieke 'over deze pagina'-copy in render door de echte
    woorden van het bedrijf zelf.
    """
    slug: str
    lead: str            # eerste body-paragraaf
    body: str            # volgende 1-2 paragrafen, samengevoegd
    bullets: list[str]   # eventuele bullet-points (zoals "- onze waarden")
    paragraph_count: int # hoeveel kandidaat-paragrafen er waren
    source: Provenance


@dataclass
class ImageInfo:
    path: str            # relative to collected/
    role_hint: str       # "logo", "hero", "portrait", "gallery", "service", "unknown"


@dataclass
class ContentInventory:
    slug: str
    company_name: str
    contact: ContactInfo
    prices: list[PriceItem]
    treatments: list[Treatment]
    brands: list[BrandMention]
    opening_hours: list[HoursRow]
    reviews: list[Review]
    pages: list[PageInfo]
    images: list[ImageInfo]
    sources_inspected: list[str]
    warnings: list[str]
    visual_dna: VisualDNA = field(default_factory=VisualDNA)
    signatures: list[Signature] = field(default_factory=list)
    pages_content: dict[str, PageContent] = field(default_factory=dict)
    voice_profile: VoiceProfile = field(default_factory=VoiceProfile)
    typography: Typography = field(default_factory=Typography)
    source_copy: SourceCopy = field(default_factory=SourceCopy)

    def summary(self) -> dict[str, int]:
        return {
            "prices":         len(self.prices),
            "treatments":     len(self.treatments),
            "brands":         len(self.brands),
            "opening_hours":  len(self.opening_hours),
            "reviews":        len(self.reviews),
            "pages":          len(self.pages),
            "images":         len(self.images),
            "warnings":       len(self.warnings),
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return default


def _format_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return phone
    if digits.startswith("31") and len(digits) == 11:
        return f"+31 {digits[2]} {digits[3:7]} {digits[7:]}"
    if digits.startswith("0") and len(digits) == 10:
        return f"{digits[:2]} {digits[2:6]} {digits[6:]}"
    return phone


# ── Prices + treatments uit text.txt ─────────────────────────────────────────
# Matcht "<label> <amount>" op één regel:
#   "Mini facial treatment (30 min)   €36,50"
#   "Knippen, föhnen halflang haar:     € 37,50"
#   "Gellak French                      € 30,-"
_PRICE_LINE_RE = re.compile(
    r"""^(?P<label>.+?)\s+
        (?:vanaf\s+)?
        (?P<amount>€\s?\d{1,4}(?:[,.]\d{2}|,-)?)\s*
        (?:\s*\(vanaf\))?\s*$
    """,
    re.VERBOSE,
)

# Matcht een standalone price-regel (label staat op de regel ervoor):
#   "€19,50"
#   "€ 25,- (vanaf)"
#   "vanaf € 55,-"
_PRICE_ONLY_RE = re.compile(
    r"""^\s*(?:vanaf\s+)?
        (?P<amount>€\s?\d{1,4}(?:[,.]\d{2}|,-)?)
        \s*(?:\(vanaf\))?\s*$
    """,
    re.VERBOSE,
)

_DURATION_RE = re.compile(r"\((?P<min>\d{1,3})\s*min(?:uten)?\)|(?P<min2>\d{1,3})\s*min\b", re.IGNORECASE)
# Categorieheaders die in Carlijn's text.txt voorkomen — herkenbaar aan korte regel
# zonder € en zonder zin-leestekens.
_CATEGORY_HEADER_RE = re.compile(
    r"^("
    r"Gezichtsverzorging|Gezichtsbehandelingen|Behandelingen|"
    r"Harsen\s*&?\s*Verven|Massages?|"
    r"Tarieven\s+tijdens\s+een\s+gezichtsbehandeling|"
    r"Wimpers?|Wimperextensions?|Wenkbrauwen|"
    # Kapsalon
    r"Knippen|Kleuren|Permanenten|Mode(?:l)?f(?:o|ö)hnen|Stylen|Föhnen|"
    r"Hoogtepunten|Highlights|Balayage|"
    # Wellness/spa
    r"Lash\s+lift|Spa|Wellness|Sauna|Voet|"
    # Beauty sub-niches
    r"Manicure|Pedicure|Nagels|Acrylnagels|"
    r"Permanente\s+(?:make-?up|makeup)|Eyeliner|Lippen|"
    r"Waxen(?:\s+voor\s+\w+)?|Ontharen|IPL|Microdermabrasie"
    r")$",
    re.IGNORECASE,
)


def _extract_prices(text: str, source_name: str) -> tuple[list[PriceItem], list[Treatment]]:
    """Run twee passes: vind prijslijnen en de bijbehorende categorie-header
    daarboven. Voeg de label-string toe als treatment-naam.

    Ondersteunt drie regel-formats:
      "<label> <amount>"               (Carlijn-stijl: één regel)
      "<label>" gevolgd door "<amount>" (Frank/Mai-Kim-stijl: twee regels)
      "<amount>"  (standalone, label staat erboven via pending-buffer)
    """
    prices: list[PriceItem] = []
    treatments: list[Treatment] = []
    seen_treatments: set[str] = set()
    current_category = ""
    items_in_current = 0
    # Buffer voor de laatste 'mogelijke label'-regel. Wordt gebruikt wanneer
    # de volgende regel een standalone prijs blijkt te zijn.
    pending_label: str | None = None

    def _record(label: str, amount: str, lineno: int) -> None:
        nonlocal items_in_current
        amount_clean = amount.replace("€ ", "€").replace(" ", "").strip()
        # ',-' = 'geen cents'; normaliseer naar '00' zodat pricelist consistent
        # rendert ('€25,-' → '€25,00'? Nee, behoud de oorspronkelijke notatie
        # voor brand-fidelity: als de bron ',-' gebruikt, doen wij dat ook.)
        duration: int | None = None
        dur_match = _DURATION_RE.search(label)
        if dur_match:
            duration = int(dur_match.group("min") or dur_match.group("min2"))
        label_clean = re.sub(r"^vanaf\s+\d+\s+\w+\s+", "", label, flags=re.IGNORECASE).strip()
        label_clean = re.sub(r"\s*\bvanaf\s*$", "", label_clean, flags=re.IGNORECASE).strip()
        label_clean = label_clean.rstrip(":").strip()
        if not label_clean or len(label_clean) < 2:
            return
        provenance = Provenance(source=f"{source_name}:{lineno}", confidence="high")
        prices.append(PriceItem(
            label=label_clean,
            amount=amount_clean,
            duration_min=duration,
            category=current_category or "Overig",
            source=provenance,
        ))
        items_in_current += 1
        norm = re.sub(r"\(.*?\)", "", label_clean).strip().lower()
        norm = re.sub(r"\s+\d+\s*min.*$", "", norm).strip()
        if norm and norm not in seen_treatments:
            seen_treatments.add(norm)
            treatments.append(Treatment(name=label_clean, source=provenance))

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            pending_label = None
            continue
        # Pagina-marker / footer: reset alles
        if line.startswith("===") or line.startswith("(+31)") or line.startswith("©"):
            current_category = ""
            items_in_current = 0
            pending_label = None
            continue
        # Context-break "Let op:"
        if line.lower().startswith("let op"):
            current_category = "Losse behandelingen"
            items_in_current = 0
            pending_label = None
            continue
        # Categorie-header
        cat_match = _CATEGORY_HEADER_RE.match(line)
        if cat_match:
            cat = line.rstrip(":")
            # Mai-Kim heeft zowel "PEDICURE" als "Pedicure" als header op
            # verschillende plaatsen — collapseren door alles in title-case te
            # zetten als de bron all-caps gebruikt.
            if cat.isupper():
                cat = cat.title()
            current_category = cat
            items_in_current = 0
            pending_label = None
            continue
        # Standalone prijs op eigen regel: gebruik pending-label van vorige regel
        only_match = _PRICE_ONLY_RE.match(line)
        if only_match:
            if pending_label and 2 <= len(pending_label) <= 90:
                _record(pending_label, only_match.group("amount"), lineno)
            pending_label = None
            continue
        # Label+prijs op één regel
        match = _PRICE_LINE_RE.match(line)
        if not match:
            # Geen match — bewaar als mogelijke pending-label voor de volgende
            # standalone-prijs regel. Beperk lengte zodat lange paragrafen
            # geen prijs-label worden.
            if 3 <= len(line) <= 90 and not line.startswith(("- ", "* ")):
                pending_label = line
            else:
                pending_label = None
            continue
        label = match.group("label").strip().rstrip(",.").strip()
        _record(label, match.group("amount"), lineno)
        pending_label = None
    return prices, treatments


# ── Brand mentions ───────────────────────────────────────────────────────────
# Curated lijst van merknamen die we expliciet gezien hebben in beauty/wellness
# data. Uitbreidbaar zonder runtime LLM. Hoofdletter-gevoelig om false positives
# te beperken.
_BRAND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Botanical Beauty",  re.compile(r"\bBotanical\s+Beauty\b")),
    ("Elleebana",         re.compile(r"\bElleebana\b", re.IGNORECASE)),
    ("Hydropeptide",      re.compile(r"\bHydro[- ]?peptide\b", re.IGNORECASE)),
    ("Bio-expert",        re.compile(r"\bBio[- ]?expert\b", re.IGNORECASE)),
    ("BB treatment",      re.compile(r"\bBB\s+treatment\b")),
    ("Skins",             re.compile(r"\bSkins\b")),
    ("Rituals",           re.compile(r"\bRituals\b")),
]


def _extract_brands(text: str, source_name: str) -> list[BrandMention]:
    brands: list[BrandMention] = []
    for name, pattern in _BRAND_PATTERNS:
        matches = pattern.findall(text)
        if not matches:
            continue
        brands.append(BrandMention(
            name=name,
            occurrences=len(matches),
            source=Provenance(source=source_name, confidence="high"),
        ))
    return brands


# ── Opening hours ────────────────────────────────────────────────────────────
_DAY_RE = (
    r"(?:ma|di|wo|do|vr|za|zo|maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag)"
)
_HOURS_LINE_RE = re.compile(
    rf"\b({_DAY_RE})[a-z]*\b[\s:.\-]*"
    r"(\d{1,2}[:.]\d{2})\s*(?:-|tot|t/m|/)\s*(\d{1,2}[:.]\d{2})",
    re.IGNORECASE,
)


def _extract_hours(text: str, source_name: str) -> list[HoursRow]:
    rows: list[HoursRow] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        for match in _HOURS_LINE_RE.finditer(raw):
            rows.append(HoursRow(
                day=match.group(1).capitalize(),
                range=f"{match.group(2)} - {match.group(3)}".replace(".", ":"),
                source=Provenance(source=f"{source_name}:{lineno}", confidence="high"),
            ))
    return rows


# ── Signature-zinnen ─────────────────────────────────────────────────────────
# Verbatim zinnen die de stem van het bedrijf vangen. Niet om te paraphraseren;
# bedoeld om hero/about/CTA te vullen met taal die de prospect zelf herkent.
_SIG_FIRST_PERSON_RE = re.compile(r"^(?:Ik|Mijn|Wij|Onze)\b")
_SIG_INVITATION_RE   = re.compile(r"^(?:Ben je|Wil je|Kom je|Bent u|Wilt u|Welkom)\b")
_SIG_CTA_RE          = re.compile(r"^(?:Boek|Plan|Reserveer|Maak|Bekijk|Ontdek|Kies)\b")
_SIG_PROMISE_RE      = re.compile(
    r"\b(?:sta voor|geloof|missie|passie|specialiseer|trots|"
    r"gepassioneerd|gespecialiseerd|verwen|in de watten|persoonlijke aandacht)",
    re.IGNORECASE,
)
# Identiteits-/missie-openers verdienen extra gewicht: dit zijn de zinnen die
# klanten zelf als hero/about willen zien ("Ik ben X", "Mijn missie is Y").
_SIG_IDENTITY_RE = re.compile(
    r"^(?:Ik ben|Mijn naam|Wij zijn|Onze missie|Onze visie|"
    r"Mijn doel|Mijn missie|Mijn avontuur|Mijn passie|Ik geloof)\b"
)
# Conditionele/modale zinnen ("Ik wil graag X", "Ik kan Y") horen bij FAQ's en
# hypothesen, niet bij hero-content. Verlaag hun score zodat echte signature-
# zinnen ze passeren.
_SIG_MODAL_RE = re.compile(
    r"^(?:Ik wil graag|Ik wil|Ik kan|Ik weet|Ik traan|Ik tril|Wil je weten)\b"
)

_SIG_REJECT_PATTERNS = [
    re.compile(r"@\S+\.\w+"),                         # email
    re.compile(r"https?://"),                          # url
    re.compile(r"\bwww\."),
    re.compile(r"\b\d{4}\s?[A-Z]{2}\b"),               # postcode (adres)
    re.compile(r"^\s*=+\s*Pagina"),
    re.compile(r"\bcookie", re.IGNORECASE),
    re.compile(r"\bprivacyverklaring", re.IGNORECASE),
    re.compile(r"\balgemene voorwaarden", re.IGNORECASE),
    re.compile(r"\bdisclaimer", re.IGNORECASE),
    re.compile(r"©"),                                  # footer copyright
    re.compile(r"\bpowered by\b", re.IGNORECASE),      # footer "Powered by JouwWeb"
    re.compile(r"\bgegevens te verzamelen\b", re.IGNORECASE),  # privacy boilerplate
    # Nav-fragmenten en gescraped page-text die soms als 'Onze ...'-zin
    # door het filter glipt.
    re.compile(r"\b(toggle|navigation|navigate)\b", re.IGNORECASE),
    re.compile(r"\b0\d{1,2}\s*[-\s]\s*\d{2,3}\s*[-\s]?\s*\d{2,3}"),  # phone met spaties/streepjes
    re.compile(r"\b\+31\s*\d"),                         # +31 telefoon
]

# Pagina-namen die geen signature-content opleveren — uitsluiten als bron.
_SIG_SKIP_PAGES = re.compile(
    r"(?:privacy|voorwaarden|disclaimer|cookie|sitemap|404|nieuwsbrief)",
    re.IGNORECASE,
)

_PAGE_MARKER_RE = re.compile(r"^=+\s*Pagina:\s*(.*?)\s*=+$", re.MULTILINE)


def _split_pages(text: str) -> list[tuple[str, str]]:
    """Splits text.txt op `=== Pagina: <name> ===` markers. Geeft lijst van
    (page_name, content) terug. Pagina's zonder marker krijgen ''-naam."""
    parts = _PAGE_MARKER_RE.split(text)
    if len(parts) < 3:
        return [("", text)]
    pages: list[tuple[str, str]] = []
    # parts: [pre-text, name1, content1, name2, content2, ...]
    if parts[0].strip():
        pages.append(("", parts[0]))
    for i in range(1, len(parts), 2):
        name = parts[i].strip()
        content = parts[i + 1] if i + 1 < len(parts) else ""
        pages.append((name, content))
    return pages


def _split_sentences(text: str) -> list[str]:
    """Naive zinsplit op .?! gevolgd door whitespace of regelend.
    Goed genoeg voor MKB-content; kommagebruik is irrelevant."""
    return re.split(r"(?<=[.!?])\s+", text)


def _extract_signatures(text: str, source_name: str) -> list[Signature]:
    """Pak top-scorende verbatim zinnen die het merk uitdragen."""
    found: list[Signature] = []
    seen: set[str] = set()
    for page_name, content in _split_pages(text):
        if page_name and _SIG_SKIP_PAGES.search(page_name):
            continue
        for raw in _split_sentences(content):
            sentence = re.sub(r"\s+", " ", raw).strip()
            if not (30 <= len(sentence) <= 300):
                continue
            if any(p.search(sentence) for p in _SIG_REJECT_PATTERNS):
                continue
            alpha = [c for c in sentence if c.isalpha()]
            if alpha and sum(c.isupper() for c in alpha) / len(alpha) > 0.6:
                continue
            score = 0
            if _SIG_FIRST_PERSON_RE.match(sentence):
                score += 3
            if _SIG_INVITATION_RE.match(sentence):
                score += 2
            if _SIG_CTA_RE.match(sentence):
                score += 1
            if _SIG_PROMISE_RE.search(sentence):
                score += 2
            if _SIG_IDENTITY_RE.match(sentence):
                score += 4
            if _SIG_MODAL_RE.match(sentence):
                score -= 3
            if score <= 0:
                continue
            normalized = sentence.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            found.append(Signature(
                quote=sentence,
                score=score,
                page=page_name,
                source=Provenance(source=source_name, confidence="high"),
            ))
    found.sort(key=lambda s: (s.score, len(s.quote)), reverse=True)
    return found[:8]


# ── Treatments fallback uit pagina-titels ────────────────────────────────────
# Veel beauty/wellness sites tonen geen prijslijst (massage-only, nail-only,
# enz.) maar hebben wel pagina-structuur als /ontspanningsmassage, /pedicure.
# Die titels ZIJN behandelingen — gebruiken als fallback wanneer de
# prijs-extractie 0 treatments oplevert.
_TREATMENT_PAGE_SKIP_RE = re.compile(
    r"^(home|over\s*(ons|mij)|contact|tarieven|prijzen|"
    r"privacy|voorwaarden|algemene|disclaimer|cookie|cadeaubon|"
    r"nieuwsbrief|online\s+reserveren|reserveren|booking|legal|"
    r"sitemap|404|blog|nieuws|portfolio|werk|projecten|producten|"
    r"team|vacature|faq|veelgestelde|inloggen|login)\b",
    re.IGNORECASE,
)


def _treatments_from_pages(pages_json: dict, source_name: str = "pages.json") -> list[Treatment]:
    """Fallback wanneer prijzen leeg zijn: leid treatments af uit pagina-
    titels die op behandelingsnamen lijken. Filter generic nav-pagina's."""
    out: list[Treatment] = []
    seen: set[str] = set()
    for entry in (pages_json.get("pages") or []):
        title = (entry.get("title") or "").strip()
        if not title or len(title) > 60:
            continue
        if _TREATMENT_PAGE_SKIP_RE.match(title):
            continue
        norm = title.lower()
        if norm in seen:
            continue
        seen.add(norm)
        out.append(Treatment(
            name=title,
            source=Provenance(
                source=f"{source_name}:{entry.get('file','?')}",
                confidence="medium",
            ),
        ))
    return out[:8]


# ── Per-pagina content extractie ─────────────────────────────────────────────
def _normalize_page_slug(name: str) -> str:
    """Normaliseer pagina-namen tot vergelijkbare slugs.

    text.txt's `=== Pagina: /over-mij/ ===` en pages.json's 'over-mij.html'
    moeten op dezelfde key matchen.
    """
    s = name.strip().lower()
    s = s.lstrip("/").rstrip("/")
    s = re.sub(r"\.html?$", "", s)
    return s


def _extract_pages_content(text: str, source_name: str) -> dict[str, PageContent]:
    """Voor elke pagina (uit text.txt page markers), extract de eerste
    body-paragrafen en bullet-points. Filtert nav-ruis en privacy-pagina's
    weg zodat de render alleen 'echte' content krijgt."""
    out: dict[str, PageContent] = {}
    for raw_name, content in _split_pages(text):
        slug = _normalize_page_slug(raw_name)
        if _SIG_SKIP_PAGES.search(slug):
            continue
        if not slug:
            slug = ""  # home

        # text.txt heeft typisch één paragraaf per regel (geen blank lines
        # ertussen). Single-line split is dus de juiste granulariteit.
        paragraphs: list[str] = []
        for para in content.splitlines():
            collapsed = re.sub(r"\s+", " ", para).strip()
            if not collapsed:
                continue
            # Skip te korte regels (meestal nav, breadcrumb, headers, of korte
            # instructie-zinnen die geen volle paragraaf zijn).
            if len(collapsed) < 80:
                continue
            # Skip footer/legal-noise + emails/URLs/postcodes
            if any(p.search(collapsed) for p in _SIG_REJECT_PATTERNS):
                continue
            # Skip prijsregels — die horen in inventory.prices, niet in body
            if re.search(r"€\s?\d", collapsed):
                continue
            # Skip pure all-caps zinnen (vaak nav-headers)
            alpha = [c for c in collapsed if c.isalpha()]
            if alpha and sum(c.isupper() for c in alpha) / len(alpha) > 0.5:
                continue
            paragraphs.append(collapsed)

        if not paragraphs:
            continue

        lead = paragraphs[0][:320]
        body_pieces = paragraphs[1:3]
        body = " ".join(body_pieces)[:500]

        # Bullets: zoek "- ", "* ", "• " prefixed regels in raw content
        bullets: list[str] = []
        for line in content.splitlines():
            line = line.strip()
            if not line.startswith(("- ", "* ", "• ", "·")):
                continue
            clean = line.lstrip("-*•· ").strip()
            if 8 <= len(clean) <= 90:
                bullets.append(clean)
            if len(bullets) >= 5:
                break

        out[slug] = PageContent(
            slug=slug,
            lead=lead,
            body=body,
            bullets=bullets[:4],
            paragraph_count=len(paragraphs),
            source=Provenance(source=f"{source_name}:page={slug or 'home'}", confidence="high"),
        )
    return out


# ── Reviews / testimonials ───────────────────────────────────────────────────
_BLOCKQUOTE_RE = re.compile(r"<blockquote[^>]*>(.*?)</blockquote>", re.DOTALL | re.IGNORECASE)
_REVIEW_CLASS_RE = re.compile(
    r"<[^>]+class=\"[^\"]*(?:review|testimonial|quote)[^\"]*\"[^>]*>(.*?)</",
    re.DOTALL | re.IGNORECASE,
)
_PLACEHOLDER_TOKENS = ("testimonials_photo", "lorem ipsum", "placeholder")


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_reviews(html: str, source_name: str) -> tuple[list[Review], list[str]]:
    """Echte reviews: tekst tussen <blockquote> of class="review/testimonial".
    Filter de WordPress 'testimonials_photo' placeholder weg en geef in plaats
    daarvan een warning."""
    reviews: list[Review] = []
    warnings: list[str] = []
    placeholder_only = False
    for match in _BLOCKQUOTE_RE.finditer(html):
        clean = _strip_html(match.group(1))
        if not clean or len(clean) < 30:
            continue
        if any(token in clean.lower() for token in _PLACEHOLDER_TOKENS):
            placeholder_only = True
            continue
        reviews.append(Review(
            quote=clean,
            attribution="",
            source=Provenance(source=source_name, confidence="medium"),
        ))
    for match in _REVIEW_CLASS_RE.finditer(html):
        clean = _strip_html(match.group(1))
        if not clean or len(clean) < 30:
            continue
        if any(token in clean.lower() for token in _PLACEHOLDER_TOKENS):
            placeholder_only = True
            continue
        reviews.append(Review(
            quote=clean,
            attribution="",
            source=Provenance(source=source_name, confidence="medium"),
        ))
    if placeholder_only and not reviews:
        warnings.append(
            "Reviews-widget aanwezig in raw.html maar alleen placeholder tekst — "
            "geen echte klantcitaten gevonden"
        )
    if not reviews and not placeholder_only:
        warnings.append("Geen reviews of testimonials gevonden in raw.html")
    return reviews, warnings


# ── Contact + booking methods ────────────────────────────────────────────────
_BOOKING_PHRASES = {
    "online_reserveren": [r"online\s+reserveren", r"online\s+afspraak", r"book\s+online"],
    "whatsapp":          [r"whats?app", r"wa\.me/"],
    "phone":             [r"bel\s+(?:ons|direct|mij)", r"telefonisch"],
    "email":             [r"per\s+mail", r"via\s+(?:de\s+)?mail", r"\bmailto:"],
    "instagram_dm":      [r"instagram", r"\bDM\b"],
}


def _extract_contact(structured: dict, briefing: str, text: str, raw_html: str = "") -> ContactInfo:
    raw = structured.get("contact") if isinstance(structured.get("contact"), dict) else {}
    phone = (raw.get("phone") or "").strip()
    email = (raw.get("email") or "").strip()
    raw_address = (raw.get("address") or "").strip()

    # Fallback-volgorde: text.txt → raw.html (tel:/mailto: hrefs).
    # Veel sites tonen het nummer in de footer, maar wij kunnen het
    # missen omdat text.txt-conversie het soms verminkt of overslaat.
    if not phone:
        phone_match = re.search(
            r"\b(?:\+31\s?|0031\s?)?0?6[\s-]?\d{2}[\s-]?\d{2}[\s-]?\d{2}[\s-]?\d{2}\b",
            text,
        )
        if phone_match:
            phone = re.sub(r"[\s-]", "", phone_match.group(0))
            if phone.startswith("06"):
                phone = "+31" + phone[1:]
    if not phone and raw_html:
        # Parse <a href="tel:..."> hrefs — meest betrouwbare bron want
        # de site-eigenaar heeft hem zelf als klikbare link gezet.
        tel_match = re.search(r'href=["\']tel:([+\d\s\-()]+)["\']', raw_html, re.IGNORECASE)
        if tel_match:
            digits = re.sub(r"[\s\-()]", "", tel_match.group(1))
            if digits.startswith("00"):
                digits = "+" + digits[2:]
            elif digits.startswith("0") and len(digits) >= 10:
                digits = "+31" + digits[1:]
            phone = digits
        else:
            # Fallback: strip HTML-tags en zoek 'tel: 072-...'-achtige patronen
            # of een NL phone-pattern in de buurt van een 'tel'-keyword.
            text_only = re.sub(r"<[^>]+>", " ", raw_html)
            inline_match = re.search(
                r"(?:tel|telefoon|t)[\.:]?\s*(\+?[\d][\d\s\-()]{8,18})",
                text_only,
                re.IGNORECASE,
            )
            if inline_match:
                digits = re.sub(r"[\s\-()]", "", inline_match.group(1))
                # Sanity check: NL nummer heeft 10-12 cijfers, eventueel +31 prefix
                clean = digits.lstrip("+")
                if 9 <= len(clean) <= 12:
                    if digits.startswith("00"):
                        digits = "+" + digits[2:]
                    elif digits.startswith("0") and len(digits) >= 10:
                        digits = "+31" + digits[1:]
                    phone = digits
    if not email:
        email_match = re.search(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", text)
        if email_match:
            email = email_match.group(0)
    if not email and raw_html:
        mail_match = re.search(r'href=["\']mailto:([\w.+\-]+@[\w.-]+)["\']', raw_html, re.IGNORECASE)
        if mail_match:
            email = mail_match.group(1)

    address_match = re.search(
        r"([A-Z][\w'.\- ]+?\s+\d+[A-Za-z]?(?:\s?-\s?\d+)?),?\s+(\d{4}\s?[A-Z]{2})\s+([A-Z][\w'.\- ]+)",
        f"{raw_address}\n{briefing}\n{text}",
    )
    address = ""
    if address_match:
        address = f"{address_match.group(1).strip()}, {address_match.group(2).strip()} {address_match.group(3).strip()}"
    elif raw_address:
        cleaned = re.sub(r"\(\+\d+\)[^,]*", "", raw_address)
        cleaned = re.sub(r"[^@\s]+@[^\s]+", "", cleaned).strip(" ,")
        address = cleaned

    methods: list[str] = []
    haystack = f"{text}\n{briefing}".lower()
    for method, patterns in _BOOKING_PHRASES.items():
        if any(re.search(p, haystack) for p in patterns):
            methods.append(method)

    return ContactInfo(
        phone=phone,
        phone_display=_format_phone(phone) if phone else "",
        email=email,
        address=address,
        booking_methods=sorted(set(methods)),
        source=Provenance(source="structured_data.json+briefing.md", confidence="high" if phone or email else "low"),
    )


# ── Pages ────────────────────────────────────────────────────────────────────
def _extract_pages(pages_json: dict, briefing: str) -> list[PageInfo]:
    pages: list[PageInfo] = []
    for entry in (pages_json.get("pages") or []):
        file = str(entry.get("file", "")).strip()
        if not file:
            continue
        title = entry.get("title") or file
        description = entry.get("description") or ""
        pages.append(PageInfo(
            file=file,
            title=title,
            description=description,
            in_briefing=file.lower() in briefing.lower(),
        ))
    return pages


# ── Images role-hints ────────────────────────────────────────────────────────
_IMAGE_ROLE_PATTERNS = [
    ("logo",      [re.compile(r"^logo\.", re.IGNORECASE), re.compile(r"/logo[/.]", re.IGNORECASE)]),
    ("hero",      [re.compile(r"banner-1", re.IGNORECASE), re.compile(r"\bhero\b", re.IGNORECASE)]),
    ("portrait",  [re.compile(r"weening|carlijn|owner|portrait|profile", re.IGNORECASE)]),
    ("gallery",   [re.compile(r"image0000\d|image0-\d|gallery|salon", re.IGNORECASE)]),
    ("service",   [re.compile(r"service\d|treatment", re.IGNORECASE)]),
    ("noise",     [re.compile(r"bg_noise|layer-12|rectangle|sprite|icon", re.IGNORECASE)]),
]
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _classify_image(rel_path: str) -> str:
    for role, patterns in _IMAGE_ROLE_PATTERNS:
        if any(p.search(rel_path) for p in patterns):
            return role
    return "unknown"


def _extract_images(collected_path: Path) -> list[ImageInfo]:
    out: list[ImageInfo] = []
    # logo.* in root
    for candidate in collected_path.glob("logo.*"):
        if candidate.suffix.lower() in _IMAGE_SUFFIXES:
            out.append(ImageInfo(path=candidate.name, role_hint="logo"))
    assets_dir = collected_path / "assets"
    if not assets_dir.exists():
        return out
    for path in sorted(assets_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        rel = str(path.relative_to(collected_path)).replace("\\", "/")
        out.append(ImageInfo(path=rel, role_hint=_classify_image(rel)))
    return out


# ── Public API ───────────────────────────────────────────────────────────────
def build_inventory(slug: str, collected_path: Path) -> ContentInventory:
    """Bouw een ContentInventory uit v1 collected data."""
    text = _read_text(collected_path / "text.txt")
    raw_html = _read_text(collected_path / "raw.html")
    briefing = _read_text(collected_path / "briefing.md")
    pages_json = _read_json(collected_path / "pages.json", {})
    structured = _read_json(collected_path / "structured_data.json", {})
    meta = _read_json(collected_path / "meta.json", {})

    sources_inspected = [
        name for name, content in [
            ("text.txt", text),
            ("raw.html", raw_html),
            ("briefing.md", briefing),
            ("pages.json", pages_json),
            ("structured_data.json", structured),
            ("meta.json", meta),
        ] if content
    ]

    company_name = meta.get("company_name") or structured.get("title") or meta.get("title") or slug.replace("-", " ").title()
    company_name = re.sub(r"\s+[Vv]\d+$", "", company_name).strip()

    prices, treatments_from_prices = _extract_prices(text, "text.txt")
    brands = _extract_brands(text + "\n" + briefing, "text.txt+briefing.md")
    hours = _extract_hours(text, "text.txt")
    reviews, review_warnings = _extract_reviews(raw_html, "raw.html")
    contact = _extract_contact(structured, briefing, text, raw_html)
    pages = _extract_pages(pages_json, briefing)
    images = _extract_images(collected_path)

    # Treatments: combineer prijslijst-afgeleide namen met de "Behandelingsnamen
    # exact:" regel uit de briefing (als die er is).
    treatments: list[Treatment] = list(treatments_from_prices)
    seen = {t.name.lower() for t in treatments}
    exact_match = re.search(r"Behandelingsnamen\s+exact:\s*(.+)", briefing, re.IGNORECASE)
    if exact_match:
        for name in exact_match.group(1).split(","):
            clean = name.strip().rstrip(".").strip()
            if clean and clean.lower() not in seen:
                treatments.append(Treatment(
                    name=clean,
                    source=Provenance(source="briefing.md:Behoud uit huidige site", confidence="high"),
                ))
                seen.add(clean.lower())

    # Fallback wanneer geen prijzen gevonden zijn: leid treatments af uit
    # pagina-titels (massage/nagelstudio's hebben vaak geen prijslijst online,
    # wel pagina's per behandeling). Voorkomt dat de gate faalt op
    # treatments.min_count terwijl de bron wel duidelijk een dienstenaanbod
    # heeft, alleen niet in prijsvorm.
    if len(treatments) < 3:
        for t in _treatments_from_pages(pages_json):
            if t.name.lower() in seen:
                continue
            treatments.append(t)
            seen.add(t.name.lower())

    warnings: list[str] = []
    warnings.extend(review_warnings)
    if not prices:
        warnings.append("Geen prijslijnen gevonden in text.txt — controleer of er een tarievenpagina is")
    if not hours:
        warnings.append("Geen openingstijden gevonden — fallback ('op afspraak') gebruiken in render")
    if not contact.phone and not contact.email:
        warnings.append("Geen telefoon en geen e-mail — site mist contactactie")
    if not images:
        warnings.append("Geen afbeeldingen gevonden in collected/assets")

    visual_dna = extract_visual_dna(collected_path)
    if visual_dna.method == "unavailable":
        warnings.append(
            f"Visual DNA niet beschikbaar (mode={visual_dna.seed_mode}); "
            f"render valt terug op briefing-kleuren"
        )

    signatures = _extract_signatures(text, "text.txt")
    if not signatures:
        warnings.append(
            "Geen signature-zinnen gevonden in text.txt — hero/about valt terug "
            "op briefing-extractie"
        )

    pages_content = _extract_pages_content(text, "text.txt")
    if not pages_content:
        warnings.append(
            "Geen per-pagina body-content geextraheerd — text.txt mist Pagina-markers "
            "of body-paragrafen"
        )

    voice_profile = extract_voice_profile(text)
    if voice_profile.method == "unavailable":
        warnings.append(
            "Voice profile niet bepaald (te weinig body-tekst) — render gebruikt "
            "default je-vorm copy"
        )

    typography = extract_typography(raw_html)
    if typography.method == "default":
        warnings.append(
            "Geen typografie geextraheerd uit raw.html — render gebruikt default fonts"
        )

    source_copy = extract_source_copy(raw_html, text)
    if source_copy.method != "extracted":
        warnings.append(
            f"source_copy mining gaf {source_copy.method} — render gebruikt default tagline/CTA"
        )

    return ContentInventory(
        slug=slug,
        company_name=company_name,
        contact=contact,
        prices=prices,
        treatments=treatments,
        brands=brands,
        opening_hours=hours,
        reviews=reviews,
        pages=pages,
        images=images,
        sources_inspected=sources_inspected,
        warnings=warnings,
        visual_dna=visual_dna,
        signatures=signatures,
        pages_content=pages_content,
        voice_profile=voice_profile,
        typography=typography,
        source_copy=source_copy,
    )
