"""
research.py — Identificeer concurrenten, crawl hun sites en genereer een marktonderzoeksrapport.

Output:
  {collected_path}/research/competitors.json   — geïdentificeerde + gecrawlde concurrenten
  {collected_path}/research.md                 — volledig marktonderzoeksrapport
"""
import argparse
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from anthropic import Anthropic
from bs4 import BeautifulSoup


PROSPECTS_FILE = Path("/workspace/data/prospects.json")

HEADERS = {"User-Agent": "SiteFactoryBot/0.1 (+internal use)"}
CRAWL_TIMEOUT  = 15
CRAWL_DELAY    = 0.8
MAX_COMP_PAGES = 3   # homepage + max 2 interne pagina's per concurrent


# ── Prospects ─────────────────────────────────────────────────────────────────

def load_prospects():
    return json.loads(PROSPECTS_FILE.read_text(encoding="utf-8"))


def save_prospects(prospects):
    PROSPECTS_FILE.write_text(
        json.dumps(prospects, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def find_prospect(prospects, name=None, force=False):
    if name:
        for i, p in enumerate(prospects):
            if p.get("name", "").strip().lower() == name.strip().lower():
                if p.get("status") != "collected":
                    raise RuntimeError(f"Prospect '{name}' is nog niet collected.")
                if not force and p.get("research_status") == "done":
                    raise RuntimeError(
                        f"Prospect '{name}' heeft al een research. Gebruik --force om opnieuw te genereren."
                    )
                return i, p
        raise RuntimeError(f"Prospect '{name}' niet gevonden.")

    for i, p in enumerate(prospects):
        if p.get("status") == "collected" and p.get("research_status") != "done":
            return i, p

    return None, None


# ── Crawler ───────────────────────────────────────────────────────────────────

def extract_visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    lines = [l.strip() for l in soup.get_text(separator="\n").splitlines()]
    return "\n".join(l for l in lines if l)


def fetch(url: str, session: requests.Session) -> str | None:
    try:
        r = session.get(url, headers=HEADERS, timeout=CRAWL_TIMEOUT, allow_redirects=True)
        r.raise_for_status()
        if "html" not in r.headers.get("content-type", ""):
            return None
        return r.text
    except Exception as e:
        print(f"[WARN] Ophalen mislukt: {url} — {e}")
        return None


def crawl_competitor(url: str) -> str:
    """Crawl homepage + een paar interne pagina's, geef gecombineerde zichtbare tekst."""
    session = requests.Session()
    homepage_html = fetch(url, session)
    if not homepage_html:
        return ""

    texts = [extract_visible_text(homepage_html)]
    base_domain = urlparse(url).netloc
    soup = BeautifulSoup(homepage_html, "lxml")

    visited = {url}
    count = 0
    for tag in soup.find_all("a", href=True):
        if count >= MAX_COMP_PAGES - 1:
            break
        href = urljoin(url, tag["href"])
        parsed = urlparse(href)
        if parsed.netloc != base_domain or parsed.scheme not in ("http", "https"):
            continue
        if href in visited:
            continue
        visited.add(href)
        time.sleep(CRAWL_DELAY)
        page_html = fetch(href, session)
        if page_html:
            texts.append(extract_visible_text(page_html))
            count += 1

    return "\n\n".join(texts)


# ── AI-stappen ────────────────────────────────────────────────────────────────

def identify_competitors(client, model: str, company_name: str, site_text: str) -> tuple[dict, object]:
    """Vraag Claude om het bedrijfstype te bepalen en concurrenten te identificeren."""
    prompt = f"""
Je bent een marktonderzoeker en webstrateeg.

Analyseer de tekst van de website van '{company_name}' en geef:
1. Een beknopte beschrijving van wat dit bedrijf doet (1-2 zinnen)
2. Een lijst van 4-5 echte, bestaande concurrerende websites

Regels:
- Kies alleen echte domeinen die je met zekerheid kent — geen gok-domeinen
- Bij een Nederlands of Belgisch bedrijf: kies bij voorkeur NL/BE/DE concurrenten
- Kies bedrijven die qua schaal en niche vergelijkbaar zijn (geen grote multinationals bij MKB)
- Geen nepdomeinen, geen placeholder-URLs

Geef ALLEEN dit JSON-object terug, zonder uitleg:
{{
  "business_type": "...",
  "competitors": [
    {{"name": "Naam", "url": "https://...", "reason": "waarom relevant"}}
  ]
}}

## Websitetekst van {company_name}
{site_text[:12000]}
"""
    response = client.messages.create(
        model=model,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = "".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()

    # Strip markdown code fences (ook bij \r\n of spaties rondom de fence)
    raw = re.sub(r'^\s*```\w*\s*\n', '', raw)
    raw = re.sub(r'\n\s*```\s*$', '', raw)
    return json.loads(raw.strip()), response


def generate_research_report(client, model: str, company_name: str,
                              original_text: str, competitor_data: list[dict]) -> tuple[str, object]:
    """Genereer een marktonderzoeksrapport. Gebruikt eigen kennis als scrapedata ontbreekt."""
    reachable = [c for c in competitor_data if c.get("text")]
    unreachable = [c for c in competitor_data if not c.get("text")]

    sections = []
    for comp in competitor_data:
        status = f"{len(comp['text'])} tekens gecrawld" if comp.get("text") else "niet bereikbaar"
        text_preview = comp.get("text", "")[:4000] or "[Niet bereikbaar — gebruik je eigen kennis over dit bedrijf]"
        sections.append(
            f"### {comp['name']} ({comp['url']})\n"
            f"**Reden gekozen:** {comp.get('reason', '')}\n"
            f"**Status:** {status}\n\n"
            f"{text_preview}"
        )

    # Schakel naar kennis-modus als weinig scrapedata beschikbaar is
    few_data = len(reachable) < 2
    knowledge_note = ""
    if few_data:
        unreachable_names = ", ".join(c["name"] for c in unreachable)
        knowledge_note = f"""
**Belangrijk:** De meeste concurrentsites waren niet bereikbaar ({unreachable_names}).
Gebruik daarom je eigen kennis en trainingsdata over deze branche en over deze bedrijven.
Geef aan met AANNEMELIJK welke informatie uit je eigen kennis komt.
"""

    length_instruction = "200 tot 300 regels" if few_data else "300 tot 400 regels"
    max_tokens = 4000 if few_data else 6000

    prompt = f"""
Je bent een senior webstrateeg en UX-researcher.

Schrijf een marktonderzoeksrapport voor het ontwerp van een nieuwe website voor **{company_name}**.
{knowledge_note}
Schrijf {length_instruction} markdown. Wees concreet.
Markeer onzekerheden met AANNEMELIJK. Verzin geen statistieken of metrics.

---

## Origineel bedrijf: {company_name}
{original_text[:5000]}

---

## Concurrenten
{"---".join(sections)}

---

Schrijf het rapport met deze structuur:

# Marktonderzoek: {company_name}

## Brancheoverzicht
Wat voor branche is dit? Welke diensten zijn typisch? Wat verwacht een bezoeker?

## Concurrentieanalyse
Analyseer elke concurrent: propositie, sitestructuur, CTAs, tone of voice, sterke en zwakke punten.
Gebruik scraped tekst waar beschikbaar, anders je eigen kennis (markeer als AANNEMELIJK).

## Gemeenschappelijke patronen in de branche
Wat doen alle spelers gemeenschappelijk? Wat verwacht een bezoeker van deze sector?

## Onderscheidingskansen voor {company_name}
Concrete aanbevelingen: waar kan {company_name} beter zijn dan de markt?

## Visuele en designtrends in de branche
Kleurgebruik, typografie, fotostijl, lay-outpatronen typisch voor deze sector.

## Samenvatting: kansen voor de nieuwe site van {company_name}
Drie tot vijf concrete, prioritaire aanbevelingen.
"""
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()
    return text, response


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name",  help="Naam van de prospect")
    parser.add_argument("--force", action="store_true", help="Genereer opnieuw ook als al gedaan")
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    model   = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY ontbreekt")

    client = Anthropic(api_key=api_key)

    prospects = load_prospects()
    index, prospect = find_prospect(prospects, name=args.name, force=args.force)

    if prospect is None:
        print("[INFO] Geen geschikte prospect gevonden.")
        return

    company_name   = prospect["name"]
    collected_path = Path(prospect["collected_path"])
    site_text      = (collected_path / "text.txt").read_text(encoding="utf-8", errors="ignore") \
                     if (collected_path / "text.txt").exists() else ""

    research_dir = collected_path / "research"
    research_dir.mkdir(exist_ok=True)

    print(f"[INFO] Research voor: {company_name}")
    print(f"[INFO] Model: {model}")

    # Stap 1: identificeer concurrenten
    print("\n[INFO] Stap 1/3: Concurrenten identificeren...")
    try:
        competitor_info, r1 = identify_competitors(client, model, company_name, site_text)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[FAIL] Kon concurrenten-JSON niet parsen: {e}")
        raise

    business_type = competitor_info.get("business_type", "")
    competitors   = competitor_info.get("competitors", [])

    print(f"[OK]  Bedrijfstype: {business_type}")
    print(f"[OK]  {len(competitors)} concurrenten geïdentificeerd:")
    for c in competitors:
        print(f"      - {c['name']} ({c['url']})")

    # Stap 2: crawl concurrenten
    print(f"\n[INFO] Stap 2/3: Concurrenten crawlen...")
    competitor_data = []
    for comp in competitors:
        url  = comp.get("url", "")
        name = comp.get("name", "")
        print(f"[INFO] Crawlen: {name} ({url})...")
        text = crawl_competitor(url)
        if text:
            print(f"[OK]  {name}: {len(text)} tekens")
        else:
            print(f"[WARN] {name}: niet bereikbaar of leeg")
        competitor_data.append({**comp, "text": text})
        time.sleep(CRAWL_DELAY)

    competitors_path = research_dir / "competitors.json"
    competitors_path.write_text(
        json.dumps({"business_type": business_type, "competitors": competitor_data},
                   indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"[OK]  Competitors opgeslagen: {competitors_path}")

    # Stap 3: genereer research rapport
    print(f"\n[INFO] Stap 3/3: Marktonderzoeksrapport genereren...")
    research_text, r2 = generate_research_report(client, model, company_name, site_text, competitor_data)

    research_path = collected_path / "research.md"
    research_path.write_text(research_text, encoding="utf-8")
    print(f"[OK]  Research rapport opgeslagen: {research_path} ({len(research_text)} tekens)")

    # Sla token usage op
    def _tok(r, field):
        return getattr(getattr(r, "usage", None), field, None) or 0

    in1, out1 = _tok(r1, "input_tokens"), _tok(r1, "output_tokens")
    in2, out2 = _tok(r2, "input_tokens"), _tok(r2, "output_tokens")

    research_meta = {
        "model": model,
        "steps": [
            {"step": "identify_competitors",   "input_tokens": in1, "output_tokens": out1},
            {"step": "generate_research_report", "input_tokens": in2, "output_tokens": out2},
        ],
        "usage": {
            "input_tokens":  in1 + in2,
            "output_tokens": out1 + out2,
        },
    }
    (collected_path / "research_meta.json").write_text(
        json.dumps(research_meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[OK]  Research metadata opgeslagen ({in1+in2} input / {out1+out2} output tokens)")

    prospects[index]["research_status"] = "done"
    prospects[index]["research_path"]   = str(research_path)
    save_prospects(prospects)

    print(f"[OK]  Prospect research_status bijgewerkt naar 'done'")


if __name__ == "__main__":
    main()
