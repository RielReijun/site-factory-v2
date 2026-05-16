import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

from pipeline_utils import (
    slugify, load_prospects, extract_visible_text,
    PROSPECTS_FILE, DATA_DIR,
)
from config import CRAWL_DELAY, MAX_PAGES, MAX_ASSETS

HEADERS = {
    "User-Agent": "SiteFactoryBot/0.1 (+internal use)"
}

# Alleen deze extensies behandelen we als downloadbare assets
ASSET_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".css", ".js",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pdf", ".zip",
    ".mp4", ".webm", ".mp3",
}

# Afbeeldingsextensies die ook van externe CDNs gedownload mogen worden
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# ── URL-hulpfuncties ──────────────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    """Verwijder fragment, normaliseer trailing slash voor deduplicatie."""
    url, _ = urldefrag(url)
    return url.rstrip("/") or url


def get_base_domain(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc.lower()


def is_same_domain(url: str, base_domain: str) -> bool:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    # Sta ook subdomein toe (www.x.nl en x.nl zijn hetzelfde)
    return domain == base_domain or domain.endswith("." + base_domain) or base_domain.endswith("." + domain)


def is_asset_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    ext = Path(path).suffix
    return ext in ASSET_EXTENSIONS


def url_to_page_path(url: str, base_url: str) -> Path:
    """Zet een pagina-URL om naar een relatief pad onder pages/."""
    parsed = urlparse(url)
    path = parsed.path.strip("/")

    if not path:
        return Path("pages/index.html")

    # Als het pad al een extensie heeft (bijv. .html, .php), gebruik die
    if Path(path).suffix:
        return Path("pages") / path

    # Anders: behandel als directory, sla op als index.html
    return Path("pages") / path / "index.html"


def url_to_asset_path(url: str) -> Path | None:
    """
    Zet een asset-URL om naar een relatief pad onder assets/.
    Geeft None terug als het pad geen bestandsnaam of herkende extensie heeft.
    """
    parsed = urlparse(url)
    rel = parsed.path.lstrip("/")
    if not rel:
        return None
    p = Path(rel)
    if not p.name or p.suffix.lower() not in ASSET_EXTENSIONS:
        return None
    return Path("assets") / rel


# ── Structured data extractie ────────────────────────────────────────────────

def extract_meta_tags(soup: BeautifulSoup) -> dict:
    """Haal relevante meta-tags op uit een pagina."""
    result = {}
    for tag in soup.find_all("meta"):
        name  = (tag.get("name") or tag.get("property") or "").lower().strip()
        content = tag.get("content", "").strip()
        if not name or not content:
            continue
        if name in ("description", "og:description", "twitter:description"):
            result.setdefault("description", content)
        elif name in ("og:title", "twitter:title"):
            result.setdefault("og_title", content)
        elif name in ("og:image", "twitter:image"):
            result.setdefault("og_image", content)
        elif name == "keywords":
            result["keywords"] = content
    return result


def extract_json_ld(soup: BeautifulSoup) -> list:
    """Extraheer alle JSON-LD blokken uit een pagina."""
    blocks = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
            blocks.append(data)
        except (json.JSONDecodeError, TypeError):
            pass
    return blocks


def extract_nav_structure(soup: BeautifulSoup) -> list[str]:
    """Haal de navigatiestructuur op (tekst van links in <nav>)."""
    items = []
    for nav in soup.find_all("nav"):
        for a in nav.find_all("a", href=True):
            text = a.get_text(strip=True)
            if text and len(text) < 60:
                items.append(text)
    seen = set()
    return [x for x in items if not (x in seen or seen.add(x))]


def extract_logo_url(soup: BeautifulSoup, base_url: str) -> str | None:
    """
    Zoek het logo van de site in volgorde van betrouwbaarheid:
    1. JSON-LD schema:Organization / schema:logo
    2. <link rel="apple-touch-icon"> of <link rel="icon">
    3. <img> met 'logo' in class, id of alt
    4. <img> met 'logo' in src-pad
    5. Eerste <img> in <header> of <nav>
    """
    from urllib.parse import urljoin

    # 1. JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                logo = item.get("logo") or item.get("image")
                if isinstance(logo, dict):
                    logo = logo.get("url") or logo.get("contentUrl")
                if isinstance(logo, str) and logo.startswith("http"):
                    return logo
        except Exception:
            pass

    # 2. Apple touch icon / favicon (vaak het beste logo-equivalent)
    for rel in ("apple-touch-icon", "shortcut icon", "icon"):
        tag = soup.find("link", rel=lambda r: r and rel in (r if isinstance(r, list) else [r]))
        if tag and tag.get("href"):
            return urljoin(base_url, tag["href"])

    # 3. <img class/id/alt bevat 'logo'>
    for img in soup.find_all("img"):
        src = img.get("src", "")
        cls = " ".join(img.get("class", []))
        alt = img.get("alt", "")
        img_id = img.get("id", "")
        if any("logo" in x.lower() for x in [cls, alt, img_id, src]):
            if src:
                return urljoin(base_url, src)

    # 4. Eerste <img> in <header>
    header = soup.find("header")
    if header:
        img = header.find("img")
        if img and img.get("src"):
            return urljoin(base_url, img["src"])

    return None


def extract_social_links(soup: BeautifulSoup) -> dict[str, str]:
    """Haal social media links op uit de pagina."""
    platforms = {
        "facebook":  r"facebook\.com/",
        "instagram": r"instagram\.com/",
        "linkedin":  r"linkedin\.com/",
        "twitter":   r"twitter\.com/|x\.com/",
        "youtube":   r"youtube\.com/",
        "tiktok":    r"tiktok\.com/",
    }
    result = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        for name, pattern in platforms.items():
            if name not in result and re.search(pattern, href, re.IGNORECASE):
                result[name] = href
    return result


def extract_contact_info(soup: BeautifulSoup) -> dict:
    """Haal telefoonnummer, e-mail en adres op uit de pagina."""
    result = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("tel:") and "phone" not in result:
            result["phone"] = href[4:].strip()
        elif href.startswith("mailto:") and "email" not in result:
            result["email"] = href[7:].split("?")[0].strip()
    address_tag = soup.find("address")
    if address_tag:
        result["address"] = address_tag.get_text(separator=" ", strip=True)
    return result


def fetch_sitemap_urls(start_url: str, session: requests.Session) -> list[str]:
    """Probeer sitemap.xml en sitemap_index.xml op te halen; geef gevonden URLs terug."""
    parsed   = urlparse(start_url)
    base     = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [
        f"{base}/sitemap.xml",
        f"{base}/sitemap_index.xml",
        f"{base}/sitemap-index.xml",
    ]
    urls: list[str] = []
    for sitemap_url in candidates:
        r = fetch(sitemap_url, session)
        if r is None or "xml" not in r.headers.get("content-type", ""):
            continue
        try:
            root = ET.fromstring(r.content)
            ns   = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            # sitemap index → haal child sitemaps op
            for loc in root.findall(".//sm:loc", ns):
                loc_url = loc.text.strip() if loc.text else ""
                if not loc_url:
                    continue
                if loc_url.endswith(".xml"):
                    sub = fetch(loc_url, session)
                    if sub and "xml" in sub.headers.get("content-type", ""):
                        try:
                            sub_root = ET.fromstring(sub.content)
                            for sub_loc in sub_root.findall(".//sm:loc", ns):
                                if sub_loc.text:
                                    urls.append(sub_loc.text.strip())
                        except ET.ParseError:
                            pass
                else:
                    urls.append(loc_url)
            print(f"[INFO] Sitemap gevonden: {sitemap_url} ({len(urls)} URLs)")
            break
        except ET.ParseError:
            continue
    return urls


# ── Ophalen en opslaan ────────────────────────────────────────────────────────

def fetch(url: str, session: requests.Session) -> requests.Response | None:
    import ssl as _ssl
    import requests.exceptions as _rex

    def _get(u, verify=True):
        return session.get(u, headers=HEADERS, timeout=20,
                           allow_redirects=True, verify=verify)

    try:
        r = _get(url)
        r.raise_for_status()
        return r
    except (_rex.SSLError, _ssl.SSLError):
        # Geen geldig SSL-certificaat: probeer zonder verificatie
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            r = _get(url, verify=False)
            r.raise_for_status()
            print(f"[WARN] SSL-verificatie uitgeschakeld voor: {url}")
            return r
        except Exception as e2:
            print(f"[WARN] Ophalen mislukt (ook zonder SSL): {url} — {e2}")
            return None
    except (_rex.ConnectionError, _rex.Timeout) as e:
        # https:// werkt niet: probeer http://
        if url.startswith("https://"):
            http_url = "http://" + url[8:]
            try:
                r = _get(http_url)
                r.raise_for_status()
                print(f"[WARN] Fallback naar HTTP: {http_url}")
                return r
            except Exception as e2:
                print(f"[WARN] Ophalen mislukt (https + http): {url} — {e2}")
                return None
        print(f"[WARN] Ophalen mislukt: {url} — {e}")
        return None
    except Exception as e:
        print(f"[WARN] Ophalen mislukt: {url} — {e}")
        return None


def save_file(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8", errors="ignore")
    else:
        path.write_bytes(content)


# ── Crawler ───────────────────────────────────────────────────────────────────

def crawl_site(start_url: str, target_dir: Path) -> dict:
    """
    Crawl alle pagina's en assets op hetzelfde domein als start_url.

    Slaat op onder:
      target_dir/pages/       — HTML-pagina's
      target_dir/assets/      — afbeeldingen, CSS, JS, fonts
      target_dir/raw.html     — homepage (backward compat)
      target_dir/text.txt     — gecombineerde zichtbare tekst
      target_dir/meta.json    — overzicht van de crawl

    Geeft een meta-dict terug.
    """
    session = requests.Session()

    # Controleer of de start-URL bereikbaar is; probeer http:// als https:// faalt
    probe = fetch(start_url, session)
    if probe is None and start_url.startswith("https://"):
        http_url = "http://" + start_url[8:]
        probe = fetch(http_url, session)
        if probe is not None:
            print(f"[WARN] Site gebruikt geen HTTPS, switched naar: {http_url}")
            start_url = http_url

    base_domain = get_base_domain(start_url)

    # ── Fase 0: sitemap ophalen ───────────────────────────────────────────────
    sitemap_urls = fetch_sitemap_urls(start_url, session)
    seed_urls = [normalize_url(start_url)]
    for u in sitemap_urls:
        nu = normalize_url(u)
        if is_same_domain(nu, base_domain) and not is_asset_url(nu):
            seed_urls.append(nu)
    seed_urls = list(dict.fromkeys(seed_urls))  # dedupliceer, behoud volgorde

    page_queue:  deque[str] = deque(seed_urls)
    asset_queue: deque[str] = deque()

    visited_pages:  set[str] = set()
    visited_assets: set[str] = set()
    external_image_queue: deque[str] = deque()

    pages_saved:  list[str] = []
    assets_saved: list[str] = []
    failed_urls:  list[str] = []

    all_texts: list[str] = []
    homepage_html = ""

    # Gestructureerde data — gevuld tijdens crawl
    structured: dict = {
        "meta_tags":    {},   # velden uit de homepage meta
        "json_ld":      [],   # alle JSON-LD blokken van alle pagina's
        "nav":          [],   # navigatiestructuur van de homepage
        "social_links": {},   # social media URLs
        "contact":      {},   # telefoon, e-mail, adres
    }

    print(f"[INFO] Start crawl: {start_url}")
    print(f"[INFO] Domein: {base_domain} | Max pagina's: {MAX_PAGES}")

    # ── Fase 1: pagina's crawlen ──────────────────────────────────────────────
    while page_queue and len(visited_pages) < MAX_PAGES:
        url = normalize_url(page_queue.popleft())

        if url in visited_pages:
            continue
        visited_pages.add(url)

        print(f"[INFO] Pagina ophalen ({len(visited_pages)}/{MAX_PAGES}): {url}")
        response = fetch(url, session)

        if response is None:
            failed_urls.append(url)
            continue

        content_type = response.headers.get("content-type", "")
        if "html" not in content_type:
            print(f"[WARN] Geen HTML-pagina, overgeslagen: {url} ({content_type})")
            continue

        html = response.text
        soup = BeautifulSoup(html, "lxml")

        # Sla pagina op
        rel_path = url_to_page_path(url, start_url)
        save_file(target_dir / rel_path, html)
        pages_saved.append(url)

        # Homepage apart opslaan voor backward compat
        if not homepage_html:
            homepage_html = html
            save_file(target_dir / "raw.html", html)
            # Homepage-specifieke extractie (eenmalig)
            structured["meta_tags"]    = extract_meta_tags(soup)
            structured["nav"]          = extract_nav_structure(soup)
            structured["social_links"] = extract_social_links(soup)
            structured["contact"]      = extract_contact_info(soup)

        # JSON-LD van elke pagina meenemen
        page_ld = extract_json_ld(soup)
        if page_ld:
            structured["json_ld"].extend(page_ld)

        # Contact info aanvullen vanuit andere pagina's (tel/mail kunnen elders staan)
        if not structured["contact"].get("phone") or not structured["contact"].get("email"):
            extra = extract_contact_info(soup)
            for key in ("phone", "email", "address"):
                if extra.get(key) and not structured["contact"].get(key):
                    structured["contact"][key] = extra[key]

        # Verzamel zichtbare tekst
        page_text = extract_visible_text(html)
        parsed_path = urlparse(url).path or "/"
        all_texts.append(f"=== Pagina: {parsed_path} ===\n{page_text}")

        # Vind interne links voor verdere crawl
        for tag in soup.find_all("a", href=True):
            href = urljoin(url, tag["href"])
            href_clean = normalize_url(href)
            parsed = urlparse(href_clean)

            if not is_same_domain(href_clean, base_domain):
                continue
            if parsed.scheme not in ("http", "https"):
                continue

            if is_asset_url(href_clean):
                if href_clean not in visited_assets:
                    asset_queue.append(href_clean)
            elif href_clean not in visited_pages:
                page_queue.append(href_clean)

        # Vind assets op deze pagina — alleen expliciete asset-typen, geen generieke <link>
        raw_asset_urls = []

        # Afbeeldingen — ook data-src (lazy loading) en eerste srcset-URL
        for tag in soup.find_all("img"):
            for attr in ("src", "data-src"):
                val = tag.get(attr)
                if val:
                    img_url = normalize_url(urljoin(url, val.split("?")[0]))
                    ext = Path(urlparse(img_url).path).suffix.lower()
                    if ext in IMAGE_EXTENSIONS:
                        if is_same_domain(img_url, base_domain):
                            if img_url not in visited_assets:
                                asset_queue.append(img_url)
                        else:
                            if img_url not in visited_assets:
                                external_image_queue.append(img_url)

        # Stylesheets (alleen <link rel="stylesheet">)
        for tag in soup.find_all("link", rel=True, href=True):
            rels = tag.get("rel", [])
            if isinstance(rels, list):
                rels = [r.lower() for r in rels]
            else:
                rels = [rels.lower()]
            if "stylesheet" in rels:
                raw_asset_urls.append(urljoin(url, tag["href"]))

        # JavaScript
        for tag in soup.find_all("script", src=True):
            raw_asset_urls.append(urljoin(url, tag["src"]))

        # Video/audio
        for tag_name in ("source", "video", "audio"):
            for tag in soup.find_all(tag_name, src=True):
                raw_asset_urls.append(urljoin(url, tag["src"]))

        for asset_url in raw_asset_urls:
            asset_clean = normalize_url(asset_url)
            if is_same_domain(asset_clean, base_domain) and asset_clean not in visited_assets:
                asset_queue.append(asset_clean)

        time.sleep(CRAWL_DELAY)

    # ── Fase 2: assets downloaden ─────────────────────────────────────────────
    total_queued = len(asset_queue) + len(external_image_queue)
    print(f"[INFO] {total_queued} assets gevonden ({len(external_image_queue)} externe afbeeldingen), downloaden (max {MAX_ASSETS})...")

    def _download_asset(asset_url: str) -> bool:
        nonlocal assets_saved
        if asset_url in visited_assets:
            return False
        visited_assets.add(asset_url)
        rel_path = url_to_asset_path(asset_url)
        if rel_path is None:
            print(f"[WARN] Asset-pad niet bruikbaar, overgeslagen: {asset_url}")
            return False
        response = fetch(asset_url, session)
        if response is None:
            failed_urls.append(asset_url)
            return False
        save_file(target_dir / rel_path, response.content)
        assets_saved.append(asset_url)
        print(f"[OK] Asset: {rel_path}")
        time.sleep(CRAWL_DELAY * 0.5)
        return True

    while asset_queue and len(assets_saved) < MAX_ASSETS:
        _download_asset(normalize_url(asset_queue.popleft()))

    while external_image_queue and len(assets_saved) < MAX_ASSETS:
        _download_asset(normalize_url(external_image_queue.popleft()))

    # ── Tekst en meta opslaan ─────────────────────────────────────────────────
    combined_text = "\n\n".join(all_texts)
    save_file(target_dir / "text.txt", combined_text)

    title = None
    if homepage_html:
        soup = BeautifulSoup(homepage_html, "lxml")
        if soup.title and soup.title.string:
            title = soup.title.string.strip()

    meta = {
        "url": start_url,
        "domain": base_domain,
        "title": title,
        "pages_crawled": len(pages_saved),
        "assets_downloaded": len(assets_saved),
        "failed_urls": failed_urls,
        "pages": pages_saved,
        "assets": assets_saved,
    }

    save_file(target_dir / "meta.json", json.dumps(meta, indent=2, ensure_ascii=False))

    # Logo opsporen en downloaden
    if homepage_html:
        _hp_soup = BeautifulSoup(homepage_html, "lxml")
        logo_url = extract_logo_url(_hp_soup, start_url)
        if logo_url:
            try:
                r = session.get(logo_url, headers=HEADERS, timeout=10)
                r.raise_for_status()
                ext = Path(urlparse(logo_url).path).suffix.lower() or ".png"
                if ext not in {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}:
                    ext = ".png"
                logo_path = target_dir / f"logo{ext}"
                logo_path.write_bytes(r.content)
                structured["logo"] = str(logo_path.relative_to(target_dir))
                print(f"[OK] Logo opgeslagen: logo{ext} ({len(r.content)} bytes) — {logo_url}")
            except Exception as e:
                print(f"[WARN] Logo downloaden mislukt: {e}")

    # Structured data opslaan
    save_file(
        target_dir / "structured_data.json",
        json.dumps(structured, indent=2, ensure_ascii=False),
    )
    sd_summary = []
    if structured["meta_tags"]:
        sd_summary.append(f"meta-tags: {list(structured['meta_tags'].keys())}")
    if structured["json_ld"]:
        sd_summary.append(f"JSON-LD blokken: {len(structured['json_ld'])}")
    if structured["contact"]:
        sd_summary.append(f"contact: {list(structured['contact'].keys())}")
    if structured["social_links"]:
        sd_summary.append(f"socials: {list(structured['social_links'].keys())}")
    if sd_summary:
        print(f"[OK] Structured data: {' | '.join(sd_summary)}")

    # Business facts JSON — harde feiten voor generator/validator
    # Wordt gebruikt door validate_generated_content.py en generate prompts
    contact = structured.get("contact", {})
    facts: dict = {
        "name":          "",  # wordt ingevuld via brief of prospect.name
        "phone":         contact.get("phone", ""),
        "email":         contact.get("email", ""),
        "address":       contact.get("address", ""),
        "opening_hours": [],  # wordt ingevuld vanuit JSON-LD indien beschikbaar
        "services":      [],  # uit nav-structuur of JSON-LD
        "prices":        [],  # uit crawled tekst, indien aanwezig
        "booking_url":   "",
        "socials":       structured.get("social_links", {}),
        "claims_allowed":   [],  # feiten die bewezen zijn
        "claims_forbidden": [],  # niet te parafraseren / verzinnen
    }
    # Haal openingstijden uit JSON-LD indien beschikbaar
    # JSON-LD blokken kunnen dicts of lijsten zijn — alleen dicts verwerken
    for block in structured.get("json_ld", []):
        if not isinstance(block, dict):
            continue
        if block.get("openingHoursSpecification"):
            facts["opening_hours"] = block["openingHoursSpecification"]
            break
        if block.get("openingHours"):
            facts["opening_hours"] = block["openingHours"]
            break
    # Services uit nav
    nav = structured.get("nav", [])
    if nav:
        facts["services"] = nav[:10]

    save_file(
        target_dir / "facts.json",
        json.dumps(facts, indent=2, ensure_ascii=False),
    )
    print(f"[OK] Facts JSON opgeslagen (telefoon: {bool(facts['phone'])}, email: {bool(facts['email'])}, adres: {bool(facts['address'])})")

    print(f"[OK] Pagina's gecrawld: {len(pages_saved)}")
    print(f"[OK] Assets gedownload: {len(assets_saved)}")
    if failed_urls:
        print(f"[WARN] Mislukt: {len(failed_urls)} URL(s)")

    return meta


# ── Prospects integratie ──────────────────────────────────────────────────────

def save_prospects(prospects: list) -> None:
    # Niet meer bulk-schrijven; gebruik update_prospect per item voor thread-safety
    PROSPECTS_FILE.write_text(
        json.dumps(prospects, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def get_next_pending_prospect(prospects: list) -> tuple[int, dict] | tuple[None, None]:
    from prospects_utils import is_queueable
    for index, prospect in enumerate(prospects):
        if is_queueable(prospect):
            return index, prospect
    return None, None


# ── Main ──────────────────────────────────────────────────────────────────────

def _extract_brand_colors(url: str, target_dir: Path) -> None:
    """Extraheer brand-kleuren via Playwright computed styles.

    Werkt voor JS-heavy sites (React/Next.js) waar CSS gebundeld is in JS.
    Sla op als brand_colors.json — heeft voorrang op briefing-kleurextractie.
    """
    out_path = target_dir / "brand_colors.json"
    if out_path.exists():
        return
    try:
        import subprocess, sys, json as _json
        helper = Path(__file__).parent / "extract_brand_colors.py"
        result = subprocess.run(
            [sys.executable, str(helper), "--url", url, "--out", str(out_path)],
            capture_output=True, text=True, timeout=45,
        )
        if result.returncode == 0 and out_path.exists():
            data = _json.loads(out_path.read_text())
            print(f"[OK]  Brand-kleur geëxtraheerd: {data.get('primary')} (via Playwright)")
        else:
            print(f"[WARN] Brand-kleurextractie mislukt: {result.stderr[:150]}")
    except Exception as e:
        print(f"[WARN] Brand-kleurextractie mislukt: {e}")


def _take_original_screenshot(url: str, target_dir: Path) -> None:
    """Maak een screenshot van de originele site voor de voor/na vergelijking."""
    out_path = target_dir / "original_screenshot.png"
    if out_path.exists():
        print(f"[INFO] original_screenshot.png bestaat al — overgeslagen")
        return
    try:
        import subprocess, sys
        script = (
            "from playwright.sync_api import sync_playwright\n"
            "with sync_playwright() as p:\n"
            "    b = p.chromium.launch()\n"
            f"    page = b.new_page(viewport={{'width':1280,'height':720}})\n"
            f"    page.goto({url!r}, wait_until='domcontentloaded', timeout=20000)\n"
            "    page.wait_for_timeout(1500)\n"
            f"    page.screenshot(path={str(out_path)!r}, full_page=False)\n"
            "    b.close()\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=45,
        )
        if result.returncode == 0:
            size_kb = out_path.stat().st_size // 1024
            print(f"[OK]  Original screenshot opgeslagen ({size_kb}KB)")
        else:
            print(f"[WARN] Screenshot originele site mislukt: {result.stderr[:200]}")
    except Exception as e:
        print(f"[WARN] Screenshot originele site mislukt: {e}")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", help="Naam van de prospect om te (her)crawlen")
    parser.add_argument("--force", action="store_true", help="Crawl opnieuw ook als status al 'collected' is")
    args = parser.parse_args()

    prospects = load_prospects()

    if args.name:
        index, prospect = None, None
        for i, p in enumerate(prospects):
            if p.get("name", "").strip().lower() == args.name.strip().lower():
                if not args.force and p.get("status") == "collected":
                    print(f"[INFO] '{args.name}' is al gecollect. Gebruik --force om opnieuw te crawlen.")
                    return
                index, prospect = i, p
                break
        if prospect is None:
            print(f"[FAIL] Prospect '{args.name}' niet gevonden.")
            return
    else:
        index, prospect = get_next_pending_prospect(prospects)

    if prospect is None:
        print("[INFO] Geen pending prospects gevonden.")
        return

    name = prospect.get("name")
    url  = prospect.get("url")

    print(f"[INFO] Verwerk prospect: {name} ({url})")

    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path
    slug = slugify(domain)
    target_dir = DATA_DIR / slug
    target_dir.mkdir(parents=True, exist_ok=True)

    from config import MINIMUM_PAGES, MINIMUM_TEXT_CHARS

    try:
        meta = crawl_site(url, target_dir)
        meta["company_name"] = name

        # Overschrijf meta.json met bedrijfsnaam erbij
        (target_dir / "meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        # ── Referentiesite crawlen (als opgegeven) ───────────────────────────
        reference_url = prospect.get("reference_url", "").strip()
        if reference_url:
            ref_dir = target_dir / "reference"
            ref_dir.mkdir(parents=True, exist_ok=True)
            print(f"[INFO] Referentiesite crawlen: {reference_url}")
            try:
                ref_meta = crawl_site(reference_url, ref_dir)
                ref_meta["is_reference"] = True
                (ref_dir / "meta.json").write_text(
                    json.dumps(ref_meta, indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )
                print(f"[OK]  Referentiesite gecrawld: {ref_meta.get('pages_crawled', 0)} pagina('s)")
            except Exception as ref_err:
                print(f"[WARN] Referentiesite crawl mislukt: {ref_err} — pipeline gaat door")

        # ── Kwaliteitscheck: genoeg data om verder te gaan? ──────────────────
        pages_crawled = meta.get("pages_crawled", 0)
        text_file     = target_dir / "text.txt"
        text_chars    = len(text_file.read_text(encoding="utf-8", errors="ignore")) if text_file.exists() else 0

        if pages_crawled < MINIMUM_PAGES or text_chars < MINIMUM_TEXT_CHARS:
            reason = (
                f"Te weinig data: {pages_crawled} pagina('s) gecrawld, "
                f"{text_chars} tekens tekst (minimum: {MINIMUM_PAGES} pagina, {MINIMUM_TEXT_CHARS} tekens). "
                "Pipeline gestopt om verzonnen content te voorkomen."
            )
            print(f"[FAIL] {reason}")
            from prospects_utils import update_prospect
            update_prospect(name, status="insufficient", error=reason)
            sys.exit(1)

        from prospects_utils import update_prospect
        update_prospect(name, status="collected", collected_path=str(target_dir))
        print("[OK] Prospect status bijgewerkt naar 'collected'")

        # ── Brand-kleuren extracteren via Playwright (computed styles) ───────
        _extract_brand_colors(url, target_dir)

        # ── Screenshot van de originele site (voor/na vergelijking) ─────────
        _take_original_screenshot(url, target_dir)

    except SystemExit:
        raise
    except Exception as e:
        from prospects_utils import update_prospect
        update_prospect(name, status="failed", error=str(e))
        print(f"[FAIL] Collect mislukt: {e}")
        raise


if __name__ == "__main__":
    main()
