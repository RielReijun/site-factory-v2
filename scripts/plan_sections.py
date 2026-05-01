"""
plan_sections.py — Hybride pagina-planner.

Stap 1: Claude bedenkt sectie-volgorde + narratieve copy uit briefing
        (hero-headline, about-body, page_hero-titel, cta-tekst).
Stap 2: Een deterministische pass overschrijft alle FEIT-secties met data
        uit inventory.json:
            - pricing      → uit inventory.prices (geen 'AANNEMELIJK'-prijzen meer)
            - contact      → uit inventory.contact (phone/email/address verbatim)
            - services     → uit inventory.treatments (titel + bron-confidence)
            - testimonials → uit inventory.reviews (verwijder sectie als 0)

Resultaat: één <slug>-plan.json per pagina, met *AI-narratief* + *verbatim-feiten*.

Geen sectie meer met 'AANNEMELIJK: zie prijslijst' — wat de bron heeft, komt mee.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline_utils import get_claude_client, get_model, with_retry
from component_registry import get_schema_description


DAISYUI_THEMES = [
    "light", "dark", "cupcake", "bumblebee", "emerald", "corporate",
    "retro", "garden", "forest", "aqua", "lofi", "pastel",
    "luxury", "dracula", "autumn", "business", "coffee", "winter",
    "dim", "nord", "sunset", "lemonade",
]


# ── Theme picker (onveranderd) ───────────────────────────────────────────────
def pick_theme(client, model: str, briefing: str) -> str:
    """Laat Claude het beste DaisyUI-theme kiezen op basis van de briefing."""
    prompt = f"""Kies het beste DaisyUI-theme voor deze website op basis van de briefing.

Beschikbare themes en hun sfeer:
- light: neutraal/clean
- dark: donker/professioneel
- cupcake: zacht/roze/vrouwelijk
- bumblebee: geel/energiek
- emerald: groen/fris
- corporate: blauw/zakelijk
- retro: warm/vintage
- garden: aards/natuur
- forest: donkergroen/natuur
- aqua: blauw/water/zwembad
- lofi: minimaal/rustig
- pastel: zachte kleuren/lief
- luxury: goud/donker/exclusief
- dracula: paars/donker/modern
- autumn: warm/herfstkleuren
- business: grijs/zakelijk
- coffee: bruin/warm/gezellig
- winter: koel/blauw/fris
- dim: gedempte donkere tinten
- nord: nordic/minimaal/koel
- sunset: oranje/roze/warm
- lemonade: geel/fris/zomers

Briefing:
{briefing[:3000]}

