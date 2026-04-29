"""
polish_site.py — Laatste kwaliteitsslag over een gegenereerde site.

Stappen:
1. Dedupliceer pagina's met bijna-gelijke bestandsnamen (spackspuiten vs spack-spuiten)
2. Herstel header-inconsistentie: vervang headers van subpagina's die te veel afwijken
   van de homepage-header, met de opgeslagen referentie-header.
3. Normaliseer footer: vervang footer van alle subpagina's door die van index.html
4. Voeg Open Graph meta-tags toe (og:title, og:description, og:type)
5. Voeg <link rel="canonical"> toe aan alle pagina's
6. Injecteer demo-banner bovenaan elke pagina
"""
import argparse
import os
import re
import sys
from pathlib import Path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize_name(stem: str) -> str:
    """Verwijder koppeltekens, underscores en maak lowercase voor vergelijking."""
    return re.sub(r"[-_\s]+", "", stem.lower())


def _extract_classes(html: str) -> set[str]:
    """Haal alle CSS-klassen op uit een stuk HTML."""
    classes: set[str] = set()
    for m in re.finditer(r'\bclass=["\']([^"\']+)["\']', html):
        classes.update(m.group(1).split())
    return classes


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    u = a | b
    return len(a & b) / len(u)


def _extract_block(html: str, tag: str) -> str:
    """Haal het eerste volledige <tag>…</tag> blok op (geen nested-aware, maar robuust genoeg)."""
    open_re  = re.compile(rf'<{tag}\b[^>]*>', re.IGNORECASE)
    close_re = re.compile(rf'</{tag}>', re.IGNORECASE)
    m_open = open_re.search(html)
    if not m_open:
        return ""
    m_close = close_re.search(html, m_open.end())
    if not m_close:
        return ""
    return html[m_open.start(): m_close.end()]


# ── Stap 1: dedupliceer pagina's ─────────────────────────────────────────────

def deduplicate_pages(site_dir: Path) -> list[str]:
    """
    Vind bestanden met bijna-gelijke namen (na normalisatie) en verwijder de duplicaten.
    Behoudt de naam die het vaakst gelinkt wordt in andere HTML-bestanden, of anders
    de kortere kebab-case naam.
    """
    # rglob voor Next.js (pagina's in subdirs: over-ons/index.html)
    html_files = [
        f for f in site_dir.rglob("*.html")
        if not f.name.startswith("_") and "_next" not in str(f)
    ]

    # Groepeer op genormaliseerde naam — voor Next.js exports (about/index.html)
    # nemen we de parent-dir mee zodat over-ons/index.html en about/index.html
    # NIET als duplicaten gezien worden.
    groups: dict[str, list[Path]] = {}
    for f in html_files:
        if f.name == "index.html" and f.parent != site_dir:
            key = _normalize_name(f.parent.name)
        else:
            key = _normalize_name(f.stem)
        groups.setdefault(key, []).append(f)

    fixes: list[str] = []

    for norm_key, members in groups.items():
        if len(members) < 2:
            continue

        # Tel hoe vaak elke naam gelinkt wordt in andere pagina's
        all_html = [f for f in html_files if f not in members]
        link_counts: dict[str, int] = {f.name: 0 for f in members}
        for page in all_html:
            try:
                content = page.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for member in members:
                if member.name in content:
                    link_counts[member.name] += content.count(member.name)

        # Canoniek bestand: meest gelinkt, of bij gelijkstand kortste naam
        canonical = max(members, key=lambda f: (link_counts[f.name], -len(f.name)))
        duplicates = [f for f in members if f != canonical]

        # Verwijder duplicaten en repareer links in alle HTML-bestanden
        all_html_including = [f for f in site_dir.glob("*.html") if not f.name.startswith("_")]
        for dup in duplicates:
            # Vervang links naar dup door links naar canonical in alle pagina's
            replaced_in = 0
            for page in all_html_including:
                if page == dup:
                    continue
                try:
                    content = page.read_text(encoding="utf-8", errors="ignore")
                    if dup.name in content:
                        new_content = content.replace(dup.name, canonical.name)
                        page.write_text(new_content, encoding="utf-8")
                        replaced_in += 1
                except Exception:
                    pass

            try:
                dup.unlink()
                fixes.append(
                    f"Duplicaat verwijderd: {dup.name} → {canonical.name} "
                    f"(links hersteld in {replaced_in} bestand(en))"
                )
            except Exception as e:
                fixes.append(f"[WARN] Kon {dup.name} niet verwijderen: {e}")

    return fixes


