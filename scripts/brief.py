import argparse
import json
import os
from pathlib import Path

from pipeline_utils import get_claude_client

from pipeline_utils import load_prospects, read_text_file, get_model, PROSPECTS_FILE


from config import MIN_IMAGE_SIZE, MAX_IMAGES_BRIEF, MAX_SITE_TEXT_CHARS, MAX_CSS_CHARS, MAX_RESEARCH_CHARS, MAX_REF_TEXT_CHARS, MAX_REF_CSS_CHARS

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}


def list_images(collected_path: Path, max_images: int = MAX_IMAGES_BRIEF) -> list[str]:
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


def extract_css(collected_path: Path, max_chars: int = MAX_CSS_CHARS) -> str:
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




def build_structured_section(sd: dict) -> str:
    """Zet structured_data.json om naar een leesbare sectie voor de briefing-prompt."""
    parts = []

    meta = sd.get("meta_tags", {})
    if meta.get("description"):
        parts.append(f"**Meta description:** {meta['description']}")
    if meta.get("og_title"):
        parts.append(f"**OG titel:** {meta['og_title']}")
    if meta.get("keywords"):
        parts.append(f"**Keywords:** {meta['keywords']}")

    contact = sd.get("contact", {})
    if contact:
        c_parts = []
        if contact.get("phone"):   c_parts.append(f"tel: {contact['phone']}")
        if contact.get("email"):   c_parts.append(f"email: {contact['email']}")
        if contact.get("address"): c_parts.append(f"adres: {contact['address']}")
        if c_parts:
            parts.append("**Contactgegevens:** " + " | ".join(c_parts))

    social = sd.get("social_links", {})
    if social:
        parts.append("**Social media:** " + ", ".join(social.keys()))

    nav = sd.get("nav", [])
    if nav:
        parts.append("**Navigatie:** " + " > ".join(nav[:12]))

    json_ld = sd.get("json_ld", [])
    if json_ld:
        # Geef de eerste 3 blokken, gekort
        ld_text = json.dumps(json_ld[:3], ensure_ascii=False, indent=2)
        if len(ld_text) > 3000:
            ld_text = ld_text[:3000] + "\n// ... afgekapt"
        parts.append(f"**Structured data (JSON-LD):**\n```json\n{ld_text}\n```")

    if not parts:
        return ""
    return "## Gestructureerde bedrijfsdata (betrouwbare bron)\n" + "\n".join(parts) + "\n"


def build_prompt(company_name: str, url: str, meta: dict, site_text: str,
                 css_text: str = "", research_text: str = "", images: list[str] | None = None,
                 structured_data: dict | None = None,
                 reference_text: str = "", reference_url: str = "",
                 reference_css: str = "") -> str:
    trimmed_text  = site_text[:MAX_SITE_TEXT_CHARS]

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
{research_text[:MAX_RESEARCH_CHARS]}
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

    structured_section = ""
    if structured_data:
        structured_section = build_structured_section(structured_data)

    reference_section = ""
    if reference_text.strip():
        ref_css_block = ""
        if reference_css.strip():
            ref_css_block = f"\n\nCSS-stijlinspiratie van referentiesite:\n```css\n{reference_css[:MAX_REF_CSS_CHARS]}\n```"
        reference_section = f"""
## Referentiesite ter inspiratie
URL: {reference_url or "onbekend"}
Gebruik de stijl, structuur en toon van deze site als designinspiratie — niet de inhoud.
{reference_text[:MAX_REF_TEXT_CHARS]}{ref_css_block}
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
{structured_section}{css_section}{research_section}{reference_section}{image_section}
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
    model = get_model()

    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY ontbreekt in environment")

    client = get_claude_client(api_key)

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

    sd_path = target_dir / "structured_data.json"
    structured_data = json.loads(sd_path.read_text(encoding="utf-8")) if sd_path.exists() else None

    # Referentiesite tekst + CSS (optioneel)
    ref_dir = target_dir / "reference"
    reference_text = read_text_file(ref_dir / "text.txt") if ref_dir.exists() else ""
    reference_url  = prospect.get("reference_url", "")
    reference_css  = ""
    if ref_dir.exists():
        ref_css_dir = ref_dir / "assets" / "css"
        if ref_css_dir.exists():
            parts, total = [], 0
            for f in sorted(ref_css_dir.glob("*.css")):
                try:
                    c = f.read_text(encoding="utf-8", errors="ignore")[:5000]
                    parts.append(c)
                    total += len(c)
                    if total >= 5000:
                        break
                except Exception:
                    pass
            reference_css = "\n".join(parts)

    if css_text:
        print(f"[INFO] CSS meegegeven: {len(css_text)} tekens")
    if research_text:
        print(f"[INFO] Research meegegeven: {len(research_text)} tekens")
    if images:
        print(f"[INFO] Afbeeldingen meegegeven: {len(images)}")
    if structured_data:
        ld_count = len(structured_data.get("json_ld", []))
        print(f"[INFO] Structured data meegegeven (JSON-LD blokken: {ld_count})")
    if reference_text:
        print(f"[INFO] Referentiesite meegegeven: {len(reference_text)} tekens tekst")

    prompt = build_prompt(company_name, url, meta, site_text,
                          css_text=css_text, research_text=research_text, images=images,
                          structured_data=structured_data,
                          reference_text=reference_text, reference_url=reference_url,
                          reference_css=reference_css)

    from pipeline_utils import with_retry
    from config import RETRY_BASE_WAIT, MAX_RATE_RETRIES

    print(f"[INFO] Genereer briefing voor: {company_name}")
    print(f"[INFO] Model: {model}")

    chunks: list[str] = []
    final = None

    def _stream():
        nonlocal final
        chunks.clear()
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

    with_retry(_stream, max_retries=MAX_RATE_RETRIES, base_wait=RETRY_BASE_WAIT, label="brief")

    briefing = "".join(chunks).strip()

    if not briefing:
        raise RuntimeError("Lege briefing teruggekregen van Anthropic")

    briefing_path.write_text(briefing, encoding="utf-8")

    from datetime import datetime, timezone
    response_meta = {
        "model":      model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
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

    from prospects_utils import update_prospect
    update_prospect(args.name, briefing_status="done", briefing_path=str(briefing_path))

    print(f"[OK] Briefing opgeslagen in: {briefing_path} ({len(briefing)} tekens)")
    print(f"[OK] Response metadata opgeslagen in: {briefing_meta_path}")
    print(f"[INFO] stop_reason: {response_meta['stop_reason']}")
    print("[OK] Prospect briefing_status bijgewerkt naar 'done'")


if __name__ == "__main__":
    main()
