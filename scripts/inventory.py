"""
inventory.py — Deterministische extractie van bron-feiten uit collected data.

Output: data/<slug>/inventory.json met gestructureerde, *verbatim* feiten:
  - prices       : exact uit text.txt (€-bedragen + label + categorie + lineno)
  - treatments   : afgeleid uit prijslijst + pages.json fallback
  - opening_hours: regex op text.txt
  - reviews      : <blockquote>/class="review" uit raw.html
  - contact      : phone/email/address met fallback structured_data → text → raw.html
  - signatures   : verbatim quotes die de stem van het bedrijf vangen
  - pages_content: per subpage de eerste body-paragrafen (geen nav-ruis)
  - voice_profile: je/u-vorm + formality
  - source_copy  : tagline (og:description) + CTA-labels + h2's + stats

Geen LLM. Alle output heeft `source` + `confidence` per veld zodat de
generator kan citeren, niet hertalen.

Gebruik:
  python3 scripts/inventory.py --slug kapsalon-frank-nl
  python3 scripts/inventory.py --all
  python3 scripts/inventory.py --slug X --print-summary
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Zorg dat sibling-modules importeerbaar zijn
sys.path.insert(0, str(Path(__file__).resolve().parent))
from source_copy import SourceCopy, extract_source_copy  # noqa: E402
from voice_profile import VoiceProfile, extract_voice_profile  # noqa: E402


# ── Types ────────────────────────────────────────────────────────────────────
@dataclass
class Provenance:
    source: str
    confidence: str  # "high", "medium", "low"


@dataclass
class PriceItem:
    label: str
    amount: str
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


@dataclass
class PageContent:
    slug: str
    lead: str
    body: str
    bullets: list[str]
    paragraph_count: int
    source: Provenance


@dataclass
class ImageInfo:
    path: str
    role_hint: str


@dataclass
class ContentInventory:
    slug: str
    company_name: str
    source_url: str
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
    signatures: list[Signature] = field(default_factory=list)
    pages_content: dict[str, PageContent] = field(default_factory=dict)
    voice_profile: VoiceProfile = field(default_factory=VoiceProfile)
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
            "signatures":     len(self.signatures),
            "pages_content":  len(self.pages_content),
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
        (?:vanaf\s+)?
        (?P<amount>€\s?\d{1,4}(?:[,.]\d{2}|,-)?)\s*
        (?:\s*\(vanaf\))?\s*$
    """,
    re.VERBOSE,
)
_PRICE_ONLY_RE = re.compile(
    r"""^\s*(?:vanaf\s+)?
        (?P<amount>€\s?\d{1,4}(?:[,.]\d{2}|,-)?)
        \s*(?:\(vanaf\))?\s*$
    """,
    re.VERBOSE,
)
_DURATION_RE = re.compile(r"\((?P<min>\d{1,3})\s*min(?:uten)?\)|(?P<min2>\d{1,3})\s*min\b", re.IGNORECASE)
_CATEGORY_HEADER_RE = re.compile(
    r"^("
    r"Gezichtsverzorging|Gezichtsbehandelingen|Behandelingen|"
    r"Harsen\s*&?\s*Verven|Massages?|"
    r"Tarieven\s+tijdens\s+een\s+gezichtsbehandeling|"
    r"Wimpers?|Wimperextensions?|Wenkbrauwen|"
    r"Knippen|Kleuren|Permanenten|Mode(?:l)?f(?:o|ö)hnen|Stylen|Föhnen|"
    r"Hoogtepunten|Highlights|Balayage|"
    r"Lash\s+lift|Spa|Wellness|Sauna|Voet|"
    r"Manicure|Pedicure|Nagels|Acrylnagels|"
    r"Permanente\s+(?:make-?up|makeup)|Eyeliner|Lippen|"
    r"Waxen(?:\s+voor\s+\w+)?|Ontharen|IPL|Microdermabrasie"
    r")$",
    re.IGNORECASE,
)