# ── Stap 2: herstel header-inconsistentie ────────────────────────────────────

def fix_header_consistency(site_dir: Path, threshold: float = 0.45) -> list[str]:
    """
    Vergelijk de <header> van elke subpagina met die van index.html via Jaccard-similariteit
    op CSS-klassen. Pagina's onder de drempel krijgen de referentie-header terug.

    De referentieheader staat in _header_footer_ref.html (gegenereerd door run_pipeline.py).
    Na vervanging wordt ook de <footer> vervangen om consistentie te garanderen.
    """
    ref_path    = site_dir / "_header_footer_ref.html"
    index_path  = site_dir / "index.html"

    if not ref_path.exists() or not index_path.exists():
        return []

    ref_html   = ref_path.read_text(encoding="utf-8", errors="ignore")
    index_html = index_path.read_text(encoding="utf-8", errors="ignore")

    ref_header  = _extract_block(ref_html, "header")
    ref_footer  = _extract_block(ref_html, "footer")
    index_header = _extract_block(index_html, "header")

    if not ref_header:
        return []

    ref_classes   = _extract_classes(ref_header)
    index_classes = _extract_classes(index_header)
    baseline      = _jaccard(ref_classes, index_classes)

    fixes: list[str] = []

    for page in sorted(site_dir.glob("*.html")):
        if page.name.startswith("_") or page.name == "index.html":
            continue
        try:
            content = page.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        page_header = _extract_block(content, "header")
        if not page_header:
            continue

        page_classes = _extract_classes(page_header)
        similarity   = _jaccard(ref_classes, page_classes)

        if similarity >= threshold:
            continue

        # Vervang header
        new_content = content
        if page_header:
            new_content = new_content.replace(page_header, ref_header, 1)

        # Vervang footer als ref_footer beschikbaar is
        if ref_footer:
            page_footer = _extract_block(new_content, "footer")
            if page_footer and page_footer != ref_footer:
                page_footer_classes = _extract_classes(page_footer)
                ref_footer_classes  = _extract_classes(ref_footer)
                footer_sim = _jaccard(ref_footer_classes, page_footer_classes)
                if footer_sim < threshold:
                    new_content = new_content.replace(page_footer, ref_footer, 1)

        if new_content != content:
            page.write_text(new_content, encoding="utf-8")
            fixes.append(
                f"Header/footer hersteld: {page.name} "
                f"(similariteit was {similarity:.2f}, drempel {threshold:.2f})"
            )

    return fixes


# ── Stap 3: normaliseer footer ───────────────────────────────────────────────

def normalize_footers(site_dir: Path) -> list[str]:
    """
    Vervang de footer van alle subpagina's door die van index.html.

    De homepage-footer is de enige die consistent goed gegenereerd wordt —
    hij is de referentie voor het ontwerp. Subpagina's worden parallel
    gegenereerd en wijken regelmatig af in class names of structuur.
    """
    index_path = site_dir / "index.html"
    if not index_path.exists():
        return []

    ref_footer = _extract_block(
        index_path.read_text(encoding="utf-8", errors="ignore"), "footer"
    )
    if not ref_footer:
        return []

    fixes: list[str] = []
    for page in sorted(site_dir.glob("*.html")):
        if page.name.startswith("_") or page.name == "index.html":
            continue
        content = page.read_text(encoding="utf-8", errors="ignore")
        page_footer = _extract_block(content, "footer")
        if not page_footer or page_footer == ref_footer:
            continue
        new_content = content.replace(page_footer, ref_footer, 1)
        if new_content != content:
            page.write_text(new_content, encoding="utf-8")
            fixes.append(f"Footer genormaliseerd: {page.name}")
    return fixes


