"""Local service archetype: B2B/B2C lokale vakmensen.

Stucadoors, schilders, hoveniers, loodgieters, dakdekkers, garages,
installateurs, aannemers. Geen tarievenpagina (offertes op aanvraag),
geen openingstijden (op afspraak), wel rijke project-galerij en
vertrouwens-signalen.

V0-aanpak: hergebruikt de beauty_wellness Astro-templates omdat dezelfde
componenten (Hero, Services, Gallery, About, Reviews, ContactCta) er
prima passen — alleen de data-shape en de copy verandert. Een eigen
template-folder is logische opvolger zodra de B2B-aesthetic genoeg
afwijkt.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from v2.pipeline.inventory import ContentInventory, build_inventory
from v2.pipeline.personality import pick_personality
from v2.pipeline.quality_gate import FieldCheck

from .base import Archetype, ArchetypeMatch
# Hergebruik utilities uit beauty_wellness
from . import beauty_wellness as bw


# ── Detectie keywords ────────────────────────────────────────────────────────
_HEAVY_KEYWORDS = [
    # Bouw / afbouw
    "stucadoor", "stukadoor", "stucwerk", "pleisterwerk", "stuc-",
    "schilder", "schildersbedrijf",
    "bouwbedrijf", "aannemer", "aannemingsbedrijf",
    "timmerman", "timmerbedrijf", "timmerwerk",
    "tegelzetter", "voegwerk", "voeger",
    # Installatie
    "loodgieter", "loodgieterswerk", "sanitair",
    "elektricien", "installateur", "installatiebedrijf",
    "cv-monteur", "cv monteur",
    "dakdekker", "dakwerken",
    # Tuin
    "hovenier", "hoveniersbedrijf", "tuinaanleg", "tuinontwerp",
    # Auto / overige
    "garage", "garagebedrijf", "schadeherstel", "autobedrijf",
    "klusbedrijf", "klussenbedrijf", "renovatiebedrijf",
]
_LIGHT_KEYWORDS = [
    "offerte", "vrijblijvend", "vakman", "ervaren", "jaar ervaring",
    "particulieren", "zakelijk", "renovatie", "verbouwing", "onderhoud",
    "advies op maat", "kennismakingsgesprek", "vakmanschap",
]


class LocalServiceArchetype:
    name = "local_service"
    # V0: deelt templates met beauty_wellness. Bij eigen template-fork:
    # zet hier "local_service" en kopieer + pas aan.
    template_dir_name = "beauty_wellness"

    def get_contract(self) -> list[FieldCheck]:
        # B2B-contract: prijzen niet vereist (offerte op aanvraag), telefoon
        # essentieel, foto's belangrijk voor vertrouwens-signaal.
        return [
            FieldCheck(
                field="contact.phone",
                rule="required",
                severity="fail",
                rationale="B2B service zonder bel-CTA verliest direct offertes",
            ),
            FieldCheck(
                field="contact.address",
                rule="recommended",
                severity="warn",
                rationale="Lokale vindbaarheid voor 'X in <plaats>'-zoekopdrachten",
            ),
            FieldCheck(
                field="images",
                rule="min_count",
                severity="warn",
                min_count=4,
                rationale="Project-foto's zijn voor B2B het belangrijkste vertrouwenssignaal",
            ),
            FieldCheck(
                field="pages",
                rule="min_count",
                severity="warn",
                min_count=3,
                rationale="Minder dan 3 pagina's wijst op onvolledige crawl",
            ),
        ]

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
        haystack_parts.append((meta.get("title") or "").lower())
        haystack_parts.append((meta.get("company_name") or "").lower())
        haystack = "\n".join(haystack_parts)

        score = 0.0
        reasons: list[str] = []
        for keyword in _HEAVY_KEYWORDS:
            if keyword in haystack:
                # 1.5 per heavy match — wint van beauty_wellness's 0.4 light
                # match op woorden als 'behandeling' (overlap stucadoor →
                # 'wandbehandeling').
                score += 1.5
                reasons.append(f"heavy: {keyword}")
        for keyword in _LIGHT_KEYWORDS:
            if keyword in haystack:
                score += 0.5
                reasons.append(f"light: {keyword}")
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
        if inventory is None:
            inventory = build_inventory(slug, collected_path)

        company_name = (
            meta.get("company_name")
            or structured.get("title")
            or meta.get("title")
            or slug.replace("-", " ").title()
        )
        company_name = re.sub(r"\s+[Vv]\d+$", "", company_name).strip()

        # Contact uit inventory (al met text.txt-fallback voor missende structured_data)
        inv_contact = inventory.contact
        contact = {
            "phone":         inv_contact.phone,
            "phone_display": inv_contact.phone_display,
            "email":         inv_contact.email,
            "address":       inv_contact.address,
        }
        social_raw = structured.get("social_links", {}) if isinstance(structured.get("social_links"), dict) else {}
        whatsapp_url = ""
        if contact["phone"]:
            digits = re.sub(r"\D", "", contact["phone"])
            if digits:
                whatsapp_url = f"https://wa.me/{digits}"
        social = {
            "facebook":  social_raw.get("facebook", ""),
            "instagram": social_raw.get("instagram", ""),
            "linkedin":  social_raw.get("linkedin", ""),
            "whatsapp":  whatsapp_url,
        }

        # Hero copy: source_copy.tagline wint, dan signature, dan briefing-bullet
        sections = bw._split_sections(briefing)
        sc = inventory.source_copy
        signature = ""
        if sc.tagline and len(sc.tagline) >= 30:
            signature = bw._clean_dashes(sc.tagline)
        elif inventory.signatures:
            signature = inventory.signatures[0].quote
        if not signature:
            project_lines = bw._bullet_lines(sections.get("project", ""))
            if project_lines:
                signature = bw._clean_dashes(project_lines[0])
        # Originele CTA-label uit raw.html
        original_cta = sc.primary_cta if sc.primary_cta and len(sc.primary_cta) >= 4 else ""

        location = ""
        for line in bw._bullet_lines(sections.get("project", "")):
            if re.search(r"\d{4}\s?[A-Z]{2}", line):
                parts = line.split(",")
                if len(parts) >= 2:
                    location = parts[-1].strip().split(" ")[-1]
                break

        # Visual DNA (gedeeld met beauty)
        theme = bw._theme_from_visual_dna(inventory) or {
            "primary": "#4A6FA5",       # B2B-default: rustig blauwgrijs
            "primary_dark": "#3B5A85",
            "secondary": "#1E2A3A",
            "accent": "#E8EEF6",
            "background": "#F7F9FC",
            "surface": "#FFFFFF",
            "text": "#1E2A3A",
            "outline": "#D5DDE8",
        }

        # Layout-heuristieken: B2B varianten per signature-type
        sig_type = bw._signature_type(inventory.signatures)
        # B2B section-volgorde: services vroeg (toon expertise), gallery
        # vervangt het beauty-galerij-concept met project-foto's, GEEN
        # openingHours (op afspraak), GEEN prices (offerte).
        if sig_type == "identity":
            home_sections = ["about", "services", "gallery", "reviews", "contactCta"]
        else:
            home_sections = ["services", "gallery", "about", "reviews", "contactCta"]
        personality = pick_personality(inventory)
        hero_variant = bw._pick_hero_variant(sig_type, has_image=bool(inventory.images),
                                             personality_name=personality.name)
        gallery_variant = bw._pick_gallery_variant(sig_type, n_categories=4)

        # B2B-specifieke voice-copy: vraag-offerte vorm i.p.v. reserveer-vorm
        is_u_form = (inventory.voice_profile.addressing == "u")
        if is_u_form:
            cta_primary    = original_cta or "Vraag offerte aan"
            cta_secondary  = "Bel direct"
            cta_tertiary   = "Stuur een WhatsApp-bericht"
            cta_title      = "Klaar om uw project te bespreken?"
            cta_body       = "Laat ons weten wat u zoekt, dan komen wij vrijblijvend langs voor een offerte op maat."
            hero_secondary = "Bekijk projecten"
        else:
            cta_primary    = original_cta or "Vraag offerte aan"
            cta_secondary  = "Bel direct"
            cta_tertiary   = "App ons via WhatsApp"
            cta_title      = "Klaar om je project te bespreken?"
            cta_body       = "Laat ons weten wat je zoekt, dan komen we vrijblijvend langs voor een offerte op maat."
            hero_secondary = "Bekijk projecten"

        # Services uit pages.json titles — meest betrouwbare bron voor de
        # specialismen van een lokaal vakbedrijf (de subpagina's heten meestal
        # 'Aanleg tuinen', 'Bestrating', 'Schuttingen' enz.). Filter generic
        # nav-pagina's weg.
        skip_titles = re.compile(
            r"^(home|over\s+ons|over\s+mij|contact|garantie|privacy|voorwaarden|disclaimer)$",
            re.IGNORECASE,
        )
        services: list[dict[str, Any]] = []
        for p in inventory.pages:
            title_clean = (p.title or "").strip()
            if not title_clean or skip_titles.match(title_clean):
                continue
            services.append({
                "key": title_clean.lower().replace(" ", "-"),
                "title": title_clean,
                "description": (p.description or "Uitgevoerd met aandacht voor detail en vakkennis.")[:200],
                "href": f"/{title_clean.lower().replace(' ', '-')}/",
            })
            if len(services) >= 5:
                break
        if not services:
            services = [{
                "key": "diensten",
                "title": "Onze specialismen",
                "description": "Vakwerk afgestemd op jouw project, van advies tot afwerking.",
            }]

        # Pages: hergebruik _decorate_pages maar met B2B-aware sections
        pages = bw._decorate_pages(
            bw._extract_pages(collected_path),
            services,
            company_name,
            contact,
            price_groups=[],          # geen prijzen → geen tarieven-sectie
            treatments=[],
            products={},
            home_section_order=home_sections,
            pages_content=inventory.pages_content,
            voice_copy={               # B2B-voice voor sub-page noten
                "behandeling_note": "Vraag advies op maat, dan kijken we samen naar wat past." if not is_u_form else "Vraag advies op maat, dan kijken wij naar wat bij u past.",
                "tarieven_helper":  "Voor exacte prijzen vragen we eerst meer details over jouw project.",
            },
        )

        plan: dict[str, Any] = {
            "archetype": self.name,
            "slug": slug,
            "company_name": company_name,
            "tagline": meta.get("description") or structured.get("meta_tags", {}).get("description", ""),
            "source_url": meta.get("url", ""),
            "logo": "",
            "theme": theme,
            "contact": contact,
            "social": social,
            "pages": pages,
            "headings": {
                "services":     {"eyebrow": "Specialismen",  "title": "Wat we doen"},
                "gallery":      {"eyebrow": "Recent werk",    "title": "Een greep uit onze projecten"},
                "reviews":      {"eyebrow": "Vertrouwen",     "title": "Wat opdrachtgevers ervaren"},
                "openingHours": {"eyebrow": "Bereikbaar",     "title": "Wanneer je ons kunt bereiken"},
                "about":        {"eyebrow": "Het verhaal",    "title": f"Over {company_name}"},
                "stats":        {"eyebrow": "In cijfers",      "title": ""},
                "process":      {"eyebrow": "Werkwijze",       "title": "Zo gaat het bij ons"},
                "featured":     {"eyebrow": "Onze focus",      "title": "Wat we het meest doen"},
                "map":          {"eyebrow": "Werkgebied",      "title": "Vanuit hier werken we"},
                "quoteCallout": {"eyebrow": "In eigen woorden", "title": ""},
            },
            "hero": {
                "eyebrow": "Lokale vakman" + (f", {location}" if location else ""),
                "headline": company_name,
                "body": signature,
                "image": "",
                "variant": hero_variant,
                "primaryCta":   {"label": cta_primary,   "href": "#contact"},
                "secondaryCta": {"label": hero_secondary, "href": "#diensten"},
                "meta": [
                    {"label": "Specialisme", "value": (services[0]["title"][:30] if services else "vakwerk")},
                    {"label": "Locatie",     "value": location or "in de regio"},
                    {"label": "Bereikbaar",  "value": "telefoon of WhatsApp"},
                ],
            },
            "variants": {
                "gallery": gallery_variant,
                "personality": personality.to_dict(),
                "_signature_type": sig_type,
                "_home_section_order": home_sections,
            },
            "typography": inventory.typography.to_dict(),
            "services": services,
            # Geen prices (B2B), geen treatments, geen products
            "prices":     {"title": "", "groups": []},
            "treatments": [],
            "products":   {},
            "gallery": [],
            "reviews": bw._extract_proof(sections, company_name),
            "openingHours": {
                "items": [],
                "fallback": "We werken op afspraak. Bel of WhatsApp voor een vrijblijvende kennismaking.",
            },
            "about": {
                "title":     f"Over {company_name}",
                "body":      (inventory.signatures[1].quote if len(inventory.signatures) >= 2 else signature),
                "signature": signature,
                "image":     "",
            },
            "contactCta": {
                "title": cta_title,
                "body":  cta_body,
                "primary":   {"label": cta_primary,   "href": "#contact"},
                "secondary": {"label": cta_secondary, "href": f"tel:{contact['phone']}" if contact["phone"] else "#"},
                "tertiary":  {"label": cta_tertiary,  "href": whatsapp_url} if whatsapp_url else None,
            },
            "stats": [
                {"value": s.value, "label": s.label}
                for s in inventory.source_copy.stats
            ],
            # B2B doet vrijwel altijd projecten via een vaste werkwijze:
            # offerte → uitvoering → oplevering. Toon process-steps voor
            # vertrouwens-signaal.
            "process": [
                {"title": "Kennismaking",  "body": "We bekijken jouw situatie en luisteren naar wat je wilt."},
                {"title": "Offerte",       "body": "Heldere prijsopgave op maat, vrijblijvend."},
                {"title": "Uitvoering",    "body": "Vakwerk met aandacht voor detail en planning."},
                {"title": "Oplevering",    "body": "Tevredenheid voorop. Bij vragen blijven we bereikbaar."},
            ] if not is_u_form else [
                {"title": "Kennismaking",  "body": "Wij bekijken uw situatie en luisteren naar uw wensen."},
                {"title": "Offerte",       "body": "Heldere prijsopgave op maat, vrijblijvend."},
                {"title": "Uitvoering",    "body": "Vakwerk met aandacht voor detail en planning."},
                {"title": "Oplevering",    "body": "Tevredenheid voorop. Bij vragen blijven wij bereikbaar."},
            ],
            "featured": {},  # B2B doet zelden 'signature service' spotlight
            "quoteCallout": (
                {"quote": inventory.signatures[1].quote, "attribution": company_name}
                if len(inventory.signatures) >= 2 else {}
            ),
        }
        if not plan["contactCta"]["tertiary"]:
            plan["contactCta"].pop("tertiary")
        return plan

    def render(self, plan: dict[str, Any], collected_path: Path, out_dir: Path, force: bool = False) -> None:
        from v2.generators.astro.render_archetype import render_archetype_site

        render_archetype_site(
            template_dir_name=self.template_dir_name,
            plan=plan,
            collected_path=collected_path,
            out_dir=out_dir,
            force=force,
            asset_picker=bw._pick_archetype_assets,
        )
