"""
repair_generated_site.py — Repareer veelvoorkomende fouten in een gegenereerde site.

Per-bestand fixes:
1. Verkeerde CSS-link           → assets/css/style.css
2. Verkeerde JS-link            → assets/js/main.js
3. Gebroken <a href>            → verwijst naar HTML die niet bestaat → #
4. Rauwe file-markers           → ===FILE: / ===END_FILE=== verwijderen

Site-brede fixes (draaien eenmalig):
5.  Interne referentiebestanden verwijderen  (_*.html)
6.  .sr-only CSS-definitie toevoegen         (als ontbreekt)
7.  footerYear JS snippet toevoegen          (als ontbreekt)
8.  Hamburger menu class mismatch herstellen (JS vs CSS alias)
9.  JS-truncatie herstellen                  (afgekapt bestand sluiten)
10. Portfolio filter class mismatch fixen    (.filter-btn → .portfolio-filter-btn alias)
11. Lightbox class + href fixen              (.portfolio-item → .portfolio-item__link + src href)
12. Google Fonts van CSS @import → HTML <link>
13. Favicon toevoegen                        (SVG als geen favicon aanwezig)
14. loading="lazy" toevoegen aan img-tags    (behalve de eerste per pagina)
15. FAQ accordion fixen                      (details/summary + div/button class-mismatch)
16. Laag contrast tekst fixen               (rgba wit/zwart met te lage alpha)
17. Keuze-card contrast op lichte achtergrond (keuze-grid buiten keuzeblokken)
18. Card contrast fixen                      (lichte achtergrond binnen donkere sectie)
19. CSS coverage check                       (classes in HTML zonder CSS-regel → WARN)
"""
import argparse
import json
import re
import sys
from pathlib import Path
from html import escape as html_escape


LINK_HREF_RE   = re.compile(r'<link\b([^>]*)>', re.IGNORECASE)
SCRIPT_SRC_RE  = re.compile(r'(<script\b[^>]*\bsrc=["\'])([^"\']+)(["\'][^>]*>)', re.IGNORECASE)
A_HREF_RE      = re.compile(r'(<a\b[^>]*\bhref=["\'])([^"\']+)(["\'])', re.IGNORECASE)
FILE_MARKER_RE = re.compile(r'===FILE:[^\n]*\n?|===END_FILE===\n?')
IMG_RE         = re.compile(r'<img\b([^>]*)>', re.IGNORECASE)

EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "javascript:", "#", "//")

SR_ONLY_CSS = """.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}"""

FOOTER_YEAR_JS = """
// footerYear invullen
(function () {
  var yr = new Date().getFullYear().toString();
  document.querySelectorAll('#footerYear, [id="footerYear"]').forEach(function (el) {
    el.textContent = yr;
  });
})();
"""


# ── Per-bestand fixes ─────────────────────────────────────────────────────────

def is_external(href: str) -> bool:
    return any(href.startswith(p) for p in EXTERNAL_PREFIXES)