# ── Stap 4: Open Graph meta-tags ─────────────────────────────────────────────

def add_og_meta(site_dir: Path) -> list[str]:
    """
    Voeg Open Graph meta-tags toe aan pagina's die ze nog niet hebben.
    Tags: og:type, og:title, og:description
    """
    fixes: list[str] = []
    TITLE_RE   = re.compile(r"<title>([^<]+)</title>", re.IGNORECASE)
    DESC_RE    = re.compile(
        r'<meta\s+(?:name=["\']description["\']\s+content=["\']([^"\']+)["\']'
        r'|content=["\']([^"\']+)["\']\s+name=["\']description["\'])',
        re.IGNORECASE,
    )

    for html_path in sorted(site_dir.glob("*.html")):
        if html_path.name.startswith("_"):
            continue
        content = html_path.read_text(encoding="utf-8", errors="ignore")
        if 'og:title' in content:
            continue

        title_m = TITLE_RE.search(content)
        title   = title_m.group(1).strip() if title_m else ""
        if not title:
            continue

        desc_m  = DESC_RE.search(content)
        if desc_m:
            description = (desc_m.group(1) or desc_m.group(2) or "").strip()
        else:
            description = title

        # Escape quotes in attribute values
        safe_title = title.replace('"', "&quot;")
        safe_desc  = description.replace('"', "&quot;")

        og_block = (
            f'  <meta property="og:type" content="website">\n'
            f'  <meta property="og:title" content="{safe_title}">\n'
            f'  <meta property="og:description" content="{safe_desc}">\n'
        )

        new_content = re.sub(
            r"(</head>)", og_block + r"\1", content, flags=re.IGNORECASE, count=1
        )
        if new_content != content:
            html_path.write_text(new_content, encoding="utf-8")
            fixes.append(f"Open Graph meta toegevoegd aan {html_path.name}")

    return fixes


# ── Stap 4: Canonical links ───────────────────────────────────────────────────

def add_canonical_links(site_dir: Path) -> list[str]:
    """
    Voeg <link rel="canonical" href="PAGINA.html"> toe aan pagina's zonder canonical.
    Gebruikt relatieve URL zodat het werkt ongeacht het domein.
    """
    fixes: list[str] = []

    for html_path in sorted(site_dir.glob("*.html")):
        if html_path.name.startswith("_"):
            continue
        content = html_path.read_text(encoding="utf-8", errors="ignore")
        if 'rel="canonical"' in content:
            continue

        link_tag = f'  <link rel="canonical" href="{html_path.name}">'
        new_content = re.sub(
            r"(</head>)", link_tag + "\n" + r"\1", content, flags=re.IGNORECASE, count=1
        )
        if new_content != content:
            html_path.write_text(new_content, encoding="utf-8")
            fixes.append(f"canonical link toegevoegd aan {html_path.name}")

    return fixes


# ── Stap 5: Demo-banner ───────────────────────────────────────────────────────

DEMO_BANNER_ID = "site-factory-demo-banner"

