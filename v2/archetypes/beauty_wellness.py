"""Beauty & wellness archetype.

Bouwt een rich site plan op uit een bestaande collected prospect en delegeert
de Astro-rendering aan render_archetype. Geen runtime LLM-calls.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from v2.pipeline.inventory import ContentInventory, build_inventory
from v2.pipeline.personality import pick_personality
from v2.pipeline.quality_gate import FieldCheck

from .base import Archetype, ArchetypeMatch


# ── Detectie keywords ────────────────────────────────────────────────────────
# Zwaar wegende termen scoren hoger; alleen presentie wordt geteld, niet frequentie.
_HEAVY_KEYWORDS = [
    "beautysalon", "schoonheidssalon", "schoonheidsspecialist", "kapsalon",
    "kapper", "wellness", "wellness center", "spa", "huidverbetering",
    "lash lift", "lash volume lift", "wimperextensions", "hair salon",
    "beauty salon", "nagelstudio", "nail salon",
]
_LIGHT_KEYWORDS = [
    "salon", "behandeling", "behandelingen", "gezichtsbehandeling",
    "manicure", "pedicure", "harsen", "wenkbrauw", "wimpers",
    "massage", "facial", "skincare", "huidverzorging", "wax",
    "make-up", "visagie", "ontharing",
]


# ── Asset patronen ───────────────────────────────────────────────────────────
# Bestanden die we als gallery-foto willen tonen (in voorkeursvolgorde) en
# patronen die we willen weren (theme-stock, logo overlays, achtergrondruis).
_GALLERY_PATTERNS_PREFER = [
    re.compile(r"image0000\d", re.IGNORECASE),
    re.compile(r"image0-\d", re.IGNORECASE),
    re.compile(r"WhatsApp-Image", re.IGNORECASE),
    re.compile(r"bb-facelifting", re.IGNORECASE),
    re.compile(r"cremes-organic", re.IGNORECASE),
    re.compile(r"organic-skincare", re.IGNORECASE),
    re.compile(r"salon", re.IGNORECASE),
    re.compile(r"treatment", re.IGNORECASE),
    re.compile(r"interior", re.IGNORECASE),
]
_GALLERY_BLOCKLIST = [
    re.compile(r"bg_noise", re.IGNORECASE),
    re.compile(r"layer-12", re.IGNORECASE),
    re.compile(r"rectangle", re.IGNORECASE),
    re.compile(r"icon", re.IGNORECASE),
    re.compile(r"sprite", re.IGNORECASE),
    re.compile(r"logo", re.IGNORECASE),
    re.compile(r"thumbnail", re.IGNORECASE),
    re.compile(r"avatar", re.IGNORECASE),
]
_PORTRAIT_PATTERNS = [
    re.compile(r"weening", re.IGNORECASE),
    re.compile(r"carlijn", re.IGNORECASE),
    re.compile(r"team-1", re.IGNORECASE),
    re.compile(r"about", re.IGNORECASE),
    re.compile(r"portrait", re.IGNORECASE),
    re.compile(r"profile", re.IGNORECASE),
    re.compile(r"owner", re.IGNORECASE),
]
_HERO_PATTERNS = [
    re.compile(r"banner-1-e\d", re.IGNORECASE),
    re.compile(r"banner-1\b", re.IGNORECASE),
    re.compile(r"image00001", re.IGNORECASE),
    re.compile(r"hero", re.IGNORECASE),
    re.compile(r"main-banner", re.IGNORECASE),
    re.compile(r"home/banner", re.IGNORECASE),
]


# ── Briefing parsing helpers ─────────────────────────────────────────────────
_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b")
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")


def _split_sections(briefing: str) -> dict[str, str]:
    """Split de briefing op `## ` headers naar een dict van sectienaam -> body."""
    sections: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    for line in briefing.splitlines():
        match = _SECTION_RE.match(line)
        if match:
            if current_name is not None:
                sections[current_name.lower()] = "\n".join(current_lines).strip()
            current_name = match.group(1).strip()
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)
    if current_name is not None:
        sections[current_name.lower()] = "\n".join(current_lines).strip()
    return sections


def _bullet_lines(section_body: str) -> list[str]:
    """Pak bullet-lijnen uit een section body (lijnen die met '- ' beginnen)."""
    return [
        line.strip().lstrip("- ").strip()
        for line in section_body.splitlines()
        if line.strip().startswith("- ")
    ]


def _clean_dashes(text: str) -> str:
    return text.replace("—", ", ").replace("–", ", ")


# ── Beautysalon-specifieke service-extractie ─────────────────────────────────
_SERVICE_DEFAULTS = [
    {
        "key": "gezichtsbehandelingen",
        "title": "Gezichtsbehandelingen",
        "description": "Behandelingen op maat met natuurlijke producten voor een verzorgde, stralende huid.",
        "match": [r"gezichtsbehandel", r"facial", r"botanical", r"hydrop"],
    },
    {
        "key": "lash-lift",
        "title": "Lash Volume Lift",
        "description": "Lift en accentueer je eigen wimpers voor een wakkere, open blik die weken meegaat.",
        "match": [r"lash lift", r"lash volume", r"wimper", r"elleebana"],
    },
    {
        "key": "massage",
        "title": "Massages",
        "description": "Wellness-massages om los te laten, spanning te verminderen en weer adem te halen.",
        "match": [r"massage", r"wellness"],
    },
    {
        "key": "harsen-verven",
        "title": "Harsen en verven",
        "description": "Ontharing en wenkbrauw- of wimperverf voor een verzorgd, afgewerkt resultaat.",
        "match": [r"harsen", r"verven", r"wenkbrauw", r"ontharing", r"wax"],
    },
    {
        "key": "manicure",
        "title": "Manicure",
        "description": "Verzorgde nagels en handen, met aandacht voor detail.",
        "match": [r"manicure", r"nagel"],
    },
    {
        "key": "pedicure",
        "title": "Pedicure",
        "description": "Voetverzorging en pedicurebehandelingen voor verzorgde voeten.",
        "match": [r"pedicure", r"voet"],
    },
]


def _extract_services(briefing: str) -> list[dict[str, Any]]:
    """Bepaal welke standaard-diensten in de briefing voorkomen."""
    haystack = briefing.lower()
    services: list[dict[str, Any]] = []
    for entry in _SERVICE_DEFAULTS:
        if any(re.search(pattern, haystack) for pattern in entry["match"]):
            services.append({
                "key": entry["key"],
                "title": entry["title"],
                "description": entry["description"],
            })
    if not services:
        services = [
            {"key": "behandelingen", "title": "Behandelingen", "description": "Persoonlijke behandelingen op maat."}
        ]
    return services


def _extract_signature(sections: dict[str, str]) -> str:
    """Pak een markante zin uit 'Behoud uit huidige site' of 'Tone of voice'."""
    body = sections.get("behoud uit huidige site", "")
    for line in body.splitlines():
        if "signature" in line.lower() or "slogan" in line.lower() or "merkbelofte" in line.lower():
            quote = re.search(r'"([^"]{10,}?)"', line)
            if quote:
                return _clean_dashes(quote.group(1))
    fallback = sections.get("tone of voice", "")
    quote = re.search(r'"([^"]{10,}?)"', fallback)
    return _clean_dashes(quote.group(1)) if quote else ""


def _extract_about_quotes(sections: dict[str, str]) -> tuple[str, str]:
    """Geef (body, signature) terug uit 'Behoud uit huidige site'."""
    body_section = sections.get("behoud uit huidige site", "")
    quotes: list[str] = []
    signature = ""
    for line in body_section.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        match = re.search(r'"([^"]+)"', line)
        if not match:
            continue
        quote = _clean_dashes(match.group(1)).strip()
        if not quote:
            continue
        if not signature and ("signature" in line.lower() or "slogan" in line.lower()):
            signature = quote
        else:
            quotes.append(quote)
    body = " ".join(quotes[:2]).strip()
    if not body:
        body = sections.get("project", "").strip().split("\n")[0]
    return body, signature


def _extract_proof(sections: dict[str, str], company_name: str) -> dict[str, Any]:
    """Geen verzonnen klantreviews. We tonen merkbeloften/signature-citaten van
    de eigenaar zelf, expliciet als zodanig gelabeld."""
    body_section = sections.get("behoud uit huidige site", "")
    items: list[dict[str, str]] = []
    for line in body_section.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        match = re.search(r'"([^"]+)"', line)
        if not match:
            continue
        quote = _clean_dashes(match.group(1)).strip()
        if len(quote) < 25 or len(quote) > 220:
            continue
        items.append({
            "quote": quote,
            "attribution": f"Beloften van {company_name}",
        })
        if len(items) == 3:
            break
    return {
        "title": "Onze beloften aan jou",
        "items": items,
        "note": "Klantreviews worden door de eigenaar aangeleverd na livegang en zijn nog niet zichtbaar.",
    }


_DUTCH_ADDRESS_RE = re.compile(
    r"([A-Z][\w'.\- ]+?\s+\d+[A-Za-z]?(?:\s?-\s?\d+)?),?\s+(\d{4}\s?[A-Z]{2})\s+([A-Z][\w'.\- ]+)"
)


def _format_phone(phone: str) -> str:
    """Maak een NL-mobiel leesbaarder: +31680052875 -> +31 6 8005 2875."""
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return phone
    if digits.startswith("31") and len(digits) == 11:
        return f"+31 {digits[2]} {digits[3:7]} {digits[7:]}"
    if digits.startswith("0") and len(digits) == 10:
        return f"{digits[:2]} {digits[2:6]} {digits[6:]}"
    return phone


def _extract_contact(structured: dict[str, Any], briefing: str) -> dict[str, str]:
    contact_raw = structured.get("contact", {}) if isinstance(structured.get("contact"), dict) else {}
    phone = contact_raw.get("phone", "").strip()
    email = contact_raw.get("email", "").strip()
    address_raw = contact_raw.get("address", "").strip()
    # Eerst: zoek expliciet naar straat-nummer + postcode + plaats in raw én briefing.
    address = ""
    for source in (address_raw, briefing):
        match = _DUTCH_ADDRESS_RE.search(source)
        if match:
            address = f"{match.group(1).strip()}, {match.group(2).strip()} {match.group(3).strip()}"
            break
    # Fallback: strip telefoon/email uit raw als we geen schone match hadden.
    if not address and address_raw:
        cleaned = re.sub(r"\(\+\d+\)[^,]*", "", address_raw)
        cleaned = re.sub(r"[^@\s]+@[^\s]+", "", cleaned).strip(" ,")
        if cleaned:
            address = cleaned
    return {
        "phone": phone,
        "phone_display": _format_phone(phone) if phone else "",
        "email": email,
        "address": address,
    }


def _pick_featured_service(services: list[dict[str, Any]], price_groups: list[dict[str, Any]]) -> dict[str, Any]:
    """Pak de duurste behandeling als 'signature'-spotlight.

    Vereist een service mét image (anders ziet de spotlight er kaal uit).
    Returns leeg dict als geen geschikte kandidaat.
    """
    if not price_groups or not services:
        return {}
    # Vind de duurste prijs-item over alle groepen
    best = None
    best_price = 0.0
    for group in price_groups:
        for item in group.get("items") or []:
            amount_str = item.get("price", "")
            try:
                value = float(re.sub(r"[^\d.,]", "", amount_str).replace(",", "."))
            except ValueError:
                continue
            if value > best_price:
                best_price = value
                best = {
                    "title": item.get("label", ""),
                    "price": amount_str,
                    "category": group.get("title", ""),
                }
    if not best or not best.get("title"):
        return {}
    # Hang er een foto aan van de eerste service-card (als die er is)
    first_with_image = next((s for s in services if s.get("image")), None)
    return {
        "title":       best["title"],
        "description": f"Onze duurste en meest uitgebreide behandeling in de categorie '{best['category']}'." if best.get("category") else "",
        "price":       best["price"],
        "image":       first_with_image.get("image") if first_with_image else "",
        "href":        "/tarieven/",
    }


def _pick_process_steps(briefing: str, sections: dict[str, str]) -> list[dict[str, str]]:
    """Heuristiek: bouw 3-4 process steps wanneer de briefing erover praat,
    anders leeg laten zodat de section niet rendert."""
    blob = (briefing + " " + sections.get("project", "")).lower()
    if not any(kw in blob for kw in ("werkwijze", "stappenplan", "process", "afspraak maken", "intake")):
        return []
    return [
        {"title": "Kennismaking",  "body": "We bespreken jouw wens en wat past bij je huid en planning."},
        {"title": "Behandeling",   "body": "Ontspannen tijd alleen voor jou, met natuurlijke producten en aandacht voor detail."},
        {"title": "Nazorg",        "body": "Adviezen voor thuis zodat het resultaat zo lang mogelijk meegaat."},
    ]


def _voice_copy(voice: Any) -> dict[str, str]:
    """Lever copy-snippets afgestemd op de gedetecteerde aanspreekvorm.

    Default = 'je' (informeel). Schakelt naar 'u' alleen wanneer de
    addressing-detectie zeker is. Tussenliggende 'mixed' gevallen blijven
    in de informele variant omdat overswitch naar u-vorm voor sites die
    eigenlijk je-vorm zijn jarrender voelt dan andersom.
    """
    is_u_form = bool(voice and getattr(voice, "addressing", "") == "u")
    if is_u_form:
        return {
            "contact_cta_title":     "Klaar voor uw bezoek?",
            "contact_cta_body":      "Plan vandaag uw behandeling, dan reserveren wij de tijd voor u.",
            "contact_cta_primary":   "Online reserveren",
            "contact_cta_secondary": "Bel direct",
            "contact_cta_tertiary":  "Stuur een WhatsApp-bericht",
            "hero_secondary":        "Bekijk de behandelingen",
            "tarieven_helper":       "Vraag bij twijfel altijd vooraf naar wat de behandeling voor u kost, wij plannen genoeg tijd in en denken graag met u mee.",
            "behandeling_note":      "Twijfel u over de juiste behandeling? Bel of WhatsApp, dan denken wij graag mee.",
        }
    return {
        "contact_cta_title":     "Klaar voor je verwenmoment?",
        "contact_cta_body":      "Plan vandaag nog je behandeling, dan reserveren we tijd alleen voor jou.",
        "contact_cta_primary":   "Online reserveren",
        "contact_cta_secondary": "Bel ons direct",
        "contact_cta_tertiary":  "App ons via WhatsApp",
        "hero_secondary":        "Bekijk behandelingen",
        "tarieven_helper":       "Hieronder vind je per categorie de actuele tarieven. Vraag bij twijfel altijd vooraf naar wat de behandeling voor jou kost, we plannen genoeg tijd in en denken graag mee.",
        "behandeling_note":      "Twijfel je welke behandeling past? Bel of WhatsApp, dan denken we even met je mee.",
    }


def _signature_type(signatures: list) -> str:
    """Categoriseer de top-signature in een type dat layout-keuzes informeert.

    Drie hoofd-types breken het cousin-effect: identity ('Ik ben X' / 'Mijn
    naam Y'), mission_strong ('Mijn missie' / 'Mijn passie'), mission_soft
    ('Mijn doel' / 'Ik geloof') en invitation ('Welkom' / 'Ben je toe aan').
    Geen match valt terug op 'generic' = default look.
    """
    if not signatures:
        return "generic"
    quote = signatures[0].quote.lower()
    if quote.startswith(("ik ben", "mijn naam", "wij zijn")):
        return "identity"
    head = quote[:60]
    if "missie" in head or "passie" in head:
        return "mission_strong"
    if "doel is" in head or "ik geloof" in head:
        return "mission_soft"
    if quote.startswith(("welkom", "ben je", "wil je")):
        return "invitation"
    return "generic"


def _pick_home_section_order(sig_type: str) -> list[str]:
    """Section-volgorde op homepage afgestemd op het type signature."""
    if sig_type == "identity":
        # Persoonlijk verhaal eerst — past bij 'Ik ben X'-zinnen.
        return ["about", "services", "gallery", "reviews", "openingHours", "contactCta"]
    if sig_type == "invitation":
        # Sfeer/galerij vlak na hero — past bij uitnodigende toon.
        return ["services", "gallery", "about", "reviews", "openingHours", "contactCta"]
    # mission_strong / mission_soft / generic
    return ["services", "about", "gallery", "reviews", "openingHours", "contactCta"]


def _pick_hero_variant(sig_type: str, has_image: bool, personality_name: str = "soft") -> str:
    """Hero-mode keuze, primair op personality, secundair op signature-type.

    luxe    → image-bg (full-bleed dramatic photo + overlay)
    sharp   → split (clean text-image grid, geen drama)
    playful → split-reverse (foto links, energieke layout)
    soft    → afhankelijk van signature-type (huidige heuristiek)
    """
    if not has_image:
        return "split"
    if personality_name == "luxe":
        return "image-bg"
    if personality_name == "sharp":
        return "split"
    if personality_name == "playful":
        return "split-reverse"
    # soft (default): val terug op signature-driven keuze
    if sig_type == "identity":
        return "split-reverse"
    if sig_type in ("mission_strong", "invitation"):
        return "image-bg"
    return "split"


def _pick_gallery_variant(sig_type: str, n_categories: int) -> str:
    """Gallery-mode: grid (4-up met tall hero), masonry (variabele aspecten)
    of strip (compact 3-up). Sites met sterke missie krijgen masonry voor
    showcase-feel; identity-sites strip voor compacter ritme."""
    if sig_type == "identity":
        return "strip"
    if sig_type == "mission_strong" or n_categories >= 6:
        return "masonry"
    if n_categories <= 2:
        return "strip"
    return "grid"


def _darken_hex(hex_color: str, amount: int = 18) -> str:
    """Maak een donkerder versie van een #RRGGBB voor hover-states.

    Gebruikt naive RGB-darkening (lineair) als fallback; volstaat voor
    button-hover semantiek. Geen HCT nodig.
    """
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return hex_color
    factor = max(0, 100 - amount) / 100.0
    return "#{:02X}{:02X}{:02X}".format(int(r * factor), int(g * factor), int(b * factor))


def _theme_from_visual_dna(inventory: ContentInventory) -> dict[str, str] | None:
    """Bouw het site-thema uit de extracted Visual DNA. Geeft None terug als
    DNA niet beschikbaar is, zodat de caller op de briefing-fallback kan
    terugvallen."""
    dna = inventory.visual_dna
    if dna.method == "unavailable" or not dna.palette:
        return None
    p = dna.palette
    primary = p.get("primary", "#b8936a")
    return {
        "primary":      primary,
        # primary_dark = ~18% donkerder dan primary voor hover-states.
        # Material You's primaryContainer is juist LICHTER (pill-achtergrond),
        # dus die past niet voor een hover-darken.
        "primary_dark": _darken_hex(primary, amount=18),
        "secondary":    p.get("onSurface", "#2c2c2c"),
        # accent = lichte tint van primary, gebruikt als full-width sectie-bg
        # (.section-tinted). primaryContainer past hier perfect: een gedempte
        # pastel-versie van het brand. tertiary is te bont voor zo'n band.
        "accent":       p.get("primaryContainer", "#f5ede3"),
        "background":   p.get("background", "#faf8f5"),
        "surface":      p.get("surface", "#ffffff"),
        "text":         p.get("onSurface", "#2c2c2c"),
        "outline":      p.get("outline", "#cccccc"),
        # Provenance voor inspectie:
        "_seed":        dna.seed_color,
        "_seed_source": dna.seed_source,
        "_seed_mode":   dna.seed_mode,
    }


def _extract_theme(briefing: str, sections: dict[str, str]) -> dict[str, str]:
    """Legacy fallback: lees hex-kleuren uit de Designrichting-sectie van de
    briefing. Blijft beschikbaar voor wanneer Visual DNA niet bruikbaar is."""
    design = sections.get("designrichting", briefing)
    colors = _HEX_RE.findall(design)
    return {
        "primary": colors[0] if len(colors) > 0 else "#b8936a",
        "primary_dark": "#8d6e4f",
        "secondary": "#2c2c2c",
        "accent": colors[1] if len(colors) > 1 else "#f5ede3",
        "background": colors[2] if len(colors) > 2 else "#faf8f5",
        "text": "#2c2c2c",
    }


def _extract_pages(collected_path: Path) -> list[dict[str, Any]]:
    import json
    pages_path = collected_path / "pages.json"
    pages: list[dict[str, Any]] = [{"slug": "", "title": "Home", "in_nav": True}]
    if pages_path.exists():
        try:
            data = json.loads(pages_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        for item in (data.get("pages") or [])[:6]:
            file_name = str(item.get("file", ""))
            slug = file_name.replace(".html", "").strip("/").lower()
            if slug in {"", "index", "home", "legal", "algemene-voorwaarden", "privacy-verklaring"}:
                continue
            pages.append({
                "slug": slug,
                "title": item.get("title") or slug.replace("-", " ").capitalize(),
                "description": item.get("description", ""),
                "in_nav": True,
            })
    if len(pages) == 1:
        for slug, title in [("behandelingen", "Behandelingen"), ("over-ons", "Over ons"), ("contact", "Contact")]:
            pages.append({"slug": slug, "title": title, "description": "", "in_nav": True})
    return pages[:6]


# Cosmetische category-aliases voor de tarievenpagina. Inventory levert ruwe
# kop-strings uit text.txt; deze map brengt ze naar mooiere kopjes voor render.
_PRICE_CATEGORY_ALIASES: dict[str, str] = {
    "Tarieven tijdens een gezichtsbehandeling": "Tijdens een gezichtsbehandeling",
    "Harsen & Verven": "Harsen en verven",
    "Behandelingen": "Tarieven",
    "Overig": "Overige tarieven",
}


def _format_amount(amount: str) -> str:
    return amount.replace("€ ", "€").strip()


def _parse_amount(amount: str) -> float:
    """Parse '€36,50' / '€100,50' / '€11' naar float voor sortering."""
    digits = re.sub(r"[^\d,.]", "", amount).replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return 0.0


def _inventory_to_services(
    inventory: ContentInventory,
    price_groups: list[dict[str, Any]],
    max_cards: int = 4,
) -> list[dict[str, Any]]:
    """Bouw service-cards uit echte inventory-data.

    Strategie:
      - Multi-categorie: per categorie 1 representatieve behandeling (de duurste,
        gezien als 'signature' aanbod), beschrijving toont aantal + range.
      - Single-category: top behandelingen op prijs.
      - Geen prijzen: val terug op de hardcoded service-categorieën.
    """
    if not price_groups:
        return _extract_services("")  # leverage default

    multi = len(price_groups) > 1
    cards: list[dict[str, Any]] = []

    if multi:
        for group in price_groups[:max_cards]:
            items = group.get("items") or []
            if not items:
                continue
            # Kies 'signature' als de duurste, met fallback op laatste item.
            sorted_items = sorted(items, key=lambda i: _parse_amount(i.get("price", "")), reverse=True)
            head = sorted_items[0]
            n = len(items)
            min_price = min((_parse_amount(i.get("price", "")) for i in items), default=0)
            min_str = next(
                (i.get("price") for i in items if _parse_amount(i.get("price", "")) == min_price),
                "",
            )
            description = (
                f"{n} behandeling{'en' if n != 1 else ''}, vanaf {min_str}. "
                f"Bekend als signature: {head.get('label','')}."
            )
            cards.append({
                "key": group["title"].lower().replace(" ", "-"),
                "title": group["title"],
                "description": description,
                "href": "/tarieven/",
            })
    else:
        # Single category: pak de eerste N items in bron-volgorde.
        # 'Top op prijs' faalt voor kapsalons (duurste = keratine-extensies, niet
        # signature). De bron volgt vrijwel altijd belangrijkste-eerst.
        items = price_groups[0].get("items") or []
        for entry in items[:max_cards]:
            label = entry.get("label", "")
            cards.append({
                "key": label.lower().replace(" ", "-"),
                "title": label,
                "description": f"Vanaf {entry.get('price','')} — onderdeel van het volledige behandelaanbod.",
                "href": "/tarieven/",
            })

    if not cards:
        return _extract_services("")
    return cards


def _inventory_prices_to_groups(inventory: ContentInventory) -> list[dict[str, Any]]:
    """Map ContentInventory.prices naar de groups-shape die PriceList.astro leest.

    Output:
        [
          {"title": "Gezichtsverzorging",
           "items": [{"label": "Mini facial treatment (30 min)", "price": "€36,50"}, ...]},
          ...
        ]
    """
    grouped: dict[str, list[dict[str, str]]] = {}
    order: list[str] = []
    for item in inventory.prices:
        category = item.category or "Overig"
        if category not in grouped:
            grouped[category] = []
            order.append(category)
        # Normaliseer bedrag: zorg dat we altijd '€<bedrag>' renderen, ook als
        # de bron '€ 36,50' had (spatie ertussen).
        amount = item.amount.replace("€ ", "€").strip()
        grouped[category].append({"label": item.label, "price": amount})
    return [
        {
            "title": _PRICE_CATEGORY_ALIASES.get(cat, cat),
            "items": grouped[cat],
        }
        for cat in order
    ]


_PRICE_RE = re.compile(r"^(?P<label>.+?)\s+(?P<price>€\s?\d+(?:,\d{1,2})?)\.?$")


def _normalise_price_line(line: str) -> str:
    line = line.replace("\xa0", " ")
    line = re.sub(r"\s+", " ", line).strip()
    line = re.sub(r"^vanaf 1 jan\s+", "", line, flags=re.IGNORECASE)
    return line.strip(" .")


def _extract_price_groups(source_text: str) -> list[dict[str, Any]]:
    """Haal echte tariefregels uit de crawl-tekst, gegroepeerd per kop."""
    aliases = {
        "Gezichtsverzorging": "Gezichtsverzorging",
        "Harsen & Verven": "Harsen en verven",
        "Massages": "Massages",
        "Tarieven tijdens een gezichtsbehandeling": "Tijdens een gezichtsbehandeling",
    }
    groups: list[dict[str, Any]] = []
    current_title: str | None = None
    current_items: list[dict[str, str]] = []

    def flush() -> None:
        nonlocal current_title, current_items
        if current_title and current_items:
            groups.append({"title": current_title, "items": current_items})
        current_title = None
        current_items = []

    for raw_line in source_text.splitlines():
        line = _normalise_price_line(raw_line)
        if not line:
            continue
        if line.startswith("===") or line.startswith("(+31)") or line.startswith("©"):
            flush()
            current_title = None
            continue
        if line in aliases:
            flush()
            current_title = aliases[line]
            continue
        if current_title is None:
            continue
        if line.lower().startswith("let op"):
            flush()
            current_title = "Losse behandelingen"
            continue
        if line.lower().startswith(("wil je", "deze losse", "of via", "info@")):
            continue
        match = _PRICE_RE.match(line)
        if not match:
            continue
        label = match.group("label").strip()
        price = match.group("price").replace("€ ", "€").strip()
        if label and price:
            current_items.append({"label": label, "price": price})

    flush()
    return groups


def _between(text: str, start: str, end: str | None = None) -> str:
    start_index = text.find(start)
    if start_index < 0:
        return ""
    content_start = start_index + len(start)
    if not end:
        return text[content_start:].strip()
    end_index = text.find(end, content_start)
    if end_index < 0:
        return text[content_start:].strip()
    return text[content_start:end_index].strip()


def _extract_treatments(source_text: str) -> list[dict[str, Any]]:
    """Maak compacte behandelkaarten uit de originele crawl-tekst."""
    specs = [
        (
            "Botanical Beauty 60 min treatment",
            "Botanical Beauty 75 min treatment",
            "60 minuten",
            "€67",
        ),
        (
            "Botanical Beauty 75 min treatment",
            "Botanical Beauty 90 min treatment",
            "75 minuten",
            "€80",
        ),
        (
            "Botanical Beauty 90 min treatment",
            "Wellness massages",
            "90 minuten",
            "€100,50",
        ),
    ]
    treatments: list[dict[str, Any]] = []
    for title, end_marker, duration, price in specs:
        block = _between(source_text, title, end_marker)
        if not block:
            continue
        lines = [_normalise_price_line(line) for line in block.splitlines()]
        lines = [line for line in lines if line and line not in {"Vaste onderdelen van deze gezichtsbehandeling zijn:"}]
        intro = next((line for line in lines if not line.startswith("*") and "kost €" not in line), "")
        items = [line.lstrip("* ").strip() for line in lines if line.startswith("*")]
        treatments.append({
            "title": title,
            "duration": duration,
            "price": price,
            "body": intro or "Een gezichtsbehandeling met vaste verzorgingsrituelen uit de originele website.",
            "items": items,
        })

    massage_block = _between(source_text, "Wellness massages", "Lash Volume Lifting")
    if massage_block:
        lines = [_normalise_price_line(line) for line in massage_block.splitlines() if _normalise_price_line(line)]
        treatments.append({
            "title": "Wellness massages",
            "duration": "30 of 50 minuten",
            "price": "€36 / €54",
            "body": " ".join(lines[:2]),
            "items": [
                "30 minuten: rug, nek en schouders",
                "50 minuten: gehele achterzijde van het lichaam",
                "Gericht op ontspanning en het losmaken van pijnlijke spierknopen",
            ],
        })

    lash_block = _between(source_text, "Lash Volume Lifting", "Harsen & Verven")
    if lash_block:
        lines = [_normalise_price_line(line) for line in lash_block.splitlines() if _normalise_price_line(line)]
        body = " ".join(lines[:3])
        items = [
            "Eigen wimpers lijken langer en ogen lijken groter",
            "Resultaat 6 tot 8 weken zichtbaar",
            "Met verven van de wimpers als onderdeel van de behandeling",
        ]
        treatments.append({
            "title": "Lash Volume Lifting",
            "duration": "6 tot 8 weken resultaat",
            "price": "€52",
            "body": body,
            "items": items,
        })

    return treatments


def _extract_product_story(source_text: str, briefing: str) -> dict[str, Any]:
    haystack = f"{source_text}\n{briefing}"
    if "Botanical Beauty" not in haystack:
        return {}
    return {
        "title": "Botanical Beauty",
        "lead": "De salon werkt met Botanical Beauty: een natuurlijke en biologische productlijn voor huidverzorging.",
        "body": "De behandelingen worden afgestemd op wat jouw huid op dat moment nodig heeft. In de originele site komt Botanical Beauty terug als basis voor de gezichtsbehandelingen en als bewuste keuze voor natuurlijke verzorging.",
        "bullets": [
            "100% natuurlijke en biologische productlijn",
            "Gebruikt bij de Botanical Beauty gezichtsbehandelingen",
            "Productkeuze per behandeling afgestemd op de huid",
        ],
    }


def _decorate_pages(
    pages: list[dict[str, Any]],
    services: list[dict[str, Any]],
    company_name: str,
    contact: dict[str, str],
    price_groups: list[dict[str, Any]],
    treatments: list[dict[str, Any]],
    products: dict[str, Any],
    home_section_order: list[str] | None = None,
    pages_content: dict[str, Any] | None = None,
    voice_copy: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Maak subpagina's inhoudelijk verschillend zonder runtime codegeneratie.

    Wanneer pages_content (uit inventory) een match heeft op de page-slug,
    wint die als bron voor lead/body/bullets — zo krijgen prospects hun eigen
    verbatim woorden op de subpagina i.p.v. de hardcoded fallback-copy.
    """
    service_titles = [service.get("title", "Behandeling") for service in services]
    first_services = service_titles[:4] or ["persoonlijke behandeling"]
    home_sections = home_section_order or [
        "services", "about", "gallery", "reviews", "openingHours", "contactCta"
    ]
    pcs = pages_content or {}
    vc = voice_copy or {
        "behandeling_note":  "Twijfel je welke behandeling past? Bel of WhatsApp, dan denken we even met je mee.",
        "tarieven_helper":   "Hieronder vind je per categorie de actuele tarieven. Vraag bij twijfel altijd vooraf naar wat de behandeling voor jou kost, we plannen genoeg tijd in en denken graag mee.",
    }

    def _fuzzy_match_page(slug: str) -> Any:
        """Vind de beste pages_content match voor een pages.json-slug.

        Slug-mismatches komen veel voor: pages.json zegt 'knippen',
        text.txt heeft 'prijzen-knipbehandelingen'; pages.json zegt
        'nagels', text.txt heeft 'beautybehandelingen-valk-beauty-health/
        nagels/overzicht-nagels'. Een substring/stem-match dekt deze
        gevallen zonder per-prospect handwerk.

        Score-based:
          + 10 als hele slug substring is van text-key
          +  4 per token (>=4 chars) van slug dat in text-key voorkomt
          +  2 per stem-match (eerste 4 chars van token >=5 chars)
          +  2 als text-key 'overzicht' bevat (landing-page van categorie)
          -  2 per pad-niveau in text-key (voorkeur voor top-level)
          tiebreak: meeste paragrafen wint
        """
        if not slug or not pcs:
            return None
        if slug in pcs:
            return pcs[slug]
        slug_tokens = [t for t in re.split(r"[-_/]", slug) if len(t) >= 4]
        best = None
        best_score = 0
        for key, pc in pcs.items():
            if not key:
                continue
            score = 0
            if slug in key:
                score += 10
            for token in slug_tokens:
                if token in key:
                    score += 4
                elif len(token) >= 5 and token[:4] in key:
                    score += 2
            if "overzicht" in key:
                score += 2
            score -= 2 * key.count("/")
            if score <= 0:
                continue
            pc_paras = getattr(pc, "paragraph_count", 0)
            best_paras = getattr(best, "paragraph_count", 0) if best else 0
            if score > best_score or (score == best_score and pc_paras > best_paras):
                best = pc
                best_score = score
        return best

    def _from_pages_content(slug: str) -> tuple[str, str, list[str]]:
        """Helper: pak verbatim lead/body/bullets uit inventory wanneer er een
        (fuzzy) page-content match is voor deze slug. Geeft ('', '', []) terug
        als geen match — dan vallen we terug op hardcoded copy."""
        pc = _fuzzy_match_page(slug)
        if not pc:
            return "", "", []
        lead = pc.lead if hasattr(pc, "lead") else (pc.get("lead") if isinstance(pc, dict) else "")
        body = pc.body if hasattr(pc, "body") else (pc.get("body") if isinstance(pc, dict) else "")
        bullets = pc.bullets if hasattr(pc, "bullets") else (pc.get("bullets") if isinstance(pc, dict) else [])
        return lead or "", body or "", list(bullets or [])

    for page in pages:
        slug = page.get("slug", "")
        if not slug:
            page["sections"] = list(home_sections)
            continue

        title = page.get("title") or slug.replace("-", " ").title()
        lower = f"{slug} {title}".lower()
        verbatim_lead, verbatim_body, verbatim_bullets = _from_pages_content(slug)

        if any(word in lower for word in ("behandeling", "diensten", "service")):
            page["sections"] = ["pageContent", "treatments", "services", "gallery", "contactCta"]
            page["content"] = {
                "eyebrow": "Behandelingen",
                "title": title,
                "lead": verbatim_lead or page.get("description") or "Kies de behandeling die past bij jouw huid, wensen en moment.",
                "body": verbatim_body or "Deze pagina gebruikt de behandelteksten uit de originele website, inclusief duur, vaste onderdelen en bekende tarieven.",
                "bullets": verbatim_bullets or [f"{name} met aandacht voor jouw wensen" for name in first_services],
                "note": vc.get("behandeling_note", "Twijfel je welke behandeling past? Bel of WhatsApp, dan denken we even met je mee."),
            }
        elif any(word in lower for word in ("tarief", "tarieven", "prijs", "prijzen", "prijslijst")):
            page["sections"] = ["pageContent", "prices", "contactCta"]
            n_items = sum(len(group.get("items", [])) for group in price_groups)
            n_categories = len(price_groups)
            default_bullets = [
                f"Verdeeld over {n_categories} categorieën" if n_categories > 1 else f"{n_items} behandelingen op één plek",
                "Vaste prijs per behandeling, geen verborgen kosten",
                "Combineer losse behandelingen met een gezichtsbehandeling voor korting",
            ]
            page["content"] = {
                "eyebrow": "Tarieven",
                "title": title,
                "lead": verbatim_lead or page.get("description") or "Heldere prijzen, geen verrassingen.",
                "body": verbatim_body or vc.get("tarieven_helper", "Hieronder vind je per categorie de actuele tarieven."),
                "bullets": verbatim_bullets or default_bullets,
                "note": "",
            }
        elif any(word in lower for word in ("product", "merk", "shop")):
            page["sections"] = ["pageContent", "products", "gallery", "contactCta"]
            page["content"] = {
                "eyebrow": "Producten",
                "title": title,
                "lead": verbatim_lead or page.get("description") or products.get("lead") or "Productadvies dat past bij jouw huid en routine.",
                "body": verbatim_body or products.get("body") or "Deze pagina gebruikt alleen productnamen en productclaims die in de crawl of briefing gevonden zijn.",
                "bullets": verbatim_bullets or products.get("bullets") or ["Advies op basis van jouw huid en wensen"],
                "note": "Productclaims blijven bewust beperkt tot wat in de originele bron is gevonden.",
            }
        elif any(word in lower for word in ("over", "mij", "ons", "salon")):
            page["sections"] = ["pageContent", "about", "reviews", "contactCta"]
            page["content"] = {
                "eyebrow": "Persoonlijk",
                "title": title,
                "lead": verbatim_lead or page.get("description") or f"Maak kennis met {company_name}.",
                "body": verbatim_body or "Een beauty/wellness-site verkoopt vertrouwen. Deze pagina geeft ruimte aan het verhaal van de behandelaar, de sfeer in de salon en de manier van werken.",
                "bullets": verbatim_bullets or [
                    "Persoonlijke aandacht in een rustige setting",
                    "Een herkenbaar verhaal in de woorden van de salon",
                    "Geen verzonnen reviews of claims",
                ],
                "note": "Aanvullen met extra eigenaarstekst zodra die beschikbaar is.",
            }
        elif "contact" in lower:
            address = contact.get("address") or "Adres wordt bevestigd door de eigenaar"
            phone = contact.get("phone_display") or contact.get("phone") or "Telefoonnummer wordt bevestigd"
            page["sections"] = ["pageContent", "openingHours", "contactCta"]
            page["content"] = {
                "eyebrow": "Contact",
                "title": title,
                "lead": verbatim_lead or page.get("description") or "Plan je afspraak of stel je vraag direct.",
                "body": verbatim_body or "Gebruik deze pagina voor de praktische contactgegevens, afspraakroute en bereikbaarheid.",
                "bullets": verbatim_bullets or [
                    f"Adres: {address}",
                    f"Telefoon: {phone}",
                    "WhatsApp is beschikbaar als er een mobiel nummer is gevonden",
                ],
                "note": "Openingstijden worden niet verzonnen; bij twijfel tonen we afspraak op aanvraag.",
            }
        else:
            page["sections"] = ["pageContent", "services", "contactCta"]
            page["content"] = {
                "eyebrow": "Informatie",
                "title": title,
                "lead": verbatim_lead or page.get("description") or f"Meer over {title.lower()}.",
                "body": verbatim_body or "Deze pagina gebruikt eigen content uit de briefing zodra die beschikbaar is. Tot die tijd blijft de tekst bewust compact en eerlijk.",
                "bullets": verbatim_bullets or [
                    "Heldere informatie zonder verzonnen claims",
                    "Aansluitend op de bestaande briefing",
                    "Met een directe route naar contact",
                ],
                "note": "Deze pagina kan later worden verdiept met extra eigenaarstekst.",
            }

    # Inventory-data mag niet in de site verloren gaan: als prices bestaan maar
    # geen pagina ze rendert (bv. kapsalon zonder /tarieven/-slug), hang ze op
    # de meest logische bestaande pagina.
    if price_groups:
        renders_prices = any("prices" in (p.get("sections") or []) for p in pages)
        if not renders_prices:
            target = None
            preferred = ("behandel", "knippen", "kleuren", "service", "diensten", "tarief")
            for p in pages:
                slug = (p.get("slug") or "").lower()
                title = (p.get("title") or "").lower()
                if any(w in f"{slug} {title}" for w in preferred):
                    target = p
                    break
            if target is None:
                target = next((p for p in pages if not p.get("slug")), None)
            if target is not None:
                sections = list(target.get("sections") or [])
                insert_at = sections.index("pageContent") + 1 if "pageContent" in sections else 0
                sections.insert(insert_at, "prices")
                target["sections"] = sections

    return pages


# ── Asset selectie ───────────────────────────────────────────────────────────
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _find_assets(collected_path: Path) -> list[Path]:
    """Vind alle bruikbare bitmap-foto's onder collected/assets, gesorteerd."""
    assets_dir = collected_path / "assets"
    if not assets_dir.exists():
        return []
    images: list[Path] = []
    for path in assets_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        rel = str(path.relative_to(assets_dir)).replace("\\", "/")
        if any(pattern.search(rel) for pattern in _GALLERY_BLOCKLIST):
            continue
        images.append(path)
    return sorted(images)


def _pick_first(images: list[Path], patterns: list[re.Pattern[str]]) -> Path | None:
    for pattern in patterns:
        for image in images:
            if pattern.search(image.name) or pattern.search(str(image)):
                return image
    return None


def _pick_gallery(images: list[Path], exclude: set[Path], limit: int = 8) -> list[Path]:
    selected: list[Path] = []
    seen: set[str] = set()
    # Eerst voorkeurspatronen
    for pattern in _GALLERY_PATTERNS_PREFER:
        for image in images:
            if image in exclude or image.name in seen:
                continue
            if pattern.search(image.name):
                selected.append(image)
                seen.add(image.name)
                if len(selected) >= limit:
                    return selected
    # Fallback: vul aan met overige assets
    for image in images:
        if image in exclude or image.name in seen:
            continue
        selected.append(image)
        seen.add(image.name)
        if len(selected) >= limit:
            break
    return selected


# ── Beauty/Wellness Archetype ────────────────────────────────────────────────
# Quality contract: wat moet de inventory aanleveren voordat we mogen bouwen?
# Geen LLM, alleen deterministische checks (zie v2/pipeline/quality_gate.py).
_BEAUTY_WELLNESS_CONTRACT: list[FieldCheck] = [
    FieldCheck(
        field="contact.phone",
        rule="required",
        severity="fail",
        rationale="Lokale salon zonder bel-CTA verliest direct conversies",
    ),
    FieldCheck(
        field="contact.address",
        rule="recommended",
        severity="warn",
        rationale="Adres is vereist voor lokale SEO + Google Maps embed",
    ),
    FieldCheck(
        field="treatments",
        rule="min_count",
        severity="fail",
        min_count=3,
        rationale="Beauty/wellness archetype gaat over behandelingen — minder dan 3 wijst op extractie-falen",
    ),
    FieldCheck(
        field="prices",
        rule="required_if_evidence",
        severity="fail",
        evidence_pattern=r"€\s?\d|\b\d+,\d{2}\s*€",
        # 3+ €-mentions = waarschijnlijk een prijslijst. 1-2 mentions zijn
        # vaak losse fees in voorwaarden of cursus-prijzen die geen
        # tarievenpagina rechtvaardigen.
        evidence_min_matches=3,
        rationale="Als de bron 3+ €-bedragen bevat, móét /tarieven/ ze tonen — anders is de site een downgrade",
    ),
    FieldCheck(
        field="opening_hours",
        rule="required_if_evidence",
        severity="warn",
        evidence_pattern=r"(?im)\b(?:maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag)\b.{0,40}\d{1,2}[:.]\d{2}",
        rationale="Bron noemt openingstijden — extractor heeft ze gemist",
    ),
    FieldCheck(
        field="reviews",
        rule="required_if_evidence",
        severity="warn",
        # \b voorkomt match in compound tokens als 'testimonials_photo' (de
        # WordPress widget-placeholder die we juist NIET als evidence willen).
        evidence_pattern=r"(?i)\b(?:review|testimonial|aanrader|tevreden klant|geweldige? ervaring)\b",
        rationale="Bron suggereert reviews — controleer of inventory ze terecht overslaat",
    ),
    FieldCheck(
        field="images",
        rule="min_count",
        severity="warn",
        min_count=5,
        rationale="Beauty-site zonder voldoende sfeerfoto's voelt leeg",
    ),
    FieldCheck(
        field="pages",
        rule="min_count",
        severity="warn",
        min_count=3,
        rationale="Minder dan 3 pagina's wijst op een onvolledige crawl",
    ),
]


class BeautyWellnessArchetype:
    name = "beauty_wellness"
    template_dir_name = "beauty_wellness"

    def get_contract(self) -> list[FieldCheck]:
        return list(_BEAUTY_WELLNESS_CONTRACT)

    def detect(
        self,
        briefing: str,
        structured: dict[str, Any],
        meta: dict[str, Any],
    ) -> ArchetypeMatch:
        haystack_parts: list[str] = [briefing.lower()]
        nav = structured.get("nav") or []
        if isinstance(nav, list):
            haystack_parts.append(" ".join(str(item).lower() for item in nav))
        title = (meta.get("title") or "").lower()
        haystack_parts.append(title)
        haystack = "\n".join(haystack_parts)

        score = 0.0
        reasons: list[str] = []
        for keyword in _HEAVY_KEYWORDS:
            if keyword in haystack:
                score += 1.0
                reasons.append(f"heavy keyword: {keyword}")
        for keyword in _LIGHT_KEYWORDS:
            if keyword in haystack:
                score += 0.4
                reasons.append(f"light keyword: {keyword}")
        return ArchetypeMatch(score=score, reasons=reasons[:8])

    def build_plan(
        self,
        slug: str,
        collected_path: Path,
        briefing: str,
        structured: dict[str, Any],
        meta: dict[str, Any],
        inventory: ContentInventory | None = None,
    ) -> dict[str, Any]:
        sections = _split_sections(briefing)
        source_text_path = collected_path / "text.txt"
        source_text = source_text_path.read_text(encoding="utf-8", errors="ignore") if source_text_path.exists() else ""
        # Inventory is single source of truth voor feiten (prijzen, behandelingen,
        # contact, ...). Kan vooraf gebouwd zijn door de gate; anders nu opbouwen.
        if inventory is None:
            inventory = build_inventory(slug, collected_path)
        company_name = meta.get("company_name") or structured.get("title") or meta.get("title") or slug.replace("-", " ").title()
        company_name = re.sub(r"\s+[Vv]\d+$", "", company_name).strip()
        # Inventory wint van de oude extractor: hij valt ook text.txt mee terug.
        inv_contact = inventory.contact
        contact = {
            "phone": inv_contact.phone,
            "phone_display": inv_contact.phone_display,
            "email": inv_contact.email,
            "address": inv_contact.address,
        }
        # Voor backwards compat met _extract_contact-callers (legacy):
        if not contact["phone"] or not contact["address"]:
            legacy = _extract_contact(structured, briefing)
            for key, value in legacy.items():
                if not contact.get(key):
                    contact[key] = value
        social_raw = structured.get("social_links", {}) if isinstance(structured.get("social_links"), dict) else {}
        whatsapp_url = ""
        if contact.get("phone"):
            digits = re.sub(r"\D", "", contact["phone"])
            if digits:
                whatsapp_url = f"https://wa.me/{digits}"
        social = {
            "facebook": social_raw.get("facebook", ""),
            "instagram": social_raw.get("instagram", ""),
            "whatsapp": whatsapp_url,
        }

        # Bron-of-truth voor tarieven is nu de inventory (incl. provenance).
        # _extract_price_groups blijft voorlopig staan voor backwards-compat /
        # vergelijkings-debug; de inventory-mapping wint.
        price_groups = _inventory_prices_to_groups(inventory)
        # Service-cards komen uit echte inventory-data wanneer beschikbaar;
        # anders fallback op de generieke 4-categorie-lijst uit de briefing.
        services = _inventory_to_services(inventory, price_groups)
        treatments = _extract_treatments(source_text)
        products = _extract_product_story(source_text, briefing)
        # Prefer verbatim signatures uit text.txt (inventory). Briefing-fallback
        # alleen als de inventory niets vond. Zo komen Carlijn's "Mijn doel
        # is om jou even helemaal in de watten te leggen" en Valk's "Ik ben
        # Cynthia Valk..." letterlijk op de site, niet hertaald.
        if inventory.signatures:
            signature = inventory.signatures[0].quote
        else:
            signature = _extract_signature(sections)

        about_body, about_signature = _extract_about_quotes(sections)
        # Als inventory >=2 signatures heeft, gebruik die voor about-body en
        # about-signature i.p.v. de briefing-quotes.
        if len(inventory.signatures) >= 2:
            about_signature = inventory.signatures[0].quote
            about_body = inventory.signatures[1].quote
        elif inventory.signatures:
            about_signature = inventory.signatures[0].quote
        if not about_body:
            about_body = sections.get("project", "").splitlines()[0] if sections.get("project") else company_name

        # Hero copy: source_copy.tagline (uit og:description / h1) wint van
        # signature. Origineel-uniek voor elke prospect.
        project_lines = _bullet_lines(sections.get("project", ""))
        hero_body = ""
        sc = inventory.source_copy
        if sc.tagline and len(sc.tagline) >= 30:
            hero_body = _clean_dashes(sc.tagline)
        elif signature:
            hero_body = signature
        elif project_lines:
            hero_body = _clean_dashes(project_lines[0])

        # Originele primary CTA-label (bv. "Boek nu", "Maak een afspraak")
        # wint van onze gegenereerde "Online reserveren".
        original_cta = sc.primary_cta if sc.primary_cta and len(sc.primary_cta) >= 4 else ""

        location = ""
        for line in project_lines:
            if re.search(r"\d{4}\s?[A-Z]{2}", line):
                parts = line.split(",")
                if len(parts) >= 2:
                    location = parts[-1].strip().split(" ")[-1]
                break

        eyebrow = "Beauty en wellness"
        if location:
            eyebrow = f"Beautysalon, {location}"

        # Layout-heuristieken: kies section-volgorde + hero-variant + gallery-
        # variant op basis van het type signature en de inventory-rijkdom.
        # Doel: drie prospects van hetzelfde archetype krijgen drie zichtbaar
        # verschillende home-pagina's, zonder per-prospect handwerk.
        sig_type = _signature_type(inventory.signatures)
        home_section_order = _pick_home_section_order(sig_type)
        # Voice profile bepaalt aanspreekvorm voor CTA-copy en sub-page noten.
        voice_copy = _voice_copy(inventory.voice_profile)
        # Personality bepaalt micro-signalen + skeleton-keuzes (hero variant).
        # 4 zelfde-archetype-prospects krijgen daarmee echt verschillende
        # shapes, niet alleen verschillende kleuren.
        personality = pick_personality(inventory)
        hero_variant = _pick_hero_variant(sig_type, has_image=bool(inventory.images),
                                          personality_name=personality.name)
        gallery_variant = _pick_gallery_variant(sig_type, n_categories=len(price_groups))

        pages = _decorate_pages(
            _extract_pages(collected_path),
            services,
            company_name,
            contact,
            price_groups,
            treatments,
            products,
            home_section_order=home_section_order,
            pages_content=inventory.pages_content,
            voice_copy=voice_copy,
        )

        plan: dict[str, Any] = {
            "archetype": self.name,
            "slug": slug,
            "company_name": company_name,
            "tagline": meta.get("description") or structured.get("meta_tags", {}).get("description", ""),
            "source_url": meta.get("url", ""),
            "logo": "",
            "theme": _theme_from_visual_dna(inventory) or _extract_theme(briefing, sections),
            "contact": contact,
            "social": social,
            "pages": pages,
            "headings": {
                "services": {"eyebrow": "Behandelingen", "title": "Wat ik voor je doe"},
                "gallery":  {"eyebrow": "Sfeer",         "title": "Even rondkijken in de salon"},
                "reviews":  {"eyebrow": "Vertrouwen",    "title": "Hoe ik werk"},
                "openingHours": {"eyebrow": "Plannen",   "title": "Wanneer je terecht kunt"},
                "about":    {"eyebrow": "Persoonlijk",   "title": f"Over {company_name}"},
                "stats":    {"eyebrow": "In cijfers",     "title": ""},
                "process":  {"eyebrow": "Werkwijze",      "title": "Hoe wij werken"},
                "featured": {"eyebrow": "Signature",      "title": "Onze signature behandeling"},
            },
            "hero": {
                "eyebrow": eyebrow,
                "headline": company_name,
                "body": hero_body,
                "image": "",
                "variant": hero_variant,
                "primaryCta": {"label": original_cta or voice_copy["contact_cta_primary"], "href": "#contact"},
                "secondaryCta": {"label": voice_copy["hero_secondary"], "href": "#diensten"},
                "meta": [
                    {"label": "Persoonlijk", "value": "altijd één behandelaar"},
                    {"label": "Locatie",     "value": location or "in de regio"},
                    {"label": "Plannen",     "value": "telefoon of WhatsApp"},
                ],
            },
            "variants": {
                "gallery": gallery_variant,
                "personality": personality.to_dict(),
                "_signature_type": sig_type,
                "_home_section_order": home_section_order,
            },
            "typography": inventory.typography.to_dict(),
            "services": services,
            "prices": {
                "title": "Tarieven",
                "groups": price_groups,
                "note": "Bedragen zijn richtprijzen, je hoort vooraf de exacte prijs voor jouw behandeling.",
            },
            "treatments": treatments,
            "products": products,
            "gallery": [],
            "reviews": _extract_proof(sections, company_name),
            "openingHours": {
                "items": [],
                "fallback": "Openingstijden gaan op afspraak. Bel of WhatsApp ons direct, dan plannen we samen een moment in.",
            },
            "about": {
                "title": f"Over {company_name}",
                "body": _clean_dashes(about_body),
                "signature": about_signature,
                "image": "",
            },
            "contactCta": {
                "title": voice_copy["contact_cta_title"],
                "body":  voice_copy["contact_cta_body"],
                "primary":   {"label": original_cta or voice_copy["contact_cta_primary"], "href": "#contact"},
                "secondary": {"label": voice_copy["contact_cta_secondary"], "href": f"tel:{contact['phone']}" if contact.get("phone") else "#"},
                "tertiary":  {"label": voice_copy["contact_cta_tertiary"],  "href": whatsapp_url} if whatsapp_url else None,
            },
            "stats": [
                {"value": s["value"] if isinstance(s, dict) else s.value,
                 "label": s["label"] if isinstance(s, dict) else s.label}
                for s in inventory.source_copy.stats
            ],
            # Featured service: pak de duurste / signature behandeling met
            # foto, voor een spotlight-section. Alleen als services rijk zijn.
            "featured": _pick_featured_service(services, price_groups),
            # Process-steps: voor sites met duidelijke werkwijze. Standaard
            # genericke 3-stap-flow; alleen tonen als briefing er over praat.
            "process": _pick_process_steps(briefing, sections),
        }
        if not plan["contactCta"]["tertiary"]:
            plan["contactCta"].pop("tertiary")
        return plan

    def render(self, plan: dict[str, Any], collected_path: Path, out_dir: Path, force: bool = False) -> None:
        # Late import om circulaire imports te voorkomen wanneer __init__ geladen wordt.
        from v2.generators.astro.render_archetype import render_archetype_site

        render_archetype_site(
            template_dir_name=self.template_dir_name,
            plan=plan,
            collected_path=collected_path,
            out_dir=out_dir,
            force=force,
            asset_picker=_pick_archetype_assets,
        )


def _pick_archetype_assets(collected_path: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Selecteer en bepaal welke afbeeldingen waar gebruikt worden.

    Geeft een dict terug met:
    - copies: lijst van (src_abs, dst_rel) tuples voor copy-naar-public
    - plan_patches: dict met velden in plan die geüpdatet moeten worden met
      relatieve URL's (bv. logo, hero.image, about.image, gallery, services[i].image)
    """
    images = _find_assets(collected_path)
    copies: list[tuple[Path, str]] = []
    patches: dict[str, Any] = {}

    # Logo: collected_path/logo.* heeft prioriteit.
    logo_src: Path | None = None
    for candidate in collected_path.glob("logo.*"):
        if candidate.suffix.lower() in _IMAGE_SUFFIXES:
            logo_src = candidate
            break
    if logo_src:
        dst = f"assets/logo{logo_src.suffix.lower()}"
        copies.append((logo_src, dst))
        patches["logo"] = f"/{dst}"

    used: set[Path] = set()

    hero_src = _pick_first(images, _HERO_PATTERNS)
    # Fallback: als geen pattern-match, neem de eerste decente photo. _find_assets
    # filtert al ruis (bg_noise, sprites, icons) weg, dus images[0] is meestal
    # bruikbaar. Dit voorkomt dat prospects zonder Carlijn-achtige bestandsnamen
    # met een leeg hero-image renderen (relevant voor de image-bg variant).
    if hero_src is None:
        for candidate in images:
            if candidate == logo_src:
                continue
            hero_src = candidate
            break
    if hero_src:
        dst = f"assets/hero{hero_src.suffix.lower()}"
        copies.append((hero_src, dst))
        patches["hero_image"] = f"/{dst}"
        used.add(hero_src)

    about_src = _pick_first(images, _PORTRAIT_PATTERNS)
    if about_src and about_src != hero_src:
        dst = f"assets/about{about_src.suffix.lower()}"
        copies.append((about_src, dst))
        patches["about_image"] = f"/{dst}"
        used.add(about_src)

    # Service-fotos: een per service, op patroon-volgorde.
    service_image_pool = [img for img in images if img not in used]
    service_assignments: list[str] = []
    for index, _service in enumerate(plan.get("services", [])):
        if index >= len(service_image_pool):
            service_assignments.append("")
            continue
        src = service_image_pool[index]
        dst = f"assets/services/service-{index + 1}{src.suffix.lower()}"
        copies.append((src, dst))
        service_assignments.append(f"/{dst}")
        used.add(src)
    patches["service_images"] = service_assignments

    gallery_picks = _pick_gallery(images, used, limit=8)
    gallery_entries: list[dict[str, str]] = []
    for index, src in enumerate(gallery_picks):
        dst = f"assets/gallery/photo-{index + 1}{src.suffix.lower()}"
        copies.append((src, dst))
        gallery_entries.append({"src": f"/{dst}", "alt": f"Sfeerfoto {index + 1}"})
    patches["gallery"] = gallery_entries

    return {"copies": copies, "patches": patches}