Geef ALLEEN de theme-naam terug, geen uitleg."""

    try:
        response = with_retry(
            lambda: client.messages.create(
                model=model, max_tokens=20,
                messages=[{"role": "user", "content": prompt}]
            ),
            label="pick_theme",
        )
        theme = "".join(b.text for b in response.content
                        if getattr(b, "type", None) == "text").strip().lower()
        if theme in DAISYUI_THEMES:
            return theme
    except Exception:
        pass
    return "light"


# ── Inventory loader ─────────────────────────────────────────────────────────
def load_inventory(brief_path: Path) -> dict:
    """Lees inventory.json uit dezelfde directory als briefing.md.
    Geeft {} terug als het bestand niet bestaat (graceful fallback)."""
    inv_path = brief_path.parent / "inventory.json"
    if not inv_path.exists():
        return {}
    try:
        return json.loads(inv_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[WARN] kon inventory.json niet lezen: {exc}")
        return {}


def _summarize_inventory_for_prompt(inv: dict) -> str:
    """Compact briefing-fragment met de feiten die de LLM mag citeren in
    narratief (hero/about/cta-copy). De LLM ziet *signatures*, *tagline* en
    *primary_cta* zodat de gegenereerde teksten bron-stem ademen."""
    if not inv:
        return ""

    lines: list[str] = []
    sc = inv.get("source_copy") or {}
    if sc.get("tagline"):
        lines.append(f"**Tagline (verbatim uit og:description):** {sc['tagline']}")
    if sc.get("primary_cta"):
        lines.append(f"**Primaire CTA (verbatim uit knoppen):** {sc['primary_cta']}")
    if sc.get("cta_labels"):
        lines.append(f"**Andere CTA-labels uit de bron:** {', '.join(sc['cta_labels'][:6])}")

    sigs = inv.get("signatures") or []
    if sigs:
        lines.append("**Signature-zinnen (verbatim citeren in hero/about):**")
        for s in sigs[:5]:
            q = s.get("quote", "").strip()
            if q:
                lines.append(f"  > \"{q}\"")

    contact = inv.get("contact") or {}
    contact_parts = []
    if contact.get("phone_display"): contact_parts.append(f"tel: {contact['phone_display']}")
    if contact.get("email"):         contact_parts.append(f"email: {contact['email']}")
    if contact.get("address"):       contact_parts.append(f"adres: {contact['address']}")
    if contact_parts:
        lines.append(f"**Contact:** {' | '.join(contact_parts)}")

    treatments = inv.get("treatments") or []
    if treatments:
        names = [t.get("name") for t in treatments[:8] if t.get("name")]
        lines.append(f"**Treatments (uit prijslijst):** {', '.join(names)}")

    n_prices = len(inv.get("prices") or [])
    n_reviews = len(inv.get("reviews") or [])
    n_hours   = len(inv.get("opening_hours") or [])
    lines.append(
        f"**Inventory-tellers:** prijzen={n_prices}, reviews={n_reviews}, "
        f"openingstijden={n_hours}"
    )

    voice = inv.get("voice_profile") or {}
    if voice.get("addressing") in ("je", "u"):
        lines.append(f"**Aanspreekvorm (gemeten):** {voice['addressing']}-vorm, "
                     f"formality={voice.get('formality','gemengd')}")

    if not lines:
        return ""
    return "## Bron-feiten (verbatim — citeer in narratief, NIET parafraseren)\n" + "\n".join(lines) + "\n"


# ── LLM plan-call ────────────────────────────────────────────────────────────
def plan_page(client, model: str, briefing: str, page_slug: str,
              page_title: str, page_desc: str, images: list[str],
              nav_pages: list[str], is_homepage: bool = False,
              theme: str = "light", inventory: dict | None = None) -> dict:
    """Vraag Claude om een JSON-sectieplan. Inventory-fragment wordt mee in
    de prompt gestopt zodat narratief copy bron-stem behoudt."""

    schema = get_schema_description()
    images_str = "\n".join(f"- {img}" for img in images[:20]) if images else "Geen afbeeldingen beschikbaar."
    nav_str    = ", ".join(nav_pages) if nav_pages else ""
    inv_block  = _summarize_inventory_for_prompt(inventory or {})

    page_context = (
        "Dit is de **homepage** — gebruik een uitgebreid hero-blok en toon de "
        "breedte van het aanbod."
    ) if is_homepage else (
        f"Dit is de pagina **{page_title}** ({page_desc}). Gebruik een page_hero "
        "als eerste sectie."
    )

    prompt = f"""Je bent een webstrateeg die een pagina-opzet maakt voor een statische Next.js website.

{page_context}

Geef een JSON-object met de secties voor deze pagina.
Gebruik ALLEEN de beschikbare sectietypes hieronder.
Vul ECHTE inhoud in op basis van de briefing en bron-feiten — verzin NIETS.

BELANGRIJK over feit-secties (pricing, contact, testimonials):
Een deterministische pass overschrijft die secties NA jouw output met
verbatim data uit inventory.json. Voeg ze gerust toe als ze relevant zijn,
maar je hoeft prijzen, telefoonnummers of reviews zelf NIET in te vullen —
laat de content-velden voor die secties leeg of zet placeholders.