def inject_demo_banner(site_dir: Path, company_name: str) -> list[str]:
    """
    Injecteer een vaste demo-banner bovenaan elke pagina.
    De banner is dismissable via een sluitknop en stored in sessionStorage.
    """
    agency_name  = os.environ.get("AGENCY_NAME",  "")
    agency_email = os.environ.get("AGENCY_EMAIL", "")
    agency_phone = os.environ.get("AGENCY_PHONE", "")

    email_line = f'<a href="mailto:{agency_email}" style="color:#6366f1;text-decoration:none;font-weight:500">{agency_email}</a>' if agency_email else ""
    phone_line = f'<a href="tel:{agency_phone}" style="color:#6366f1;text-decoration:none;font-weight:500">{agency_phone}</a>' if agency_phone else ""
    contact_lines = "<br>".join(filter(None, [email_line, phone_line]))

    banner_html = f"""  <div id="{DEMO_BANNER_ID}" style="position:fixed;bottom:20px;right:20px;z-index:99999;background:#fff;color:#1e293b;font-family:sans-serif;font-size:13px;line-height:1.5;padding:14px 16px 12px;border-radius:10px;box-shadow:0 4px 20px rgba(0,0,0,.15);max-width:220px;border:1px solid #e2e8f0">
    <button onclick="document.getElementById('{DEMO_BANNER_ID}').style.display='none';sessionStorage.setItem('{DEMO_BANNER_ID}','1')" style="position:absolute;top:8px;right:10px;background:none;border:none;color:#94a3b8;cursor:pointer;font-size:15px;line-height:1;padding:0">×</button>
    <div style="font-size:11px;color:#94a3b8;margin-bottom:4px;text-transform:uppercase;letter-spacing:.05em">Demo</div>
    <div style="font-weight:600;margin-bottom:8px;padding-right:16px">{company_name}</div>
    <div style="font-size:12px;color:#475569">Interesse? Neem contact op:<br>{contact_lines}</div>
  </div>
  <script>if(sessionStorage.getItem('{DEMO_BANNER_ID}'))document.getElementById('{DEMO_BANNER_ID}').style.display='none';</script>"""

    fixes: list[str] = []
    for html_path in sorted(site_dir.glob("*.html")):
        if html_path.name.startswith("_"):
            continue
        content = html_path.read_text(encoding="utf-8", errors="ignore")
        if DEMO_BANNER_ID in content:
            continue
        new_content = re.sub(
            r"(<body[^>]*>)",
            r"\1\n" + banner_html,
            content, flags=re.IGNORECASE, count=1,
        )
        if new_content != content:
            html_path.write_text(new_content, encoding="utf-8")
            fixes.append(f"Demo-banner toegevoegd aan {html_path.name}")
    return fixes


# ── Schema.org LocalBusiness ─────────────────────────────────────────────────

