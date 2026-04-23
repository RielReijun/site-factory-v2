import argparse
import json
import os
from pathlib import Path

from anthropic import Anthropic


PROSPECTS_FILE = Path("/workspace/data/prospects.json")


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}
MIN_IMAGE_SIZE = 2000  # bytes — sla kleine iconen/favicons over


def list_images(collected_path: Path, max_images: int = 30) -> list[str]:
    """Geef paden terug van gedownloade afbeeldingen, relatief aan collected_path."""
    assets_dir = collected_path / "assets"
    if not assets_dir.exists():
        return []
    images = []
    for f in sorted(assets_dir.rglob("*")):
        if not f.is_file():
            continue
        if f.suffix.lower() not in IMAGE_EXTS:
            continue
        try:
            if f.stat().st_size < MIN_IMAGE_SIZE:
                continue
        except OSError:
            continue
        images.append(str(f.relative_to(collected_path)))
    return images[:max_images]


def extract_css(collected_path: Path, max_chars: int = 10000) -> str:
    """Combineer CSS-bestanden uit de gecrawlde assets tot een beperkt overzicht."""
    css_dir = collected_path / "assets" / "css"
    if not css_dir.exists():
        return ""
    parts = []
    total = 0
    for css_file in sorted(css_dir.glob("*.css")):
        try:
            content = css_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(content) > remaining:
            content = content[:remaining] + "\n/* ... afgekapt */"
        parts.append(f"/* === {css_file.name} === */\n{content}")
        total += len(content)
        if total >= max_chars:
            break
    return "\n\n".join(parts)


def load_prospects():
    return json.loads(PROSPECTS_FILE.read_text(encoding="utf-8"))