def _extract_prices(text: str, source_name: str) -> tuple[list[PriceItem], list[Treatment]]:
    prices: list[PriceItem] = []
    treatments: list[Treatment] = []
    seen_treatments: set[str] = set()
    current_category = ""
    items_in_current = 0
    pending_label: str | None = None

    def _record(label: str, amount: str, lineno: int) -> None:
        nonlocal items_in_current
        amount_clean = amount.replace("€ ", "€").replace(" ", "").strip()
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
        if line.startswith("===") or line.startswith("(+31)") or line.startswith("©"):
            current_category = ""
            items_in_current = 0
            pending_label = None
            continue
        if line.lower().startswith("let op"):
            current_category = "Losse behandelingen"
            items_in_current = 0
            pending_label = None
            continue
        cat_match = _CATEGORY_HEADER_RE.match(line)
        if cat_match:
            cat = line.rstrip(":")
            if cat.isupper():
                cat = cat.title()
            current_category = cat
            items_in_current = 0
            pending_label = None
            continue
        only_match = _PRICE_ONLY_RE.match(line)
        if only_match:
            if pending_label and 2 <= len(pending_label) <= 90:
                _record(pending_label, only_match.group("amount"), lineno)
            pending_label = None
            continue
        match = _PRICE_LINE_RE.match(line)
        if not match:
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
_BRAND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Botanical Beauty",  re.compile(r"\bBotanical\s+Beauty\b")),
    ("Selective Professional", re.compile(r"\bSelective\s+Professional\b", re.IGNORECASE)),
    ("Powerplex",         re.compile(r"\bPowerplex\b", re.IGNORECASE)),
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
_SIG_FIRST_PERSON_RE = re.compile(r"^(?:Ik|Mijn|Wij|Onze)\b")
_SIG_INVITATION_RE   = re.compile(r"^(?:Ben je|Wil je|Kom je|Bent u|Wilt u|Welkom)\b")
_SIG_CTA_RE          = re.compile(r"^(?:Boek|Plan|Reserveer|Maak|Bekijk|Ontdek|Kies)\b")
_SIG_PROMISE_RE      = re.compile(
    r"\b(?:sta voor|geloof|missie|passie|specialiseer|trots|"
    r"gepassioneerd|gespecialiseerd|verwen|in de watten|persoonlijke aandacht)",
    re.IGNORECASE,
)
_SIG_IDENTITY_RE = re.compile(
    r"^(?:Ik ben|Mijn naam|Wij zijn|Onze missie|Onze visie|"
    r"Mijn doel|Mijn missie|Mijn avontuur|Mijn passie|Ik geloof)\b"
)
_SIG_MODAL_RE = re.compile(
    r"^(?:Ik wil graag|Ik wil|Ik kan|Ik weet|Ik traan|Ik tril|Wil je weten)\b"
)
_SIG_REJECT_PATTERNS = [
    re.compile(r"@\S+\.\w+"),
    re.compile(r"https?://"),
    re.compile(r"\bwww\."),
    re.compile(r"\b\d{4}\s?[A-Z]{2}\b"),
    re.compile(r"^\s*=+\s*Pagina"),
    re.compile(r"\bcookie", re.IGNORECASE),
    re.compile(r"\bprivacyverklaring", re.IGNORECASE),
    re.compile(r"\balgemene voorwaarden", re.IGNORECASE),
    re.compile(r"\bdisclaimer", re.IGNORECASE),
    re.compile(r"©"),
    re.compile(r"\bpowered by\b", re.IGNORECASE),
    re.compile(r"\bgegevens te verzamelen\b", re.IGNORECASE),
    re.compile(r"\b(toggle|navigation|navigate)\b", re.IGNORECASE),
    re.compile(r"\b0\d{1,2}\s*[-\s]\s*\d{2,3}\s*[-\s]?\s*\d{2,3}"),
    re.compile(r"\b\+31\s*\d"),
]
_SIG_SKIP_PAGES = re.compile(
    r"(?:privacy|voorwaarden|disclaimer|cookie|sitemap|404|nieuwsbrief)",
    re.IGNORECASE,
)
_PAGE_MARKER_RE = re.compile(r"^=+\s*Pagina:\s*(.*?)\s*=+$", re.MULTILINE)


def _split_pages(text: str) -> list[tuple[str, str]]:
    parts = _PAGE_MARKER_RE.split(text)
    if len(parts) < 3:
        return [("", text)]
    pages: list[tuple[str, str]] = []
    if parts[0].strip():
        pages.append(("", parts[0]))
    for i in range(1, len(parts), 2):
        name = parts[i].strip()
        content = parts[i + 1] if i + 1 < len(parts) else ""
        pages.append((name, content))
    return pages


def _split_sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", text)


