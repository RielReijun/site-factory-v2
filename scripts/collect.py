import json
import re
import time
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup


PROSPECTS_FILE = Path("/workspace/data/prospects.json")
DATA_DIR = Path("/workspace/data")

CRAWL_DELAY = 0.5   # seconden tussen requests
MAX_PAGES   = 50    # maximaal aantal HTML-pagina's om te crawlen
MAX_ASSETS  = 150   # maximaal aantal assets te downloaden

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

# Extensies die we als asset behandelen (niet als HTML-pagina)
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


# ── Tekst extractie ───────────────────────────────────────────────────────────

def extract_visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


# ── Ophalen en opslaan ────────────────────────────────────────────────────────

def fetch(url: str, session: requests.Session) -> requests.Response | None:
    try:
        r = session.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        r.raise_for_status()
        return r
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
    base_domain = get_base_domain(start_url)
    session = requests.Session()

    page_queue:  deque[str] = deque([normalize_url(start_url)])
    asset_queue: deque[str] = deque()

    visited_pages:  set[str] = set()
    visited_assets: set[str] = set()

    pages_saved:  list[str] = []
    assets_saved: list[str] = []
    failed_urls:  list[str] = []

    all_texts: list[str] = []
    homepage_html = ""

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

        # Afbeeldingen
        for tag in soup.find_all("img", src=True):
            raw_asset_urls.append(urljoin(url, tag["src"]))

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
    print(f"[INFO] {len(asset_queue)} assets gevonden, downloaden (max {MAX_ASSETS})...")

    while asset_queue and len(assets_saved) < MAX_ASSETS:
        url = normalize_url(asset_queue.popleft())

        if url in visited_assets:
            continue
        visited_assets.add(url)

        rel_path = url_to_asset_path(url)
        if rel_path is None:
            print(f"[WARN] Asset-pad niet bruikbaar, overgeslagen: {url}")
            continue

        response = fetch(url, session)
        if response is None:
            failed_urls.append(url)
            continue

        save_file(target_dir / rel_path, response.content)
        assets_saved.append(url)
        print(f"[OK] Asset: {rel_path}")

        time.sleep(CRAWL_DELAY * 0.5)

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

    print(f"[OK] Pagina's gecrawld: {len(pages_saved)}")
    print(f"[OK] Assets gedownload: {len(assets_saved)}")
    if failed_urls:
        print(f"[WARN] Mislukt: {len(failed_urls)} URL(s)")

    return meta


# ── Prospects integratie ──────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def load_prospects() -> list:
    if not PROSPECTS_FILE.exists():
        raise FileNotFoundError(f"Prospects-bestand niet gevonden: {PROSPECTS_FILE}")
    return json.loads(PROSPECTS_FILE.read_text(encoding="utf-8"))


def save_prospects(prospects: list) -> None:
    PROSPECTS_FILE.write_text(
        json.dumps(prospects, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def get_next_pending_prospect(prospects: list) -> tuple[int, dict] | tuple[None, None]:
    for index, prospect in enumerate(prospects):
        if prospect.get("status", "pending") == "pending":
            return index, prospect
    return None, None


# ── Main ──────────────────────────────────────────────────────────────────────

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

    try:
        meta = crawl_site(url, target_dir)
        meta["company_name"] = name

        # Overschrijf meta.json met bedrijfsnaam erbij
        (target_dir / "meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        prospects[index]["status"]         = "collected"
        prospects[index]["collected_path"] = str(target_dir)
        save_prospects(prospects)
        print("[OK] Prospect status bijgewerkt naar 'collected'")

    except Exception as e:
        prospects[index]["status"] = "failed"
        prospects[index]["error"]  = str(e)
        save_prospects(prospects)
        print(f"[FAIL] Collect mislukt: {e}")
        raise


if __name__ == "__main__":
    main()