def fix_link_tags(content: str) -> tuple[str, list[str]]:
    fixes = []

    def replace_link(m: re.Match) -> str:
        attrs = m.group(1)
        if 'stylesheet' not in attrs.lower():
            return m.group(0)
        href_m = re.search(r'href=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
        if not href_m:
            return m.group(0)
        href = href_m.group(1)
        # Externe links (Google Fonts etc.) nooit aanraken
        if href.startswith(("http://", "https://", "//", "data:")):
            return m.group(0)
        if href == "assets/css/style.css":
            return m.group(0)
        fixes.append(f"CSS-link {href!r} → assets/css/style.css")
        return '<link rel="stylesheet" href="assets/css/style.css">'

    return LINK_HREF_RE.sub(replace_link, content), fixes


def fix_script_tags(content: str, site_dir: Path, html_path: Path) -> tuple[str, list[str]]:
    fixes = []

    def replace_script(m: re.Match) -> str:
        prefix, src, suffix = m.group(1), m.group(2), m.group(3)
        if is_external(src):
            return m.group(0)
        resolved = (html_path.parent / src).resolve()
        try:
            resolved.relative_to(site_dir.resolve())
        except ValueError:
            return m.group(0)
        if resolved.exists():
            return m.group(0)
        fixes.append(f"JS-src {src!r} → assets/js/main.js")
        return f'{prefix}assets/js/main.js{suffix}'

    return SCRIPT_SRC_RE.sub(replace_script, content), fixes


def fix_broken_links(content: str, site_dir: Path, html_path: Path) -> tuple[str, list[str]]:
    fixes = []

    def replace_href(m: re.Match) -> str:
        prefix, href, suffix = m.group(1), m.group(2), m.group(3)
        if is_external(href):
            return m.group(0)
        href_clean = href.split("?")[0].split("#")[0].strip()
        if not href_clean or not href_clean.endswith(".html"):
            return m.group(0)
        resolved = (html_path.parent / href_clean).resolve()
        try:
            resolved.relative_to(site_dir.resolve())
        except ValueError:
            return m.group(0)
        if resolved.exists():
            return m.group(0)
        fixes.append(f"Gebroken link {href_clean!r} → #")
        return f'{prefix}#{suffix}'

    return A_HREF_RE.sub(replace_href, content), fixes


def fix_file_markers(content: str) -> tuple[str, list[str]]:
    new_content, n = FILE_MARKER_RE.subn("", content)
    fixes = [f"{n} rauwe file-marker(s) verwijderd"] if n else []
    return new_content, fixes


def repair_file(html_path: Path, site_dir: Path) -> list[str]:
    content  = html_path.read_text(encoding="utf-8", errors="ignore")
    original = content
    all_fixes: list[str] = []

    content, f = fix_file_markers(content)
    all_fixes.extend(f)
    content, f = fix_link_tags(content)
    all_fixes.extend(f)
    content, f = fix_script_tags(content, site_dir, html_path)
    all_fixes.extend(f)
    content, f = fix_broken_links(content, site_dir, html_path)
    all_fixes.extend(f)

    if content != original:
        html_path.write_text(content, encoding="utf-8")
    return all_fixes


# ── Site-brede fixes ──────────────────────────────────────────────────────────

def fix_ref_files(site_dir: Path) -> list[str]:
    """Verwijder interne referentiebestanden (_*.html) die niet voor bezoekers bedoeld zijn."""
    fixes = []
    for f in site_dir.glob("_*.html"):
        try:
            f.unlink()
            fixes.append(f"Referentiefile verwijderd: {f.name}")
        except Exception as e:
            fixes.append(f"[WARN] Kon {f.name} niet verwijderen: {e}")
    return fixes


def fix_sr_only(site_dir: Path) -> list[str]:
    """Voeg .sr-only CSS-definitie toe als die gebruikt wordt maar ontbreekt."""
    css_path = site_dir / "assets" / "css" / "style.css"
    if not css_path.exists():
        return []

    used = any(
        "sr-only" in f.read_text(encoding="utf-8", errors="ignore")
        for f in site_dir.glob("*.html")
        if not f.name.startswith("_")
    )
    if not used:
        return []

    css_text = css_path.read_text(encoding="utf-8", errors="ignore")
    if ".sr-only" in css_text:
        return []

    css_path.write_text(css_text + f"\n\n{SR_ONLY_CSS}\n", encoding="utf-8")
    return [".sr-only definitie toegevoegd aan CSS"]


def _brace_diff(js: str) -> int:
    """Tel het verschil tussen { en } in JS (strings en comments genegeerd)."""
    c = re.sub(r'//[^\n]*', '', js)
    c = re.sub(r'/\*.*?\*/', '', c, flags=re.DOTALL)
    c = re.sub(r'`(?:[^`\\]|\\.)*`', '``', c)
    c = re.sub(r'"(?:[^"\\]|\\.)*"', '""', c)
    c = re.sub(r"'(?:[^'\\]|\\.)*'", "''", c)
    return c.count('{') - c.count('}')


def fix_footer_year(site_dir: Path) -> list[str]:
    """Voeg JS snippet toe voor id='footerYear' als dat in HTML staat maar JS het niet invult."""
    js_path = site_dir / "assets" / "js" / "main.js"
    if not js_path.exists():
        return []

    used = any(
        "footerYear" in f.read_text(encoding="utf-8", errors="ignore")
        for f in site_dir.glob("*.html")
        if not f.name.startswith("_")
    )
    if not used:
        return []

    js_text = js_path.read_text(encoding="utf-8", errors="ignore")
    if "footerYear" in js_text:
        return []

    js_path.write_text(js_text + FOOTER_YEAR_JS, encoding="utf-8")
    return ["footerYear JS snippet toegevoegd"]


def fix_js_truncation(site_dir: Path) -> list[str]:
    """
    Detecteer en herstel afgekapte JS-bestanden.
    Strategie:
    1. Verwijder het footerYear-snippet tijdelijk (indien aanwezig)
    2. Strip incomplete laatste regels
    3. Sluit openstaande accolades
    4. Voeg footerYear terug
    """
    js_path = site_dir / "assets" / "js" / "main.js"
    if not js_path.exists():
        return []

    original = js_path.read_text(encoding="utf-8", errors="ignore")

    # Verwijder ons footerYear-snippet tijdelijk zodat de balans-check klopt
    footer_marker = "// footerYear invullen"
    footer_snippet: str | None = None
    js = original
    if footer_marker in js:
        idx = js.index(footer_marker)
        footer_snippet = "\n" + js[idx:].lstrip("\n")
        js = js[:idx].rstrip("\n")

    diff = _brace_diff(js)
    if diff <= 0:
        # Gebalanceerd — schrijf terug zonder wijziging (footerYear zit er al in)
        return []

    # Strip onvolledige laatste regels tot we een complete regel bereiken
    lines = js.split("\n")
    stripped = 0
    COMPLETE_ENDINGS = re.compile(r'[;{})]\s*$|^\s*$|^\s*//')
    while lines:
        if COMPLETE_ENDINGS.search(lines[-1]):
            break
        lines.pop()
        stripped += 1

    js_trimmed = "\n".join(lines).rstrip("\n")

    # Tel opnieuw na strippen
    diff_after = _brace_diff(js_trimmed)

    # Bouw sluitsequentie op basis van context
    closing_parts: list[str] = []
    remaining = diff_after
    if remaining > 0:
        # Sluit de binnenste callback (event listener) met });
        if remaining >= 1 and re.search(r'addEventListener\s*\([^)]+,\s*function', js_trimmed):
            closing_parts.append("  });")
            remaining -= 1
        # Sluit overige named functions met }
        while remaining > 1:
            closing_parts.append("}")
            remaining -= 1
        # Sluit buitenste IIFE
        if remaining == 1:
            if js_trimmed.lstrip().startswith("(function"):
                closing_parts.append("}());")
            else:
                closing_parts.append("}")

    closing = "\n" + "\n".join(closing_parts) if closing_parts else ""
    js_fixed = js_trimmed + closing

    # Voeg footerYear terug
    if footer_snippet:
        js_fixed = js_fixed + footer_snippet
    elif "footerYear" not in js_fixed:
        # Controleer of het nodig is
        for html_f in site_dir.glob("*.html"):
            if not html_f.name.startswith("_") and "footerYear" in html_f.read_text(encoding="utf-8", errors="ignore"):
                js_fixed = js_fixed + FOOTER_YEAR_JS
                break

    if js_fixed == original:
        return []

    js_path.write_text(js_fixed, encoding="utf-8")
    return [
        f"JS-truncatie hersteld: {stripped} afgekapte regel(s) verwijderd, "
        f"{diff_after} accolades gesloten"
    ]


def fix_hamburger_mismatch(site_dir: Path) -> list[str]:
    """
    Detecteer en herstel mismatch tussen de class die JS toevoegt aan het nav-element
    bij het openen van het hamburger-menu en de class waarop de CSS reageert.
    """
    js_path  = site_dir / "assets" / "js" / "main.js"
    css_path = site_dir / "assets" / "css" / "style.css"
    if not js_path.exists() or not css_path.exists():
        return []

    js_text  = js_path.read_text(encoding="utf-8", errors="ignore")
    css_text = css_path.read_text(encoding="utf-8", errors="ignore")

    TOGGLE_NAME_PARTS = frozenset(
        {"toggle", "btn", "button", "hamburger", "burger", "trigger", "icon", "header"}
    )
    VAR_RE = re.compile(
        r'(?:var|let|const)\s+(\w+)\s*=\s*'
        r'(?:qs\b|document\.querySelector|document\.getElementById)\s*\([\'"]([^\'"]+)[\'"]\)',
        re.IGNORECASE,
    )
    nav_vars: dict[str, str] = {}
    for m in VAR_RE.finditer(js_text):
        varname, selector = m.group(1), m.group(2)
        if any(x in selector.lower() for x in ("nav", "menu", "mobile")):
            if not any(x in varname.lower() for x in TOGGLE_NAME_PARTS):
                nav_vars[varname] = selector

    if not nav_vars:
        return []

    SKIP_CLS_PARTS = frozenset(
        {"stick", "scroll", "-link", "dropdown", "header", "current", "selected", "visible"}
    )
    js_nav_opens: list[tuple[str, str, str]] = []
    for varname, selector in nav_vars.items():
        pat = re.compile(
            rf'\b{re.escape(varname)}\.classList\.(?:add|toggle)\([\'"]([^\'"]+)[\'"]'
        )
        for m in pat.finditer(js_text):
            cls = m.group(1)
            if not any(skip in cls.lower() for skip in SKIP_CLS_PARTS):
                js_nav_opens.append((varname, selector, cls))
                break

    if not js_nav_opens:
        return []

    SHOW_RE = re.compile(
        r'display\s*:\s*(?:block|flex|grid)|max-height\s*:\s*(?!0[\s;,])',
        re.IGNORECASE,
    )
    CSS_COMPOUND_RE = re.compile(r'(\.[\w-]+)\.([\w-]+)\s*\{([^}]+)\}', re.DOTALL)

    css_nav_open: dict[str, tuple[str, str]] = {}
    for m in CSS_COMPOUND_RE.finditer(css_text):
        base, modifier, body = m.group(1), m.group(2), m.group(3)
        if any(x in base.lower() for x in ("nav", "menu", "mobile")) and SHOW_RE.search(body):
            if base not in css_nav_open:
                css_nav_open[base] = (modifier, body)

    if not css_nav_open:
        return []

    id_class_map: dict[str, str] = {}
    index_html = site_dir / "index.html"
    if index_html.exists():
        html = index_html.read_text(encoding="utf-8", errors="ignore")
        TAG_RE = re.compile(r'<[a-zA-Z]+([^>]+)>', re.DOTALL)
        for tag_m in TAG_RE.finditer(html):
            attrs = tag_m.group(1)
            id_m  = re.search(r'\bid=[\'"]([^\'"]+)[\'"]', attrs)
            cls_m = re.search(r'\bclass=[\'"]([^\'"]+)[\'"]', attrs)
            if id_m and cls_m:
                id_class_map[id_m.group(1)] = cls_m.group(1).split()[0]

    def js_sel_to_css_base(sel: str) -> str | None:
        if sel.startswith("."):
            return sel
        id_val = sel[1:] if sel.startswith("#") else sel
        if id_val in id_class_map:
            return f".{id_class_map[id_val]}"
        kebab = re.sub(r"([A-Z])", lambda x: "-" + x.group(1).lower(), id_val).lstrip("-")
        return f".{kebab}"

    fixes: list[str] = []
    new_css = css_text

    for varname, js_selector, js_open_cls in js_nav_opens:
        css_base = js_sel_to_css_base(js_selector)
        if not css_base:
            continue

        if css_base in css_nav_open:
            css_modifier, css_body = css_nav_open[css_base]
            css_base_actual = css_base
        else:
            first = next(iter(css_nav_open.items()), None)
            if first is None:
                continue
            css_base_actual, (css_modifier, css_body) = first

        if js_open_cls == css_modifier and css_base == css_base_actual:
            continue

        alias_sel = f"{css_base}.{js_open_cls}"
        if alias_sel in new_css:
            continue

        new_css += f"\n/* hamburger fix */\n{alias_sel} {{{css_body}}}\n"
        fixes.append(
            f"Hamburger alias toegevoegd: {alias_sel} "
            f"(JS gebruikt '{js_open_cls}', CSS had '{css_base_actual}.{css_modifier}')"
        )

    if fixes:
        css_path.write_text(new_css, encoding="utf-8")

    return fixes


def fix_portfolio_filter_class(site_dir: Path) -> list[str]:
    """
    Voeg portfolio-filter-btn class toe aan filter-btn buttons als JS die verwacht
    maar HTML ze niet heeft.
    """
    js_path = site_dir / "assets" / "js" / "main.js"
    if not js_path.exists():
        return []

    js = js_path.read_text(encoding="utf-8", errors="ignore")
    if ".portfolio-filter-btn" not in js:
        return []

    fixes: list[str] = []
    for html_path in sorted(site_dir.glob("*.html")):
        if html_path.name.startswith("_"):
            continue
        content = html_path.read_text(encoding="utf-8", errors="ignore")
        if "filter-btn" not in content or "portfolio-filter-btn" in content:
            continue

        # Voeg portfolio-filter-btn toe aan elke filter-btn
        new_content = re.sub(
            r'class="((?:[\w-]+ )*filter-btn(?:[ ][\w-]+)*)"',
            lambda m: f'class="{m.group(1)} portfolio-filter-btn"',
            content,
        )
        if new_content != content:
            html_path.write_text(new_content, encoding="utf-8")
            fixes.append(f"portfolio-filter-btn class toegevoegd in {html_path.name}")

    return fixes


def fix_lightbox_class(site_dir: Path) -> list[str]:
    """
    Fix lightbox-triggers:
    - voeg portfolio-item__link class toe aan <a class="portfolio-item">
    - vervang href="#lightbox-N" door de src van de ingesloten <img>
    """
    js_path = site_dir / "assets" / "js" / "main.js"
    if not js_path.exists():
        return []

    js = js_path.read_text(encoding="utf-8", errors="ignore")
    if ".portfolio-item__link" not in js and ".gallery__link" not in js:
        return []

    # Regex die een <a class="...portfolio-item...">…</a> blok matcht
    ANCHOR_RE = re.compile(
        r'(<a\b[^>]*\bclass="([^"]*\bportfolio-item\b[^"]*)"[^>]*>)(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    IMG_SRC_RE = re.compile(r'<img\b[^>]*\bsrc="([^"]+)"', re.IGNORECASE)
    HREF_ATTR_RE = re.compile(r'\bhref="([^"]*)"')

    fixes: list[str] = []

    for html_path in sorted(site_dir.glob("*.html")):
        if html_path.name.startswith("_"):
            continue
        content = html_path.read_text(encoding="utf-8", errors="ignore")
        if "portfolio-item__link" in content or "portfolio-item" not in content:
            continue

        changed = 0

        def replace_anchor(m: re.Match) -> str:
            nonlocal changed
            tag_open, classes, inner = m.group(1), m.group(2), m.group(3)

            img_m = IMG_SRC_RE.search(inner)
            img_src = img_m.group(1) if img_m else None

            href_m = HREF_ATTR_RE.search(tag_open)
            current_href = href_m.group(1) if href_m else ""

            # Vervang #lightbox-N href door de img src
            new_tag = tag_open
            if img_src and current_href.startswith("#"):
                new_tag = HREF_ATTR_RE.sub(f'href="{img_src}"', new_tag, count=1)

            # Voeg portfolio-item__link class toe
            if "portfolio-item__link" not in classes:
                new_classes = classes + " portfolio-item__link"
                new_tag = new_tag.replace(f'class="{classes}"', f'class="{new_classes}"', 1)

            changed += 1
            return f"{new_tag}{inner}</a>"

        new_content = ANCHOR_RE.sub(replace_anchor, content)
        if changed and new_content != content:
            html_path.write_text(new_content, encoding="utf-8")
            fixes.append(f"Lightbox triggers hersteld in {html_path.name} ({changed} items)")

    return fixes