def _extract_signatures(text: str, source_name: str) -> list[Signature]:
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
_TREATMENT_PAGE_SKIP_RE = re.compile(
    r"^(home|over\s*(ons|mij)|contact|tarieven|prijzen|"
    r"privacy|voorwaarden|algemene|disclaimer|cookie|cadeaubon|"
    r"nieuwsbrief|online\s+reserveren|reserveren|booking|legal|"
    r"sitemap|404|blog|nieuws|portfolio|werk|projecten|producten|"
    r"team|vacature|faq|veelgestelde|inloggen|login)\b",
    re.IGNORECASE,
)


def _treatments_from_pages(pages_json: dict, source_name: str = "pages.json") -> list[Treatment]:
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
    s = name.strip().lower()
    s = s.lstrip("/").rstrip("/")
    s = re.sub(r"\.html?$", "", s)
    return s


def _extract_pages_content(text: str, source_name: str) -> dict[str, PageContent]:
    out: dict[str, PageContent] = {}
    for raw_name, content in _split_pages(text):
        slug = _normalize_page_slug(raw_name)
        if _SIG_SKIP_PAGES.search(slug):
            continue
        if not slug:
            slug = ""

        paragraphs: list[str] = []
        for para in content.splitlines():
            collapsed = re.sub(r"\s+", " ", para).strip()
            if not collapsed:
                continue
            if len(collapsed) < 80:
                continue
            if any(p.search(collapsed) for p in _SIG_REJECT_PATTERNS):
                continue
            if re.search(r"€\s?\d", collapsed):
                continue
            alpha = [c for c in collapsed if c.isalpha()]
            if alpha and sum(c.isupper() for c in alpha) / len(alpha) > 0.5:
                continue
            paragraphs.append(collapsed)

        if not paragraphs:
            continue

        lead = paragraphs[0][:320]
        body_pieces = paragraphs[1:3]
        body = " ".join(body_pieces)[:500]

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
    reviews: list[Review] = []
    warnings: list[str] = []
    placeholder_only = False
    seen: set[str] = set()
    for match in _BLOCKQUOTE_RE.finditer(html):
        clean = _strip_html(match.group(1))
        if not clean or len(clean) < 30:
            continue
        if any(token in clean.lower() for token in _PLACEHOLDER_TOKENS):
            placeholder_only = True
            continue
        if clean.lower() in seen:
            continue
        seen.add(clean.lower())
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
        if clean.lower() in seen:
            continue
        seen.add(clean.lower())
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
        tel_match = re.search(r'href=["\']tel:([+\d\s\-()]+)["\']', raw_html, re.IGNORECASE)
        if tel_match:
            digits = re.sub(r"[\s\-()]", "", tel_match.group(1))
            if digits.startswith("00"):
                digits = "+" + digits[2:]
            elif digits.startswith("0") and len(digits) >= 10:
                digits = "+31" + digits[1:]
            phone = digits
        else:
            text_only = re.sub(r"<[^>]+>", " ", raw_html)
            inline_match = re.search(
                r"(?:tel|telefoon|t)[\.:]?\s*(\+?[\d][\d\s\-()]{8,18})",
                text_only,
                re.IGNORECASE,
            )
            if inline_match:
                digits = re.sub(r"[\s\-()]", "", inline_match.group(1))
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
        source=Provenance(source="structured_data.json+text.txt+raw.html", confidence="high" if phone or email else "low"),
    )


# ── Pages ────────────────────────────────────────────────────────────────────
def _extract_pages(pages_json: dict) -> list[PageInfo]:
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
        ))
    return pages


