"""
fetch_stock_photos.py — Haal gratis stockfoto's op via Pexels als fallback.

Draait na collect.py als er te weinig bruikbare afbeeldingen zijn.
Vereist PEXELS_API_KEY in .env (gratis aan te vragen op pexels.com/api).

Afbeeldingen worden opgeslagen in:
  /data/[slug]/assets/images/stock/stock_01.jpg  etc.

Ze worden automatisch opgepikt door save_image_manifest() in run_pipeline.py.
"""
import argparse
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

DATA_DIR       = Path("/workspace/data")
MIN_IMAGES     = 3    # minder dan dit → stockfoto's ophalen
MAX_PHOTOS     = 5    # maximaal op te halen stockfoto's
IMAGE_EXTS     = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}
MIN_IMAGE_SIZE = 2000  # bytes


# ── Helpers ───────────────────────────────────────────────────────────────────

def _count_usable_images(collected_path: Path) -> int:
    assets_dir = collected_path / "assets"
    if not assets_dir.exists():
        return 0
    count = 0
    for f in assets_dir.rglob("*"):
        if f.is_file() and f.suffix.lower() in IMAGE_EXTS:
            try:
                if f.stat().st_size >= MIN_IMAGE_SIZE:
                    count += 1
            except OSError:
                pass
    return count


def _extract_search_query(collected_path: Path, company_name: str) -> str:
    """Extraheer een relevante zoekterm uit briefing of structured_data."""
    # Probeer briefing: zoek business-type / dienst-termen
    briefing_path = collected_path / "briefing.md"
    if briefing_path.exists():
        text = briefing_path.read_text(encoding="utf-8", errors="ignore")
        # Zoek naar een branche/type aanduiding
        m = re.search(
            r'(?:Branche|Sector|Industrie|Bedrijfstype|Type bedrijf)[:\s]+([^\n]{5,60})',
            text, re.IGNORECASE,
        )
        if m:
            term = m.group(1).strip().rstrip(".,;")
            print(f"[INFO] Zoekterm uit briefing: '{term}'")
            return term

        # Alternatief: eerste zin van de samenvatting
        m2 = re.search(r'(?:##\s*Samenvatting|##\s*Bedrijfsprofiel)\s*\n+([^\n#]{20,120})', text)
        if m2:
            words = m2.group(1).strip().split()[:5]
            term = " ".join(words)
            print(f"[INFO] Zoekterm uit briefing-samenvatting: '{term}'")
            return term

    # Fallback: og:description of meta description uit structured_data
    sd_path = collected_path / "structured_data.json"
    if sd_path.exists():
        try:
            sd = json.loads(sd_path.read_text(encoding="utf-8"))
            desc = sd.get("meta", {}).get("description", "") or ""
            if len(desc) > 15:
                words = desc.split()[:5]
                term = " ".join(words)
                print(f"[INFO] Zoekterm uit meta description: '{term}'")
                return term
        except Exception:
            pass

    print(f"[INFO] Zoekterm: bedrijfsnaam '{company_name}'")
    return company_name


def fetch_photos(query: str, api_key: str, out_dir: Path,
                 max_photos: int = MAX_PHOTOS) -> list[Path]:
    """Haal foto's op van Pexels en sla op in out_dir."""
    url = (
        "https://api.pexels.com/v1/search"
        f"?query={urllib.parse.quote(query)}"
        f"&per_page={max_photos}"
        "&orientation=landscape"
        "&size=medium"
    )
    req = urllib.request.Request(url, headers={"Authorization": api_key})

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[FAIL] Pexels API-aanroep mislukt: {e}")
        return []

    photos = data.get("photos", [])
    if not photos:
        print(f"[WARN] Geen Pexels-resultaten voor query: '{query}'")
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    for i, photo in enumerate(photos[:max_photos]):
        # Gebruik 'large' (1280px breed) — kleiner dan 'original', groter dan 'medium'
        src_url = photo.get("src", {}).get("large", "")
        if not src_url:
            continue

        filename = f"stock_{i + 1:02d}.jpg"
        dest = out_dir / filename

        try:
            with urllib.request.urlopen(src_url, timeout=30) as r:
                dest.write_bytes(r.read())
            size_kb = dest.stat().st_size // 1024
            photographer = photo.get("photographer", "onbekend")
            print(f"[OK]  {filename}  ({size_kb}KB, foto: {photographer} via Pexels)")
            saved.append(dest)
            time.sleep(0.2)
        except Exception as e:
            print(f"[WARN] Foto {i + 1} downloaden mislukt: {e}")

    return saved


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Haal Pexels stockfoto's op als er te weinig afbeeldingen zijn."
    )
    parser.add_argument("--slug",    required=True, help="Data-slug (mapnaam in /data/)")
    parser.add_argument("--company", required=True, help="Bedrijfsnaam voor zoekterm")
    parser.add_argument("--force",   action="store_true",
                        help="Haal foto's op ook als er al genoeg zijn")
    args = parser.parse_args()

    api_key = os.getenv("PEXELS_API_KEY", "").strip()
    if not api_key:
        print("[WARN] PEXELS_API_KEY niet ingesteld — stockfoto's overgeslagen")
        raise SystemExit(0)

    collected_path = DATA_DIR / args.slug
    if not collected_path.exists():
        print(f"[FAIL] Data-map niet gevonden: {collected_path}")
        raise SystemExit(1)

    n_images = _count_usable_images(collected_path)
    print(f"[INFO] Bruikbare afbeeldingen gevonden: {n_images} (minimum: {MIN_IMAGES})")

    if n_images >= MIN_IMAGES and not args.force:
        print(f"[OK]  Genoeg afbeeldingen — Pexels-fallback niet nodig")
        raise SystemExit(0)

    query   = _extract_search_query(collected_path, args.company)
    out_dir = collected_path / "assets" / "images" / "stock"

    print(f"[INFO] Stockfoto's ophalen via Pexels: '{query}'")
    saved = fetch_photos(query, api_key, out_dir)

    if saved:
        print(f"[OK]  {len(saved)} stockfoto('s) opgeslagen in {out_dir}")
    else:
        print("[WARN] Geen stockfoto's opgeslagen")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
