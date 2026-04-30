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

from .visual_dna import VisualDNA, extract_visual_dna


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
_PRICE_LINE_RE = re.compile(
    r"""^(?P<label>.+?)\s+
        (?P<amount>€\s?\d{1,4}(?:[,.]\d{2})?)\s*$
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
    """
    prices: list[PriceItem] = []
    treatments: list[Treatment] = []
    seen_treatments: set[str] = set()
    current_category = ""
    # Aantal prijzen geboekt onder de huidige categorie. Als dit 0 blijft
    # voordat een nieuwe categorie verschijnt, was de vorige waarschijnlijk
    # een nav-breadcrumb i.p.v. een echte prijslijst-header.
    items_in_current = 0
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        # Pagina-marker uit collect.py: reset categorie. Voorkomt dat een
        # nav-categorie van pagina A blijft hangen op pagina B's content.
        if line.startswith("===") or line.startswith("(+31)") or line.startswith("©"):
            current_category = ""
            items_in_current = 0
            continue
        # Context-break: "Let op:" of "deze losse behandelingen ..." tekst
        # markeert het einde van de huidige categorie. Volgende prijzen vallen
        # onder een aparte 'Losse behandelingen' bucket tot een echte header
        # ze opnieuw groepeert.
        if line.lower().startswith("let op"):
            current_category = "Losse behandelingen"
            items_in_current = 0
            continue
        cat_match = _CATEGORY_HEADER_RE.match(line)
        if cat_match:
            current_category = line.rstrip(":")
            items_in_current = 0
            continue
        match = _PRICE_LINE_RE.match(line)
        if not match:
            continue
        label = match.group("label").strip().rstrip(",.").strip()
        amount = match.group("amount").replace(" ", "")
        duration: int | None = None
        dur_match = _DURATION_RE.search(label)
        if dur_match:
            duration = int(dur_match.group("min") or dur_match.group("min2"))
        if len(label) < 3 or label.lower().startswith(("vanaf 1 jan ", "let op")):
            label_clean = re.sub(r"^vanaf\s+\d+\s+\w+\s+", "", label, flags=re.IGNORECASE).strip()
        else:
            label_clean = label
        # "vanaf" tussen label en prijs ("krullen lang haar: vanaf € 35,50") is
        # geen onderdeel van de behandelingsnaam.
        label_clean = re.sub(r"\s*\bvanaf\s*$", "", label_clean, flags=re.IGNORECASE).strip()
        # Trailing dubbele punt is een formatteringsartefact, hoort niet in de naam.
        label_clean = label_clean.rstrip(":").strip()
        if not label_clean:
            continue
        provenance = Provenance(source=f"{source_name}:{lineno}", confidence="high")
        prices.append(PriceItem(
            label=label_clean,
            amount=amount,
            duration_min=duration,
            category=current_category or "Overig",
            source=provenance,
        ))
        items_in_current += 1
        # Treatment afgeleid uit prijslijst-label, dedupliceren op normalised name
        norm = re.sub(r"\(.*?\)", "", label_clean).strip().lower()
        norm = re.sub(r"\s+\d+\s*min.*$", "", norm).strip()
        if norm and norm not in seen_treatments:
            seen_treatments.add(norm)
            treatments.append(Treatment(name=label_clean, source=provenance))
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


def _extract_contact(structured: dict, briefing: str, text: str) -> ContactInfo:
    raw = structured.get("contact") if isinstance(structured.get("contact"), dict) else {}
    phone = (raw.get("phone") or "").strip()
    email = (raw.get("email") or "").strip()
    raw_address = (raw.get("address") or "").strip()

    # Fallback: pak telefoon/email uit text.txt als structured_data leeg/kapot
    # is. Veel WordPress-sites tonen het nummer expliciet in de footer.
    if not phone:
        phone_match = re.search(
            r"\b(?:\+31\s?|0031\s?)?0?6[\s-]?\d{2}[\s-]?\d{2}[\s-]?\d{2}[\s-]?\d{2}\b",
            text,
        )
        if phone_match:
            phone = re.sub(r"[\s-]", "", phone_match.group(0))
            # Normaliseer 06... naar +316... voor tel:-links
            if phone.startswith("06"):
                phone = "+31" + phone[1:]
    if not email:
        email_match = re.search(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", text)
        if email_match:
            email = email_match.group(0)

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
    contact = _extract_contact(structured, briefing, text)
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
    )