# ── Images role-hints ────────────────────────────────────────────────────────
_IMAGE_ROLE_PATTERNS = [
    ("logo",      [re.compile(r"^logo\.", re.IGNORECASE), re.compile(r"/logo[/.]", re.IGNORECASE)]),
    ("hero",      [re.compile(r"banner-1", re.IGNORECASE), re.compile(r"\bhero\b", re.IGNORECASE)]),
    ("portrait",  [re.compile(r"weening|owner|portrait|profile", re.IGNORECASE)]),
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

    company_name = (
        meta.get("company_name")
        or structured.get("title")
        or meta.get("title")
        or slug.replace("-", " ").title()
    )
    company_name = re.sub(r"\s+[Vv]\d+$", "", company_name).strip()
    source_url = meta.get("url") or ""

    prices, treatments_from_prices = _extract_prices(text, "text.txt")
    brands = _extract_brands(text + "\n" + briefing, "text.txt+briefing.md")
    hours = _extract_hours(text, "text.txt")
    reviews, review_warnings = _extract_reviews(raw_html, "raw.html")
    contact = _extract_contact(structured, briefing, text, raw_html)
    pages = _extract_pages(pages_json)
    images = _extract_images(collected_path)

    treatments: list[Treatment] = list(treatments_from_prices)
    seen = {t.name.lower() for t in treatments}
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

    signatures = _extract_signatures(text, "text.txt")
    if not signatures:
        warnings.append("Geen signature-zinnen gevonden in text.txt")

    pages_content = _extract_pages_content(text, "text.txt")
    if not pages_content:
        warnings.append("Geen per-pagina body-content geextraheerd")

    voice_profile = extract_voice_profile(text)
    if voice_profile.method == "unavailable":
        warnings.append("Voice profile niet bepaald (te weinig body-tekst)")

    source_copy = extract_source_copy(raw_html, text)
    if source_copy.method != "extracted":
        warnings.append(f"source_copy mining gaf {source_copy.method}")

    return ContentInventory(
        slug=slug,
        company_name=company_name,
        source_url=source_url,
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
        signatures=signatures,
        pages_content=pages_content,
        voice_profile=voice_profile,
        source_copy=source_copy,
    )


def write_inventory(inv: ContentInventory, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(inv.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_inventory(path: Path) -> dict:
    """Lees inventory.json als dict (geen dataclass-roundtrip nodig voor consumers)."""
    return json.loads(path.read_text(encoding="utf-8"))


# ── CLI ──────────────────────────────────────────────────────────────────────
def _print_summary(inv: ContentInventory) -> None:
    s = inv.summary()
    print(f"\n══════ INVENTORY · {inv.slug} ══════")
    print(f"  bedrijf:    {inv.company_name}")
    print(f"  bron:       {inv.source_url}")
    print(f"  prijzen:    {s['prices']}")
    print(f"  treatments: {s['treatments']}")
    print(f"  hours:      {s['opening_hours']}")
    print(f"  reviews:    {s['reviews']}")
    print(f"  pages:      {s['pages']}")
    print(f"  images:     {s['images']}")
    print(f"  signatures: {s['signatures']}")
    print(f"  pages_content keys: {list(inv.pages_content.keys())}")
    print(f"  contact:    phone='{inv.contact.phone}' email='{inv.contact.email}' address='{inv.contact.address[:60]}'")
    print(f"  voice:      addressing={inv.voice_profile.addressing} formality={inv.voice_profile.formality}")
    print(f"  tagline:    {inv.source_copy.tagline[:120] if inv.source_copy.tagline else '(geen)'}")
    print(f"  primary_cta:{inv.source_copy.primary_cta or '(geen)'}")
    if inv.warnings:
        print(f"  warnings:")
        for w in inv.warnings:
            print(f"    · {w}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build content inventory voor een prospect.")
    parser.add_argument("--slug", help="Naam van de prospect-map onder data/")
    parser.add_argument("--all", action="store_true", help="Bouw inventory voor alle prospects")
    parser.add_argument("--data-dir", default="data", help="Pad naar data/-directory")
    parser.add_argument("--print-summary", action="store_true", help="Print samenvatting na build")
    args = parser.parse_args()

    if not args.slug and not args.all:
        parser.error("specify --slug or --all")

    project_dir = Path(__file__).resolve().parents[1]
    data_dir = project_dir / args.data_dir if not Path(args.data_dir).is_absolute() else Path(args.data_dir)

    if args.slug:
        slugs = [args.slug]
    else:
        skip = {"trash", "trash_old", "_archive"}
        slugs = sorted(
            d.name for d in data_dir.iterdir()
            if d.is_dir() and d.name not in skip and (d / "text.txt").exists()
        )

    print(f"[INFO] Bouw inventory voor {len(slugs)} prospect(s)")
    rc = 0
    for slug in slugs:
        collected = data_dir / slug
        if not collected.exists():
            print(f"[FAIL] {slug}: directory niet gevonden ({collected})")
            rc = 1
            continue
        try:
            inv = build_inventory(slug, collected)
        except Exception as exc:
            print(f"[FAIL] {slug}: {exc}")
            rc = 1
            continue
        out_path = collected / "inventory.json"
        write_inventory(inv, out_path)
        s = inv.summary()
        print(f"[OK]   {slug:<35} prijzen={s['prices']:>3} treatments={s['treatments']:>2} "
              f"hours={s['opening_hours']:>2} reviews={s['reviews']:>2} sigs={s['signatures']:>2}")
        if args.print_summary:
            _print_summary(inv)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