Voor narratieve secties (hero, about, page_hero, cta) MAG je geen feiten
verzinnen, maar WEL de signature-zinnen en tagline uit bron-feiten citeren.

## Beschikbare secties
{schema}

## Beschikbare afbeeldingen (gebruik exacte paden)
{images_str}

## Navigatielinks voor CTA's
{nav_str}

{inv_block}
## Briefing (strategie + tone)
{briefing[:8000]}

---

Geef ALLEEN dit JSON-object terug, geen uitleg:
{{
  "sections": [
    {{"type": "...", "variant": "...", "content": {{...}}}},
    ...
  ]
}}

Regels:
- homepage: 4-6 secties inclusief hero en cta (laatste voor footer)
- subpagina: 3-5 secties inclusief page_hero en cta (laatste)
- Voeg geen testimonials toe als reviews=0 in inventory-tellers
- Voeg geen pricing toe als prijzen=0 in inventory-tellers
- Afbeeldingen: alleen paden gebruiken die hierboven staan
"""

    def _call():
        return client.messages.create(
            model=model, max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )

    response = with_retry(_call, label=f"plan:{page_slug}")
    raw = "".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()
    raw = re.sub(r'^```\w*\s*', '', raw, flags=re.MULTILINE)
    raw = re.sub(r'\s*```\s*$', '', raw, flags=re.MULTILINE)

    try:
        plan = json.loads(raw.strip())
    except json.JSONDecodeError as e:
        raise ValueError(f"Ongeldige JSON van Claude voor {page_slug}: {e}\n{raw[:300]}")

    if theme:
        plan["theme"] = theme
    return plan


# ── Deterministische injectie van inventory-feiten ───────────────────────────
def _truncate(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def _build_pricing_plans(prices: list[dict]) -> list[dict]:
    """Map inventory.prices naar pricing/table-schema: plans=[{name,price,description}].

    De template heeft één tabel met `plans`; we voegen een 'description' toe
    die de categorie noemt (zodat 'Knippen' / 'Kleur' visueel scheiden zonder
    aparte tabellen)."""
    out: list[dict] = []
    for p in prices:
        label = (p.get("label") or "").strip()
        amount = (p.get("amount") or "").strip()
        category = (p.get("category") or "").strip()
        if not label or not amount:
            continue
        desc = category if category and category.lower() != "overig" else ""
        if p.get("duration_min"):
            desc = (desc + f" · {p['duration_min']} min").strip(" ·")
        out.append({
            "name": _truncate(label, 80),
            "price": amount,
            "description": desc,
        })
    return out


def _build_services_items(treatments: list[dict], images: list[dict]) -> list[dict]:
    """Map inventory.treatments naar services/cards-N items=[{title,description,icon}].

    Description blijft kort: een echte description ontbreekt in inventory; de
    template toont em uitsluitend als ingevuld. Geen verzonnen copy hier."""
    icon_pool = ["Sparkles", "Scissors", "Brush", "Heart", "Sun", "Star", "Leaf", "Gem"]
    out: list[dict] = []
    for i, t in enumerate(treatments[:6]):
        name = (t.get("name") or "").strip()
        if not name:
            continue
        out.append({
            "title": _truncate(name, 60),
            "description": "",
            "icon": icon_pool[i % len(icon_pool)],
        })
    return out


def _build_testimonials_items(reviews: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in reviews[:6]:
        text = (r.get("quote") or "").strip()
        attribution = (r.get("attribution") or "").strip()
        if not text:
            continue
        out.append({
            "text": _truncate(text, 240),
            "name": attribution or "Tevreden klant",
        })
    return out


def _strip_phone(phone: str) -> str:
    """Voor href=tel: — alleen digits + leading + (één)."""
    if not phone:
        return ""
    digits = re.sub(r"[^\d+]", "", phone)
    return digits


def inject_inventory_facts(plan: dict, inventory: dict, *, is_homepage: bool, page_slug: str) -> dict:
    """Walk plan.sections en overschrijf feit-secties met inventory-data.

    Retourneert het aangepaste plan. Verwijdert secties waarvan de bron
    leeg is (geen prijzen → geen pricing-sectie, geen reviews → geen
    testimonials)."""
    if not inventory:
        return plan

    sections = plan.get("sections") or []
    new_sections: list[dict] = []

    prices = inventory.get("prices") or []
    treatments = inventory.get("treatments") or []
    reviews = inventory.get("reviews") or []
    contact = inventory.get("contact") or {}
    hours = inventory.get("opening_hours") or []
    booking_methods = contact.get("booking_methods") or []
    whatsapp_number = ""
    if "whatsapp" in booking_methods and contact.get("phone"):
        whatsapp_number = _strip_phone(contact["phone"]).lstrip("+")

    pricing_built = _build_pricing_plans(prices)
    services_built = _build_services_items(treatments, inventory.get("images") or [])
    testimonials_built = _build_testimonials_items(reviews)

    for section in sections:
        stype = (section.get("type") or "").strip()
        content = dict(section.get("content") or {})

        if stype == "pricing":
            if not pricing_built:
                # Verwijder lege pricing-sectie volledig — geen "AANNEMELIJK" meer
                continue
            if not content.get("headline"):
                content["headline"] = "Tarieven"
            content["plans"] = pricing_built
            section["content"] = content
            new_sections.append(section)
            continue

        if stype == "services":
            if services_built:
                if not content.get("headline"):
                    content["headline"] = "Behandelingen"
                content["items"] = services_built
                section["content"] = content
            new_sections.append(section)
            continue

        if stype == "testimonials":
            if not testimonials_built:
                # Verwijder sectie als er geen echte reviews zijn
                continue
            if not content.get("headline"):
                content["headline"] = "Wat klanten zeggen"
            content["items"] = testimonials_built
            section["content"] = content
            new_sections.append(section)
            continue

        if stype == "contact":
            if contact.get("phone"):
                content["phone"] = contact.get("phone_display") or contact["phone"]
            if contact.get("email"):
                content["email"] = contact["email"]
            if contact.get("address"):
                content["address"] = contact["address"]
            if not content.get("headline"):
                content["headline"] = "Contact"
            section["content"] = content
            new_sections.append(section)
            continue

        if stype == "cta":
            # CTA krijgt phone/whatsapp uit inventory zodat de "klik om te bellen"-
            # buttons verbatim het juiste nummer hebben.
            if contact.get("phone") and not content.get("phone"):
                content["phone"] = contact.get("phone_display") or contact["phone"]
            if whatsapp_number and not content.get("whatsapp"):
                content["whatsapp"] = whatsapp_number
            section["content"] = content
            new_sections.append(section)
            continue

        if stype == "hero":
            # Vul image als die ontbreekt en we hebben een hero/portrait-rol image
            if not content.get("image"):
                for img in inventory.get("images") or []:
                    if img.get("role_hint") in ("hero", "portrait"):
                        content["image"] = img.get("path", "")
                        break
            # Als subline leeg is en we hebben een tagline, gebruik die
            sc = inventory.get("source_copy") or {}
            if not content.get("subline") and sc.get("tagline"):
                content["subline"] = sc["tagline"]
            section["content"] = content
            new_sections.append(section)
            continue

        # Andere sectie-types onveranderd doorlaten
        new_sections.append(section)

    # Voor pages met expliciete page_slug-match in pages_content: vul about-body
    pages_content = inventory.get("pages_content") or {}
    page_body = pages_content.get(page_slug) if page_slug else None
    if page_body and not is_homepage:
        for section in new_sections:
            if section.get("type") == "about":
                content = dict(section.get("content") or {})
                if page_body.get("lead"):
                    content["body"] = page_body["lead"]
                    if page_body.get("body"):
                        content["body"] += " " + page_body["body"]
                if page_body.get("bullets"):
                    content["bullets"] = page_body["bullets"]
                section["content"] = content
                break

    plan["sections"] = new_sections
    return plan


def warn_about_remaining_aannemelijk(plan: dict, page_slug: str) -> None:
    """Print een waarschuwing als er nog AANNEMELIJK-tokens in het plan zitten."""
    raw = json.dumps(plan, ensure_ascii=False)
    n = raw.count("AANNEMELIJK")
    if n > 0:
        print(f"[WARN] {n}× 'AANNEMELIJK' nog aanwezig in {page_slug}-plan na injectie")


# ── CLI ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--brief",       required=True)
    parser.add_argument("--page-slug",   required=True)
    parser.add_argument("--page-title",  default="")
    parser.add_argument("--page-desc",   default="")
    parser.add_argument("--out",         required=True, help="JSON output pad")
    parser.add_argument("--image-manifest", default="")
    parser.add_argument("--nav-pages",   default="")
    parser.add_argument("--homepage",    action="store_true")
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY ontbreekt")

    brief_path = Path(args.brief)
    briefing = brief_path.read_text(encoding="utf-8", errors="ignore")
    inventory = load_inventory(brief_path)

    images = []
    if args.image_manifest and Path(args.image_manifest).exists():
        try:
            images = json.loads(Path(args.image_manifest).read_text())
        except Exception:
            pass

    nav_pages = []
    if args.nav_pages:
        try:
            nav_pages = json.loads(args.nav_pages)
        except Exception:
            pass

    client = get_claude_client(api_key)
    model  = get_model()

    # Sitemap URL opslaan (homepage)
    if args.homepage:
        url_match = re.search(r'https?://[^\s\'"]+', briefing[:500])
        sitemap_url = ""
        if url_match:
            sitemap_url = url_match.group(0).rstrip('/')
        elif inventory.get("source_url"):
            sitemap_url = inventory["source_url"].rstrip('/')
        if sitemap_url:
            (brief_path.parent / "site_url.txt").write_text(sitemap_url, encoding="utf-8")

    # Theme bepalen
    theme = ""
    theme_file = brief_path.parent / "theme.json"
    if args.homepage:
        if theme_file.exists():
            theme = json.loads(theme_file.read_text()).get("theme", "")
        if not theme:
            print(f"[INFO] DaisyUI theme kiezen...")
            theme = pick_theme(client, model, briefing)
            theme_file.write_text(json.dumps({"theme": theme}), encoding="utf-8")
            print(f"[OK]  Theme gekozen: {theme}")
    elif theme_file.exists():
        theme = json.loads(theme_file.read_text()).get("theme", "light")

    have_inv = bool(inventory)
    print(f"[INFO] Sectieplan voor: {args.page_slug} ({'homepage' if args.homepage else 'sub'}) | "
          f"theme={theme} | inventory={'ja' if have_inv else 'nee'}")
    if have_inv:
        print(f"[INFO] inventory: prijzen={len(inventory.get('prices') or [])} "
              f"treatments={len(inventory.get('treatments') or [])} "
              f"reviews={len(inventory.get('reviews') or [])} "
              f"sigs={len(inventory.get('signatures') or [])}")

    plan = plan_page(
        client, model, briefing,
        page_slug=args.page_slug,
        page_title=args.page_title,
        page_desc=args.page_desc,
        images=images,
        nav_pages=nav_pages,
        is_homepage=args.homepage,
        theme=theme,
        inventory=inventory,
    )

    plan = inject_inventory_facts(
        plan, inventory,
        is_homepage=args.homepage,
        page_slug=args.page_slug,
    )
    warn_about_remaining_aannemelijk(plan, args.page_slug)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK]  Sectieplan opgeslagen: {out_path} ({len(plan.get('sections', []))} secties)")


if __name__ == "__main__":
    main()