def fix_font_loading(site_dir: Path) -> list[str]:
    """
    Verplaats Google Fonts van CSS @import naar HTML <link> tags.
    @import blokkeert render; <link rel="preconnect"> + <link rel="stylesheet"> is sneller.
    """
    css_path = site_dir / "assets" / "css" / "style.css"
    if not css_path.exists():
        return []

    css = css_path.read_text(encoding="utf-8", errors="ignore")

    IMPORT_RE = re.compile(
        r"@import\s+url\(['\"]?(https://fonts\.googleapis\.com[^'\")\s]+)['\"]?\)\s*;?\n?",
        re.IGNORECASE,
    )
    font_urls = IMPORT_RE.findall(css)
    if not font_urls:
        return []

    # Check of HTML al font-links heeft
    index_html = site_dir / "index.html"
    if index_html.exists():
        if "fonts.googleapis.com" in index_html.read_text(encoding="utf-8", errors="ignore"):
            return []  # al correct

    # Verwijder @import uit CSS
    new_css = IMPORT_RE.sub("", css).lstrip("\n")
    css_path.write_text(new_css, encoding="utf-8")

    # Bouw HTML <link> tags
    preconnects = (
        '  <link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    )
    font_links = "".join(f'  <link rel="stylesheet" href="{url}">\n' for url in font_urls)
    inject = preconnects + font_links

    fixes = [f"Google Fonts @import → HTML <link> ({len(font_urls)} url(s))"]

    CSS_LINK_RE = re.compile(
        r'(<link\s[^>]*rel=["\']stylesheet["\']\s[^>]*href=["\']assets/css/style\.css["\'][^>]*>)',
        re.IGNORECASE,
    )

    for html_path in sorted(site_dir.glob("*.html")):
        if html_path.name.startswith("_"):
            continue
        content = html_path.read_text(encoding="utf-8", errors="ignore")
        if "fonts.googleapis.com" in content:
            continue

        # Voeg font-links in vóór de stylesheet-link
        m = CSS_LINK_RE.search(content)
        if m:
            new_content = content[: m.start()] + inject.rstrip("\n") + "\n  " + content[m.start():]
        else:
            new_content = re.sub(r"(</head>)", inject + r"\1", content, flags=re.IGNORECASE, count=1)

        if new_content != content:
            html_path.write_text(new_content, encoding="utf-8")
            fixes.append(f"Font-links toegevoegd aan {html_path.name}")

    return fixes


def fix_favicon(site_dir: Path) -> list[str]:
    """
    Maak een generieke SVG-favicon aan en voeg <link rel="icon"> toe aan alle pagina's
    als er nog geen favicon aanwezig is.
    """
    # Check of er al een favicon bestaat
    for html_path in site_dir.glob("*.html"):
        if html_path.name.startswith("_"):
            continue
        content = html_path.read_text(encoding="utf-8", errors="ignore")
        if 'rel="icon"' in content or 'rel="shortcut icon"' in content:
            return []

    # Haal eerste letter op uit de paginatitel
    initial = "•"
    index_html = site_dir / "index.html"
    if index_html.exists():
        m = re.search(r"<title>([^<]+)", index_html.read_text(encoding="utf-8", errors="ignore"))
        if m:
            first = m.group(1).strip()
            initial = html_escape(first[0].upper()) if first else "•"

    favicon_svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="6" fill="#1a1a2e"/>
  <text x="16" y="23" font-family="Arial,sans-serif" font-size="20" font-weight="bold"
        text-anchor="middle" fill="#ffffff">{initial}</text>
</svg>
"""
    favicon_path = site_dir / "favicon.svg"
    favicon_path.write_text(favicon_svg, encoding="utf-8")

    link_tag = '<link rel="icon" type="image/svg+xml" href="favicon.svg">'
    fixes = ["favicon.svg aangemaakt"]

    CHARSET_RE = re.compile(r'(<meta\s[^>]*charset=[^>]+>)', re.IGNORECASE)

    for html_path in sorted(site_dir.glob("*.html")):
        if html_path.name.startswith("_"):
            continue
        content = html_path.read_text(encoding="utf-8", errors="ignore")
        if 'rel="icon"' in content:
            continue

        m = CHARSET_RE.search(content)
        if m:
            new_content = content[: m.end()] + f"\n  {link_tag}" + content[m.end():]
        else:
            new_content = re.sub(
                r"(</head>)", f"  {link_tag}\n" + r"\1", content, flags=re.IGNORECASE, count=1
            )

        if new_content != content:
            html_path.write_text(new_content, encoding="utf-8")
            fixes.append(f"favicon link toegevoegd aan {html_path.name}")

    return fixes


FAQ_JS_FIX = """
/* faq-fix: universele accordion voor div-gebaseerde FAQ met wisselende class names */
(function () {
  document.querySelectorAll('.faq-item, .faq__item').forEach(function (item) {
    var trigger = item.querySelector('.faq-item__question, .faq__question, .faq-question');
    var answer  = item.querySelector('.faq-item__answer, .faq__answer, .faq-answer');
    if (!trigger || !answer || trigger._fi) return;
    trigger._fi = 1;
    if (answer.hasAttribute('hidden')) {
      answer.removeAttribute('hidden');
      answer.style.overflow   = 'hidden';
      answer.style.maxHeight  = item.classList.contains('is-open') ? answer.scrollHeight + 'px' : '0';
      answer.style.transition = 'max-height .3s ease';
    }
    trigger.addEventListener('click', function () {
      var open = item.classList.toggle('is-open');
      trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
      answer.style.maxHeight = open ? answer.scrollHeight + 'px' : '0';
    });
  });
})();
"""

CHEVRON_SVG = '<svg class="faq-icon" viewBox="0 0 24 24" aria-hidden="true" width="20" height="20"><path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>'

DETAILS_RE = re.compile(
    r'<details([^>]*class="[^"]*faq[^"]*"[^>]*)>\s*'
    r'<summary([^>]*)>(.*?)</summary>\s*'
    r'(<div[^>]*>.*?</div>)\s*'
    r'</details>',
    re.DOTALL | re.IGNORECASE,
)

FAQ_BUTTON_CSS = """
/* faq-button-fix: grid-layout zodat tekst en icon betrouwbaar naast elkaar staan */
.faq-question, .faq-item__question {
  display: grid !important;
  grid-template-columns: 1fr auto !important;
  align-items: center !important;
  gap: 0.75rem !important;
  width: 100% !important;
}
.faq-item.is-open .faq-icon,
.faq-item.is-open .faq-toggle,
.faq-item.is-open .faq-toggle__icon {
  transform: rotate(180deg);
}
.faq-item.is-open .faq-answer,
.faq-item.is-open .faq-item__answer {
  max-height: 2000px !important;
  padding-bottom: 1rem;
}
"""


def _convert_details_faq(html: str) -> tuple[str, int]:
    """Zet <details class="faq-*">/<summary> om naar <div class="faq-item">/<button>."""
    count = 0

    def replace(m: re.Match) -> str:
        nonlocal count
        det_attrs   = m.group(1)
        sum_inner   = m.group(3).strip()
        answer_html = m.group(4).strip()

        answer_html = re.sub(r'\bfaq-item__answer\b', 'faq-answer', answer_html)
        answer_html = re.sub(r'\bfaq__answer\b', 'faq-answer', answer_html)

        cls_m = re.search(r'class="([^"]*)"', det_attrs)
        item_class = cls_m.group(1) if cls_m else 'faq-item'

        count += 1
        return (
            f'<div class="{item_class}">\n'
            f'          <button class="faq-question" aria-expanded="false">'
            f'<span class="faq-question__text">{sum_inner}</span>'
            f'{CHEVRON_SVG}</button>\n'
            f'          {answer_html}\n'
            f'        </div>'
        )

    return DETAILS_RE.sub(replace, html), count


def fix_faq_accordion(site_dir: Path) -> list[str]:
    """
    Repareer FAQ accordions:
    - <details>/<summary> pattern: converteer naar <div>/<button> zodat JS polyfill werkt
    - <div>/<button> pattern met verkeerde class names of hidden-attribuut: JS polyfill fix
    """
    html_files = [f for f in sorted(site_dir.glob("*.html")) if not f.name.startswith("_")]
    if not html_files:
        return []

    fixes: list[str] = []

    # Stap 1: converteer <details>/<summary> FAQ naar <div>/<button>
    for html_path in html_files:
        content = html_path.read_text(encoding="utf-8", errors="ignore")
        if not re.search(r'<details[^>]+class="[^"]*faq', content, re.IGNORECASE):
            continue
        new_content, n = _convert_details_faq(content)
        if n:
            html_path.write_text(new_content, encoding="utf-8")
            fixes.append(f"FAQ: {n} <details>→<div> omgezet in {html_path.name}")

    # Hercheck na conversie
    has_div_faq = any(
        re.search(r'class="[^"]*faq-item\b', f.read_text(encoding="utf-8", errors="ignore"))
        for f in html_files
    )

    if not has_div_faq and not fixes:
        return []

    # Stap 2: injecteer CSS button-fix (grid-layout voor betrouwbare text+icon uitlijning)
    css_path = site_dir / "assets" / "css" / "style.css"
    if css_path.exists():
        css = css_path.read_text(encoding="utf-8", errors="ignore")
        if "faq-button-fix" not in css:
            css_path.write_text(css + FAQ_BUTTON_CSS, encoding="utf-8")
            fixes.append("FAQ button CSS-fix toegevoegd (grid-layout voor text+icon)")

    # Stap 3: FAQ JS — zorg dat er precies één click-listener zit.
    # Als initFaq aanwezig is, is de volledige polyfill (faq-fix) schadelijk:
    # twee listeners → open→sluit onmiddellijk → FAQ werkt niet.
    # Oplossing: verwijder de faq-fix polyfill als initFaq aanwezig is.
    INIT_PATCH = """
/* faq-init-patch: initialiseer maxHeight voor gesloten items (geen extra listener) */
(function () {
  document.querySelectorAll('.faq-item:not(.is-open) .faq-answer, .faq__item:not(.is-open) .faq__answer').forEach(function (a) {
    if (!a.style.maxHeight) {
      a.style.overflow  = 'hidden';
      a.style.maxHeight = '0';
      a.style.transition = 'max-height .3s ease';
    }
  });
})();
"""
    js_path = site_dir / "assets" / "js" / "main.js"
    if js_path.exists():
        js = js_path.read_text(encoding="utf-8", errors="ignore")
        has_init_faq = "function initFaq" in js or ("initFaq" in js and "faq-fix" not in js)

        if "faq-fix" in js and has_init_faq:
            # Verwijder de schadelijke dubbele polyfill
            js = re.sub(
                r'\n*/\* faq-fix:.*?}\)\(\);\n?',
                '\n' + INIT_PATCH,
                js, flags=re.DOTALL
            )
            js_path.write_text(js, encoding="utf-8")
            fixes.append("FAQ dubbele listener gerepareerd (faq-fix vervangen door init-patch)")
        elif "faq-fix" not in js and "faq-init-patch" not in js:
            if has_init_faq:
                js_path.write_text(js + INIT_PATCH, encoding="utf-8")
                fixes.append("FAQ init-patch toegevoegd (maxHeight initialisatie zonder extra listener)")
            else:
                js_path.write_text(js + "\n" + FAQ_JS_FIX, encoding="utf-8")
                fixes.append("FAQ JS polyfill toegevoegd (geen initFaq gevonden)")

    return fixes


def fix_low_contrast(site_dir: Path) -> list[str]:
    """
    Vervang lage-alpha rgba kleuren in CSS die contrast-problemen veroorzaken:
    - rgba(255,255,255,X) waar X < 0.85 → 0.9  (witte tekst op donkere achtergrond)
    - rgba(0,0,0,X) waar X < 0.5 in color: context → 0.65 (grijze tekst op lichte achtergrond)
    Raakt ALLEEN color: regels, niet background-color of opacity overlays.
    """
    css_path = site_dir / "assets" / "css" / "style.css"
    if not css_path.exists():
        return []

    css = css_path.read_text(encoding="utf-8", errors="ignore")
    original = css
    count = 0

    def fix_white_rgba(m: re.Match) -> str:
        nonlocal count
        alpha = float(m.group(1))
        if alpha < 0.85:
            count += 1
            return f"rgba(255,255,255,0.9)"
        return m.group(0)

    def fix_black_rgba(m: re.Match) -> str:
        nonlocal count
        alpha = float(m.group(1))
        if alpha < 0.5:
            count += 1
            return f"rgba(0,0,0,0.65)"
        return m.group(0)

    # Alleen in color: context (niet background, border, box-shadow, etc.)
    COLOR_PROP_RE = re.compile(
        r'(?<!\w)color\s*:\s*[^;{}\n]*',
        re.IGNORECASE,
    )

    def fix_color_value(m: re.Match) -> str:
        val = m.group(0)
        val = re.sub(r'rgba\(\s*255\s*,\s*255\s*,\s*255\s*,\s*([\d.]+)\s*\)', fix_white_rgba, val)
        val = re.sub(r'rgba\(\s*0\s*,\s*0\s*,\s*0\s*,\s*([\d.]+)\s*\)', fix_black_rgba, val)
        return val

    css = COLOR_PROP_RE.sub(fix_color_value, css)

    if count and css != original:
        css_path.write_text(css, encoding="utf-8")
        return [f"Contrast fix: {count} lage-alpha rgba kleur(en) verbeterd"]
    return []


# CSS to inject when keuze-cards appear inside a light-background section
KEUZE_LIGHT_BG_CSS = """
/* keuze-card-contrast-fix: kaarten staan op lichte achtergrond, gebruik donkere tekst */
.section--tint .keuze-card,
.section--bg .keuze-card,
.section:not(.keuzeblokken) .keuze-card {
  background: rgba(255,255,255,0.7);
  border-color: rgba(0,0,0,0.1);
}
.section--tint .keuze-card-heading,
.section--bg .keuze-card-heading,
.section:not(.keuzeblokken) .keuze-card-heading {
  color: var(--color-text, #1a1a1a);
}
.section--tint .keuze-card-text,
.section--bg .keuze-card-text,
.section:not(.keuzeblokken) .keuze-card-text {
  color: rgba(0,0,0,0.75);
}
/* herstel witte stijl wanneer WEL in keuzeblokken (donkere bg) */
.keuzeblokken .keuze-card {
  background: rgba(255,255,255,0.06);
  border-color: rgba(255,255,255,0.1);
}
.keuzeblokken .keuze-card-heading {
  color: var(--color-white, #fff);
}
.keuzeblokken .keuze-card-text {
  color: rgba(255,255,255,0.9);
}
"""


FOOTER_FIX_CSS = """
/* footer-fix: padding, gap en basis-stijling ongeacht class-name variaties */
footer, .site-footer, [class*="footer"]:not([class*="footer-"]) {
  padding-top: var(--space-xl, 3rem) !important;
  padding-bottom: var(--space-lg, 2rem) !important;
}
/* Zorg dat footer-grid kinderen altijd zichtbaar zijn, ook bij class-mismatch */
.footer-grid > * { min-width: 0; }
/* footer-inner als wrapper ontbreekt: container pakt de padding over */
.site-footer > .container, footer > .container {
  padding-top: var(--space-xl, 3rem);
  padding-bottom: var(--space-lg, 2rem);
}
/* Links in footer leesbaar */
footer a, .site-footer a {
  color: inherit;
  opacity: 0.85;
  text-decoration: none;
}
footer a:hover, .site-footer a:hover { opacity: 1; }
/* Footer-bottom balk */
.footer-bottom, [class*="footer-bottom"] {
  margin-top: var(--space-lg, 2rem);
  padding-top: var(--space-sm, 0.75rem);
  border-top: 1px solid rgba(255,255,255,0.15);
  font-size: 0.8rem;
  opacity: 0.7;
}
"""


def fix_footer(site_dir: Path) -> list[str]:
    """Voeg footer-fix CSS toe als de footer padding of basisstijling mist."""
    css_path = site_dir / "assets" / "css" / "style.css"
    if not css_path.exists():
        return []
    css = css_path.read_text(encoding="utf-8", errors="ignore")
    if "footer-fix" in css:
        return []
    # Alleen injecteren als er daadwerkelijk een footer in de HTML staat
    html_files = list(site_dir.glob("*.html"))
    has_footer  = any(
        "<footer" in (f.read_text(encoding="utf-8", errors="ignore") if f.exists() else "")
        for f in html_files[:3]
    )
    if not has_footer:
        return []
    css_path.write_text(css + FOOTER_FIX_CSS, encoding="utf-8")
    return ["Footer-fix CSS toegevoegd (padding + links + footer-bottom)"]


NAV_ACTIVE_JS = """
/* nav-active: markeer de huidige pagina als actief in de navigatie */
(function () {
  var current = location.pathname.split('/').pop() || 'index.html';
  document.querySelectorAll('nav a[href], header a[href]').forEach(function (a) {
    var href = a.getAttribute('href').split('#')[0].split('?')[0];
    if (href === current || (current === '' && href === 'index.html')) {
      a.classList.add('is-active');
      a.setAttribute('aria-current', 'page');
    }
  });
})();
"""

NAV_ACTIVE_CSS = """
/* nav-active: highlight huidige pagina in navigatie */
nav a.is-active, header a.is-active,
.site-nav a.is-active, .nav-list a.is-active,
.main-nav a.is-active {
  color: var(--color-primary, var(--accent, #6366f1)) !important;
  font-weight: 600 !important;
  border-bottom: 2px solid currentColor;
}
"""


def fix_nav_active_state(site_dir: Path) -> list[str]:
    """Voeg nav-active JS + CSS toe zodat de huidige pagina gemarkeerd is in de nav."""
    fixes = []

    js_path = site_dir / "assets" / "js" / "main.js"
    if js_path.exists():
        js = js_path.read_text(encoding="utf-8", errors="ignore")
        if "nav-active" not in js:
            js_path.write_text(js + NAV_ACTIVE_JS, encoding="utf-8")
            fixes.append("Nav active state JS toegevoegd")

    css_path = site_dir / "assets" / "css" / "style.css"
    if css_path.exists():
        css = css_path.read_text(encoding="utf-8", errors="ignore")
        if "nav-active" not in css:
            css_path.write_text(css + NAV_ACTIVE_CSS, encoding="utf-8")
            fixes.append("Nav active state CSS toegevoegd")

    return fixes


def fix_keuze_card_on_light_bg(site_dir: Path) -> list[str]:
    """
    Wanneer een .keuze-grid voorkomt buiten een .keuzeblokken sectie (bijv. in section--tint),
    dan zijn de witte kaartteksten onleesbaar. Injecteer CSS die de kleuren corrigeert.
    """
    css_path = site_dir / "assets" / "css" / "style.css"
    if not css_path.exists():
        return []

    # Check of de site überhaupt keuze-cards heeft
    html_files = list(site_dir.glob("*.html"))
    has_keuze_on_light = any(
        "keuze-grid" in f.read_text(encoding="utf-8", errors="ignore")
        and "section--tint" in f.read_text(encoding="utf-8", errors="ignore")
        for f in html_files
    )
    if not has_keuze_on_light:
        return []

    css = css_path.read_text(encoding="utf-8", errors="ignore")
    if "keuze-card-contrast-fix" in css:
        return []  # al toegepast

    css += KEUZE_LIGHT_BG_CSS
    css_path.write_text(css, encoding="utf-8")
    return ["Contrast fix: keuze-card tekst aangepast voor lichte achtergrond"]


def fix_lazy_loading(site_dir: Path) -> list[str]:
    """
    Voeg loading="lazy" toe aan <img> tags die dat attribuut missen.
    De eerste afbeelding per pagina (hero / above-the-fold) krijgt geen lazy loading.
    """
    fixes: list[str] = []

    for html_path in sorted(site_dir.glob("*.html")):
        if html_path.name.startswith("_"):
            continue
        content = html_path.read_text(encoding="utf-8", errors="ignore")

        images = list(IMG_RE.finditer(content))
        if not images:
            continue

        parts: list[str] = []
        last_end = 0
        added = 0

        for idx, m in enumerate(images):
            parts.append(content[last_end : m.start()])
            attrs = m.group(1)
            if "loading=" in attrs.lower() or idx == 0:
                parts.append(m.group(0))
            else:
                parts.append(f"<img{attrs} loading=\"lazy\">")
                added += 1
            last_end = m.end()

        parts.append(content[last_end:])

        if added:
            html_path.write_text("".join(parts), encoding="utf-8")
            fixes.append(
                f'loading="lazy" toegevoegd aan {added} afbeeldingen in {html_path.name}'
            )

    return fixes


# ── Entrypoint ────────────────────────────────────────────────────────────────

CARD_CONTRAST_CSS = """
/* card-contrast-fix: kaarten met lichte achtergrond krijgen altijd donkere tekst,
   ongeacht de achtergrondkleur van de parent-sectie */
{selectors} {{
  color: var(--color-text, #1a1a1a);
}}
{selectors_children} {{
  color: inherit;
}}
"""

# Klassen die lichte achtergronden hebben maar typisch state/utility zijn — skip
_SKIP_CONTRAST_CLASSES = {
    "modal", "tooltip", "dropdown", "popup", "overlay",
    "badge", "tag", "chip", "alert", "notification", "toast",
}

def fix_card_contrast(site_dir: Path) -> list[str]:
    """
    Detecteer CSS-klassen met lichte achtergrond (white / rgba(255,255,255,...) /
    lichte CSS-variabelen) en voeg expliciet color: var(--color-text) toe zodat
    tekst leesbaar blijft ook als de parent-sectie donker is.
    """
    css_path = site_dir / "assets" / "css" / "style.css"
    if not css_path.exists():
        return []
    css = css_path.read_text(encoding="utf-8", errors="ignore")
    if "card-contrast-fix" in css:
        return []

    light_bg_re = re.compile(
        r'(\.[\w-]+)\s*\{([^}]*background(?:-color)?:\s*'
        r'(?:#fff\b|#ffffff\b|white\b|rgba\(\s*255\s*,\s*255\s*,\s*255'
        r'|var\(--color-(?:white|light|cream|parchment|bg\b|surface\b|card\b))'
        r'[^}]*)\}',
        re.IGNORECASE,
    )

    light_classes = []
    for m in light_bg_re.finditer(css):
        cls      = m.group(1).lstrip(".")
        rule_body = m.group(2)
        # Sla over als de klasse al een expliciete color: heeft
        if re.search(r'\bcolor\s*:', rule_body):
            continue
        if any(skip in cls for skip in _SKIP_CONTRAST_CLASSES):
            continue
        light_classes.append(m.group(1))  # inclusief punt

    if not light_classes:
        return []

    selectors          = ",\n".join(light_classes)
    children_selectors = ",\n".join(
        f"{s} p, {s} h1, {s} h2, {s} h3, {s} h4, {s} h5, {s} li, {s} span, {s} a"
        for s in light_classes
    )
    injection = CARD_CONTRAST_CSS.format(
        selectors=selectors,
        selectors_children=children_selectors,
    )
    css_path.write_text(css + injection, encoding="utf-8")
    return [f"Card contrast fix: {len(light_classes)} klassen met lichte achtergrond"]


# Skip-lijst voor CSS coverage check — state, utility en BEM-modifier classes
_COVERAGE_SKIP = {
    "active", "inactive", "is-open", "is-active", "is-scrolled", "is-hidden",
    "is-visible", "is-loading", "open", "closed", "hidden", "visible",
    "disabled", "selected", "checked", "loading", "loaded", "error",
    "container", "wrapper", "inner", "outer", "row", "col", "grid",
    "sr-only", "clearfix", "js", "no-js", "lazyload", "lazyloaded",
    "site-factory-demo-banner",
}


def check_css_coverage(site_dir: Path) -> list[str]:
    """
    Verzamel alle class names uit de HTML en check welke geen CSS-regel hebben.
    Rapporteert als WARN zodat de pipeline niet blokkeert maar problemen
    wel zichtbaar zijn in de log.
    """
    css_path = site_dir / "assets" / "css" / "style.css"
    if not css_path.exists():
        return []
    css = css_path.read_text(encoding="utf-8", errors="ignore")

    all_classes: set[str] = set()
    for html_path in site_dir.glob("*.html"):
        if html_path.name.startswith("_"):
            continue
        content = html_path.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r'class="([^"]+)"', content):
            for cls in m.group(1).split():
                all_classes.add(cls)

    missing = []
    for cls in sorted(all_classes):
        # Skip utility/state classes
        if cls in _COVERAGE_SKIP:
            continue
        # Skip BEM-modifiers (--variant) en enkelvoudige letters
        if "--" in cls or len(cls) < 3:
            continue
        # Skip klassen die beginnen met js- of data-
        if cls.startswith(("js-", "data-", "wp-")):
            continue
        if f".{cls}" not in css and f".{cls} " not in css and f".{cls}{{" not in css:
            missing.append(cls)

    if not missing:
        return []

    # Max 15 meldingen om de log leesbaar te houden
    warnings = [f"[WARN] CSS ontbreekt voor class: .{cls}" for cls in missing[:15]]
    if len(missing) > 15:
        warnings.append(f"[WARN] ... en {len(missing) - 15} andere classes zonder CSS-regel")
    for w in warnings:
        print(w)
    # Geen fixes toegepast — alleen rapporteren
    return []


def run_one_pass(site_dir: Path, html_to_repair: list[Path]) -> tuple[int, int]:
    """Voer één volledige reparatieronde uit. Geeft (file_fixes, site_fixes) terug."""
    total_file_fixes = 0
    for html_path in sorted(html_to_repair):
        if not html_path.exists():
            continue
        fixes = repair_file(html_path, site_dir)
        if fixes:
            print(f"[OK]  {html_path.name}: {len(fixes)} fix(es)")
            for fix in fixes:
                print(f"      - {fix}")
            total_file_fixes += len(fixes)
        else:
            print(f"[INFO] {html_path.name}: geen fixes nodig")

    print(f"\n[INFO] Site-brede fixes uitvoeren...")
    site_wide_fixes: list[str] = []
    for fn, label in [
        (fix_ref_files,              "ref-files"),
        (fix_js_truncation,          "js-truncatie"),
        (fix_sr_only,                "sr-only"),
        (fix_footer_year,            "footer-year"),
        (fix_hamburger_mismatch,     "hamburger"),
        (fix_portfolio_filter_class, "portfolio-filter"),
        (fix_lightbox_class,         "lightbox"),
        (fix_font_loading,           "font-loading"),
        (fix_favicon,                "favicon"),
        (fix_lazy_loading,           "lazy-loading"),
        (fix_footer,                 "footer"),
        (fix_nav_active_state,       "nav-active"),
        (fix_faq_accordion,          "faq-accordion"),
        (fix_low_contrast,           "low-contrast"),
        (fix_keuze_card_on_light_bg, "keuze-contrast"),
        (fix_card_contrast,          "card-contrast"),
        (check_css_coverage,         "css-coverage"),
    ]:
        result = fn(site_dir)
        site_wide_fixes.extend(result)
        if result:
            for fix in result:
                print(f"[OK]  {fix}")

    if not site_wide_fixes:
        print(f"[INFO] Geen site-brede fixes nodig")

    return total_file_fixes, len(site_wide_fixes)


def fix_screenshot_issues(site_dir: Path, screenshot_json: Path) -> int:
    """
    Lees visuele issues uit screenshot_validation.json en injecteer
    gerichte CSS-overrides via Claude. Geeft aantal fixes terug.
    """
    import os
    try:
        data = json.loads(screenshot_json.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] Kon screenshot JSON niet lezen: {e}")
        return 0

    all_issues = [i for page in data.get("results", []) for i in page.get("issues", [])]
    if not all_issues:
        print("[INFO] screenshot-repair: geen visuele issues om te fixen")
        return 0

    css_path = site_dir / "assets" / "css" / "style.css"
    if not css_path.exists():
        print("[WARN] screenshot-repair: style.css niet gevonden")
        return 0

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("[WARN] screenshot-repair: ANTHROPIC_API_KEY ontbreekt")
        return 0

    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    model  = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    current_css  = css_path.read_text(encoding="utf-8", errors="ignore")
    issues_text  = "\n".join(f"- {i}" for i in all_issues)

    prompt = f"""Je bent een CSS-expert die visuele problemen repareert in een gegenereerde website.

## Gevonden visuele problemen (via screenshot-analyse)
{issues_text}

## Huidige style.css (laatste 3000 tekens)
```css
{current_css[-3000:]}
```

## Opdracht
Schrijf ALLEEN de minimale CSS-overrides die deze problemen oplossen.
- Gebruik specifieke selectors die de bestaande stijlen overschrijven
- Geen uitleg, geen commentaar, alleen CSS-regels
- Begin direct met de CSS, geen code fences
- Maximaal 40 regels
- Als een probleem niet via CSS opgelost kan worden: sla het over"""

    try:
        response = client.messages.create(
            model=model, max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        css_fix = "".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()
        if not css_fix:
            print("[INFO] screenshot-repair: geen CSS-fixes gegenereerd")
            return 0

        # Verwijder eventuele code fences
        css_fix = re.sub(r'^```\w*\s*', '', css_fix, flags=re.MULTILINE)
        css_fix = re.sub(r'\s*```\s*$', '', css_fix, flags=re.MULTILINE)

        injection = f"\n\n/* ── Screenshot-repair fixes ───────────────────────────────── */\n{css_fix.strip()}\n"
        current = css_path.read_text(encoding="utf-8", errors="ignore")
        # Verwijder eerdere screenshot-repair block als die er al in zit
        current = re.sub(
            r'\n\n/\* ── Screenshot-repair fixes.*?(?=\n\n/\*|\Z)',
            '', current, flags=re.DOTALL
        )
        css_path.write_text(current + injection, encoding="utf-8")
        fix_count = len(all_issues)
        print(f"[OK]  screenshot-repair: {fix_count} issue(s) verwerkt in style.css ({len(css_fix)} tekens CSS)")
        return fix_count
    except Exception as e:
        print(f"[WARN] screenshot-repair Claude-aanroep mislukt: {e}")
        return 0


def main():
    parser = argparse.ArgumentParser(description="Repareer veelvoorkomende fouten in een gegenereerde site")
    parser.add_argument("--site-dir",        required=True, help="Pad naar de gegenereerde site-map")
    parser.add_argument("--validation-json", help="Optioneel: pad naar validate_generated_site JSON-rapport")
    parser.add_argument("--screenshot-json", help="Optioneel: pad naar screenshot_validation.json voor visuele fixes")
    parser.add_argument("--passes",          type=int, default=2,
                        help="Aantal reparatierondes (default: 2)")
    args = parser.parse_args()

    site_dir = Path(args.site_dir)
    if not site_dir.exists():
        print(f"[FAIL] Site-map niet gevonden: {site_dir}")
        sys.exit(1)

    # Bepaal welke bestanden per-file gerepareerd moeten worden
    if args.validation_json:
        try:
            report = json.loads(Path(args.validation_json).read_text(encoding="utf-8"))
            files_with_issues = {i["file"] for i in report.get("issues", [])
                                 if i["level"] == "FAIL" and i["file"]}
            files_with_issues |= {i["file"] for i in report.get("broken_links", []) if i["file"]}
            html_to_repair = [site_dir / f for f in files_with_issues if f.endswith(".html")]
        except Exception as e:
            print(f"[WARN] Kon validation JSON niet lezen: {e} — repareer alle HTML-bestanden")
            html_to_repair = list(site_dir.glob("*.html"))
    else:
        html_to_repair = list(site_dir.glob("*.html"))

    total_file = 0
    total_site  = 0
    for i in range(args.passes):
        if args.passes > 1:
            print(f"\n[INFO] ── Reparatieronde {i + 1}/{args.passes} ──────────────────────")
        ff, sf = run_one_pass(site_dir, html_to_repair)
        total_file += ff
        total_site  += sf
        if ff == 0 and sf == 0 and i > 0:
            print(f"[INFO] Geen wijzigingen in ronde {i + 1} — stoppen.")
            break

    # Screenshot-gebaseerde visuele fixes (apart van de standaard passes)
    screenshot_fixes = 0
    if args.screenshot_json:
        shot_path = Path(args.screenshot_json)
        if shot_path.exists():
            print(f"\n[INFO] Screenshot-repair uitvoeren op basis van {shot_path.name}...")
            screenshot_fixes = fix_screenshot_issues(site_dir, shot_path)
        else:
            print(f"[WARN] screenshot-json niet gevonden: {shot_path}")

    print(f"\n[OK]  Totaal: {total_file} bestandsfixes + {total_site} site-brede fixes + {screenshot_fixes} screenshot-fixes in {min(i+1, args.passes)} ronde(s)")


if __name__ == "__main__":
    main()
