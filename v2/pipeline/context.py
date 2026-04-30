from __future__ import annotations

import json
import re
from pathlib import Path

from .models import ContactInfo, Page, Section, SitePlan, Theme


HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b")
EMAIL_RE = re.compile(r"[\w.\-+]+@[\w.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+31|0031|0)\s?[1-9][0-9\s().-]{7,}")


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return default


def first_match(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(0).strip() if match else ""


def extract_theme(briefing: str) -> Theme:
    colors = HEX_RE.findall(briefing)
    return Theme(
        primary=colors[0] if len(colors) > 0 else "#2563eb",
        secondary=colors[1] if len(colors) > 1 else "#0f172a",
        accent=colors[2] if len(colors) > 2 else "#f59e0b",
    )


def extract_pages(collected_path: Path) -> list[Page]:
    pages_json = read_json(collected_path / "pages.json", {})
    raw_pages = pages_json.get("pages") or []
    pages = [Page(slug="", title="Home", description="Hoofdpagina")]
    for item in raw_pages:
        file_name = str(item.get("file", ""))
        slug = file_name.replace(".html", "").strip("/")
        if slug in {"", "index", "home"}:
            continue
        pages.append(Page(
            slug=slug,
            title=item.get("title") or slug.replace("-", " ").title(),
            description=item.get("description", ""),
        ))
    if len(pages) == 1:
        pages.extend([
            Page(slug="over-ons", title="Over ons"),
            Page(slug="diensten", title="Diensten"),
            Page(slug="contact", title="Contact"),
        ])
    return pages[:8]


def build_site_plan(collected_path: Path, slug: str) -> SitePlan:
    briefing = (collected_path / "briefing.md").read_text(encoding="utf-8", errors="ignore")
    meta = read_json(collected_path / "meta.json", {})
    structured = read_json(collected_path / "structured_data.json", {})

    company_name = meta.get("company_name") or slug.replace("-", " ").title()
    contact = structured.get("contact") if isinstance(structured.get("contact"), dict) else {}

    contact_info = ContactInfo(
        phone=contact.get("phone") or first_match(PHONE_RE, briefing),
        email=contact.get("email") or first_match(EMAIL_RE, briefing),
        address=contact.get("address", ""),
        website=meta.get("url") or structured.get("url", ""),
    )

    pages = extract_pages(collected_path)
    service_lines = [
        line.strip("- ").strip()
        for line in briefing.splitlines()
        if line.strip().startswith("- ") and 8 <= len(line.strip()) <= 90
    ][:6]

    sections = [
        Section("hero", "split", {
            "eyebrow": "Nieuwe website preview",
            "headline": company_name,
            "body": "Een snelle Astro-prototype op basis van de bestaande Site Factory briefing.",
            "primaryCta": "Neem contact op",
            "secondaryCta": "Bekijk diensten",
        }),
        Section("services", "cards", {
            "title": "Diensten",
            "items": service_lines or ["Advies", "Uitvoering", "Service"],
        }),
        Section("about", "text", {
            "title": f"Over {company_name}",
            "body": briefing[:900].replace("\n", " ").strip(),
        }),
        Section("contact", "compact", {
            "title": "Contact",
        }),
    ]

    return SitePlan(
        slug=slug,
        company_name=company_name,
        source_url=meta.get("url", ""),
        contact=contact_info,
        theme=extract_theme(briefing),
        pages=pages,
        sections=sections,
    )