def save_prospects(prospects):
    PROSPECTS_FILE.write_text(
        json.dumps(prospects, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def find_prospect(prospects, name=None, force=False):
    if name:
        for index, prospect in enumerate(prospects):
            if prospect.get("name", "").strip().lower() == name.strip().lower():
                if prospect.get("status") != "collected":
                    raise RuntimeError(f"Prospect '{name}' is nog niet collected.")
                if not force and prospect.get("briefing_status") == "done":
                    raise RuntimeError(
                        f"Prospect '{name}' heeft al een briefing. Gebruik --force om opnieuw te genereren."
                    )
                return index, prospect
        raise RuntimeError(f"Prospect '{name}' niet gevonden.")

    for index, prospect in enumerate(prospects):
        if prospect.get("status") == "collected":
            if force:
                return index, prospect
            if prospect.get("briefing_status") != "done":
                return index, prospect

    return None, None


def read_text_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def build_prompt(company_name: str, url: str, meta: dict, site_text: str,
                 css_text: str = "", research_text: str = "", images: list[str] | None = None) -> str:
    trimmed_text  = site_text[:20000]

    css_section = ""
    if css_text.strip():
        css_section = f"""
## CSS van de huidige site (gebruik voor kleur, font en stijlextractie)
Extraheer hieruit: exacte hex-kleuren, font-families, border-radius, spacing-patronen, schaduwen en alle merkgebonden visuele keuzes.

```css
{css_text}
```
"""

    research_section = ""
    if research_text.strip():
        research_section = f"""
## Marktonderzoek en concurrentieanalyse
{research_text[:15000]}
"""

    image_section = ""
    if images:
        image_list = "\n".join(f"- {img}" for img in images)
        image_section = f"""
## Gedownloade afbeeldingen van de originele site
De volgende bestanden zijn beschikbaar. Verwijs er in de briefing naar waar relevant.
Geef aan op welke pagina's en secties ze logisch passen.

{image_list}
"""

    return f"""
Je bent een senior webstrateeg en UX-expert.

Schrijf een compacte markdown briefing voor een nieuwe website voor {company_name}.
Deze briefing wordt door een AI gebruikt om direct HTML/CSS/JS te genereren.

Regels:
- Maximaal 200 regels totaal
- Maximaal 12 regels per sectie — gebruik bullets, geen alinea's
- Geen herhaling, geen opvulling, geen inleidingen
- Verzin GEEN feiten — markeer onzekerheden met AANNEMELIJK
- Geen percentages, ROI-claims of statistieken tenzij aantoonbaar

## Input

**Bedrijf:** {company_name}
**URL:** {url}
**Paginatitel:** {meta.get("title", "onbekend")}
{css_section}{research_section}{image_section}
## Websitetekst
{trimmed_text}

---

Schrijf de briefing nu met EXACT deze headings in EXACT deze volgorde — sla er geen over:

# Website brief voor Claude

## Project
Max 8 bullets. Wat doet het bedrijf, welke diensten/producten, locatie, bijzonderheden, doel van de nieuwe site.

## Doelgroep
Max 6 bullets. Wie zijn de bezoekers, wat willen ze, wat zijn hun twijfels of drempels.

## Wat er mis of zwak is aan de huidige site
Max 8 bullets. Concrete problemen: structuur, conversie, vertrouwen, UX, techniek.

## Wat behouden moet blijven
Max 6 bullets. Stijl, content, functionaliteiten of merkwaarden die goed werken.

## Gewenste verbeteringen
Max 8 bullets. Wat moet beter: structuur, conversie, vertrouwen, UX, SEO.

## Tone of voice
Max 6 bullets. Register, woordkeuze (wel/niet), toon, 1-2 voorbeeldzinnen.

## Designrichting
Max 12 bullets. Gebruik exacte hex-codes uit de CSS waar beschikbaar:
- Primaire kleur: #...
- Secundaire kleur: #...
- Achtergrond: #...
- Tekst: #...
- Heading font: naam + gewicht
- Body font: naam + gewicht
- Stijl: [woorden die de uitstraling beschrijven]
- Buttons: kleur, radius, stijl
- Sfeer: [3-5 woorden]

## Aanbevolen paginastructuur
Per pagina één regel: `bestandsnaam.html — doel in max 10 woorden`

## Belangrijkste secties op de homepage
Max 8 secties. Per sectie één regel: `Sectienaam — inhoud/doel in max 10 woorden`

## Contentregels
Max 6 bullets. Schrijfregels, CTAs (exacte tekst + kleur), verboden termen.

## Wat niet verzonnen mag worden
Max 5 bullets. Claims, cijfers, reviews of informatie die alleen van de klant mag komen.

## Technische eisen
Max 6 bullets. SEO, performance, toegankelijkheid, integraties, CMS-wensen.

## Samenvatting in 1 alinea
Één alinea (max 3 zinnen): wat wordt gebouwd, voor wie, waarom beter dan nu.
"""
    

def extract_response_text(response) -> str:
    chunks = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            chunks.append(block.text)
    return "\n".join(chunks).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", help="Naam van de prospect die je wilt verwerken")
    parser.add_argument("--force", action="store_true", help="Genereer briefing opnieuw, ook als die al bestaat")
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY ontbreekt in environment")

    client = Anthropic(api_key=api_key)

    prospects = load_prospects()
    index, prospect = find_prospect(prospects, name=args.name, force=args.force)

    if prospect is None:
        print("[INFO] Geen geschikte prospect gevonden.")
        return

    company_name = prospect.get("name", "Onbekend bedrijf")
    url = prospect.get("url")
    collected_path = prospect.get("collected_path")

    if not collected_path:
        raise RuntimeError("collected_path ontbreekt in prospect")

    target_dir = Path(collected_path)
    meta_path = target_dir / "meta.json"
    text_path = target_dir / "text.txt"
    briefing_path = target_dir / "briefing.md"
    briefing_meta_path = target_dir / "briefing_meta.json"

    meta      = json.loads(read_text_file(meta_path)) if meta_path.exists() else {}
    site_text = read_text_file(text_path)
    css_text  = extract_css(target_dir)
    research_text = read_text_file(briefing_path.parent / "research.md")
    images    = list_images(target_dir)

    if css_text:
        print(f"[INFO] CSS meegegeven: {len(css_text)} tekens")
    if research_text:
        print(f"[INFO] Research meegegeven: {len(research_text)} tekens")
    if images:
        print(f"[INFO] Afbeeldingen meegegeven: {len(images)}")

    prompt = build_prompt(company_name, url, meta, site_text,
                          css_text=css_text, research_text=research_text, images=images)

    print(f"[INFO] Genereer briefing voor: {company_name}")
    print(f"[INFO] Model: {model}")

    chunks = []
    with client.messages.stream(
        model=model,
        max_tokens=5000,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            chunks.append(text)
        print()
        final = stream.get_final_message()

    briefing = "".join(chunks).strip()

    if not briefing:
        raise RuntimeError("Lege briefing teruggekregen van Anthropic")

    briefing_path.write_text(briefing, encoding="utf-8")

    response_meta = {
        "model": model,
        "stop_reason": getattr(final, "stop_reason", None),
        "usage": {
            "input_tokens":  getattr(final.usage, "input_tokens",  None) if getattr(final, "usage", None) else None,
            "output_tokens": getattr(final.usage, "output_tokens", None) if getattr(final, "usage", None) else None,
        },
    }

    briefing_meta_path.write_text(
        json.dumps(response_meta, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    prospects[index]["briefing_status"] = "done"
    prospects[index]["briefing_path"] = str(briefing_path)
    save_prospects(prospects)

    print(f"[OK] Briefing opgeslagen in: {briefing_path} ({len(briefing)} tekens)")
    print(f"[OK] Response metadata opgeslagen in: {briefing_meta_path}")
    print(f"[INFO] stop_reason: {response_meta['stop_reason']}")
    print("[OK] Prospect briefing_status bijgewerkt naar 'done'")


if __name__ == "__main__":
    main()
