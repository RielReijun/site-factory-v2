"""Haal logo's op voor bestaande Next.js sites."""
import json, re, shutil
from pathlib import Path
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import requests

HEADERS  = {"User-Agent": "Mozilla/5.0 (compatible; SiteFactoryBot/0.1)"}
IMG_EXTS = {'.png','.jpg','.jpeg','.svg','.webp','.gif','.ico'}


def find_logo(soup, base_url):
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            for item in (data if isinstance(data, list) else [data]):
                logo = item.get("logo") or item.get("image")
                if isinstance(logo, dict):
                    logo = logo.get("url") or logo.get("contentUrl")
                if isinstance(logo, str) and logo.startswith("http"):
                    return logo
        except Exception:
            pass
    for rel in (["apple-touch-icon"], ["shortcut icon"], ["icon"]):
        tag = soup.find("link", rel=rel)
        if tag and tag.get("href"):
            return urljoin(base_url, tag["href"])
    for img in soup.find_all("img"):
        src = img.get("src", "")
        attrs = " ".join([" ".join(img.get("class", [])), img.get("alt", ""), img.get("id", ""), src])
        if "logo" in attrs.lower() and src:
            return urljoin(base_url, src)
    header = soup.find("header")
    if header:
        img = header.find("img", src=True)
        if img:
            return urljoin(base_url, img["src"])
    return None


output    = Path("/workspace/output")
prospects = json.loads(Path("/workspace/data/prospects.json").read_text(encoding="utf-8"))

for p in prospects:
    name = p["name"]
    url  = p.get("url", "")
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    proj = output / f"{slug}-next"
    cp   = Path(p["collected_path"]) if p.get("collected_path") else None

    if not proj.exists() or not cp:
        continue
    if any((proj / "public" / f"logo{e}").exists() for e in IMG_EXTS):
        print(f"[SKIP] {name}: logo al aanwezig")
        continue

    # Lees HTML
    html = None
    for fname in ("raw.html", "index.html"):
        f = cp / fname
        if f.exists():
            html = f.read_text(encoding="utf-8", errors="ignore")
            break
    if not html:
        try:
            r = requests.get(url, headers=HEADERS, timeout=12, allow_redirects=True)
            html = r.text
        except Exception as e:
            print(f"[WARN] {name}: site ophalen mislukt — {e}")
            continue

    soup     = BeautifulSoup(html, "lxml")
    logo_url = find_logo(soup, url)
    if not logo_url:
        print(f"[WARN] {name}: geen logo gevonden")
        continue

    try:
        r = requests.get(logo_url, headers=HEADERS, timeout=12)
        r.raise_for_status()
        ext = Path(urlparse(logo_url).path).suffix.lower()
        if ext not in IMG_EXTS:
            ext = ".png"

        dest = proj / "public" / f"logo{ext}"
        dest.write_bytes(r.content)
        (cp / f"logo{ext}").write_bytes(r.content)
        print(f"[OK]  {name}: logo{ext} ({len(r.content):,} bytes)")
        print(f"      {logo_url}")
    except Exception as e:
        print(f"[WARN] {name}: download mislukt — {e}")
        print(f"       {logo_url}")

print("\nKlaar")