def inject_schema_org(site_dir: Path, collected_path: Path | None) -> list[str]:
    """
    Injecteer Schema.org LocalBusiness JSON-LD in index.html.
    Data komt uit structured_data.json en de prospect-URL.
    """
    if not collected_path:
        return []
    sd_path = Path(collected_path) / "structured_data.json"
    if not sd_path.exists():
        return []

    try:
        import json as _json
        sd = _json.loads(sd_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    contact = sd.get("contact", {})
    name_hint = sd.get("meta_tags", {}).get("og_title", "")

    # Bouw minimale LocalBusiness markup
    schema: dict = {"@context": "https://schema.org", "@type": "LocalBusiness"}
    if name_hint:
        schema["name"] = name_hint
    if contact.get("phone"):
        schema["telephone"] = contact["phone"]
    if contact.get("email"):
        schema["email"] = contact["email"]
    if contact.get("address"):
        schema["address"] = {"@type": "PostalAddress", "streetAddress": contact["address"]}

    # Voeg openingstijden toe als die in JSON-LD blokken staan
    for block in sd.get("json_ld", []):
        if block.get("openingHoursSpecification") or block.get("openingHours"):
            schema.update({k: v for k, v in block.items()
                           if k in ("openingHoursSpecification", "openingHours")})
            break

    if len(schema) < 3:
        return []  # Te weinig data, niet injecteren

    import json as _json2
    ld_block = f'<script type="application/ld+json">{_json2.dumps(schema, ensure_ascii=False)}</script>'

    index = site_dir / "index.html"
    if not index.exists():
        return []

    content = index.read_text(encoding="utf-8", errors="ignore")
    if "LocalBusiness" in content:
        return []

    new = re.sub(r"(</head>)", ld_block + r"\n\1", content, flags=re.IGNORECASE, count=1)
    if new != content:
        index.write_text(new, encoding="utf-8")
        return ["Schema.org LocalBusiness JSON-LD toegevoegd aan index.html"]
    return []


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Kwaliteitsslag over een gegenereerde site")
    parser.add_argument("--site-dir",        required=True)
    parser.add_argument("--company",         default="")
    parser.add_argument("--collected-path",  default="", help="Pad naar collected data voor schema.org")
    parser.add_argument("--threshold",       type=float, default=0.45,
                        help="Jaccard-drempel voor header-consistentie (default: 0.45)")
    args = parser.parse_args()

    site_dir = Path(args.site_dir)
    if not site_dir.exists():
        print(f"[FAIL] Site-map niet gevonden: {site_dir}")
        sys.exit(1)

    total_fixes = 0

    print(f"[INFO] Stap 1/4: Dubbele pagina's zoeken...")
    dedup_fixes = deduplicate_pages(site_dir)
    for fix in dedup_fixes:
        print(f"[OK]  {fix}")
    if not dedup_fixes:
        print(f"[INFO] Geen duplicaten gevonden")
    total_fixes += len(dedup_fixes)

    print(f"\n[INFO] Stap 2/5: Header-consistentie controleren (drempel={args.threshold})...")
    header_fixes = fix_header_consistency(site_dir, threshold=args.threshold)
    for fix in header_fixes:
        print(f"[OK]  {fix}")
    if not header_fixes:
        print(f"[INFO] Alle headers zijn consistent")
    total_fixes += len(header_fixes)

    print(f"\n[INFO] Stap 3/5: Footer normaliseren (index.html als referentie)...")
    footer_fixes = normalize_footers(site_dir)
    for fix in footer_fixes:
        print(f"[OK]  {fix}")
    if not footer_fixes:
        print(f"[INFO] Alle footers zijn al gelijk aan homepage")
    total_fixes += len(footer_fixes)

    print(f"\n[INFO] Stap 4/5: Open Graph meta-tags toevoegen...")
    og_fixes = add_og_meta(site_dir)
    for fix in og_fixes:
        print(f"[OK]  {fix}")
    if not og_fixes:
        print(f"[INFO] Alle pagina's hebben al OG-meta")
    total_fixes += len(og_fixes)

    print(f"\n[INFO] Stap 5/5: Canonical links toevoegen...")
    canonical_fixes = add_canonical_links(site_dir)
    for fix in canonical_fixes:
        print(f"[OK]  {fix}")
    if not canonical_fixes:
        print(f"[INFO] Alle pagina's hebben al een canonical link")
    total_fixes += len(canonical_fixes)

    print(f"\n[INFO] Stap 6/6: Demo-banner injecteren...")
    if args.company:
        banner_fixes = inject_demo_banner(site_dir, args.company)
        for fix in banner_fixes:
            print(f"[OK]  {fix}")
        if not banner_fixes:
            print(f"[INFO] Demo-banner al aanwezig")
        total_fixes += len(banner_fixes)
    else:
        print(f"[INFO] --company niet opgegeven, demo-banner overgeslagen")

    # Schema.org LocalBusiness
    collected_path = Path(args.collected_path) if args.collected_path else None
    schema_fixes = inject_schema_org(site_dir, collected_path)
    for fix in schema_fixes:
        print(f"[OK]  {fix}")
    total_fixes += len(schema_fixes)

    print(f"\n[OK]  Polish klaar: {total_fixes} fix(es) toegepast")


if __name__ == "__main__":
    main()
