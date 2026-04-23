"""
generate_mail.py — Genereer een persoonlijke cold-outreach mail op basis van de briefing.

Output: data/{slug}/outreach_mail.txt
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

from anthropic import Anthropic

PROSPECTS_FILE = Path("/workspace/data/prospects.json")
DATA_DIR       = Path("/workspace/data")


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def load_prospects() -> list:
    return json.loads(PROSPECTS_FILE.read_text(encoding="utf-8"))


def save_prospects(prospects: list) -> None:
    PROSPECTS_FILE.write_text(
        json.dumps(prospects, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    model   = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    if not api_key:
        print("[FAIL] ANTHROPIC_API_KEY ontbreekt")
        sys.exit(1)

    agency_name  = os.getenv("AGENCY_NAME",  "")
    agency_email = os.getenv("AGENCY_EMAIL", "")

    prospects = load_prospects()
    prospect  = next((p for p in prospects if p.get("name", "").strip().lower() == args.name.strip().lower()), None)
    if not prospect:
        print(f"[FAIL] Prospect '{args.name}' niet gevonden")
        sys.exit(1)

    slug          = slugify(prospect.get("name", ""))
    collected_dir = Path(prospect.get("collected_path", DATA_DIR / slug))
    briefing_path = Path(prospect.get("briefing_path", collected_dir / "briefing.md"))
    out_path      = collected_dir / "outreach_mail.txt"

    if not briefing_path.exists():
        print(f"[FAIL] Briefing niet gevonden: {briefing_path}")
        sys.exit(1)

    briefing   = briefing_path.read_text(encoding="utf-8", errors="ignore")
    company    = prospect.get("name", args.name)
    site_url   = prospect.get("url", "")

    cf_url     = prospect.get("cloudflare_url", "")
    demo_url   = cf_url if cf_url else f"https://site-{slug}.pages.dev"

    print(f"[INFO] Mail genereren voor {company}...")

    prompt = f"""Je schrijft een korte, persoonlijke cold-outreach e-mail namens {agency_name or 'een webdesigner'}.

Situatie:
- Jij hebt pro-actief een nieuwe website gemaakt voor {company} als demo
- De demo staat live op: {demo_url}
- Je stuurt deze mail naar het bedrijf om te vragen of ze interesse hebben
- Eenmalige kosten: €300 voor de complete website
- Hosting: €10 per maand (inclusief onderhoud en live zetten)
- Jij regelt alles: het overzetten, live zetten en de hosting. Zij hoeven niks te doen.

Toon:
- Kort, direct en menselijk, geen marketingtaal
- Geen bulleted lijstjes, geen headers, gewone alinea's
- Maximaal 3 korte alinea's
- Noem de prijs gewoon en zelfverzekerd, niet verontschuldigend
- Sluit af met naam van de afzender als die beschikbaar is

Bedrijfscontext uit de briefing:
{briefing[:3000]}

Genereer:
1. Een onderwerpregel (prefix: "Onderwerp: ")
2. De e-mailtekst

Schrijf in het Nederlands. Noem iets specifieks over hun bedrijf zodat het niet generiek aanvoelt.
Verwijs expliciet naar de demo-URL: {demo_url}
Gebruik GEEN em-dashes (—). Gebruik een komma of punt waar dat natuurlijker klinkt.
"""

    client   = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )

    mail_text = response.content[0].text.strip()

    # Voeg afzenderinfo toe als die beschikbaar is
    if agency_name or agency_email:
        footer = "\n\n--\n"
        if agency_name:
            footer += agency_name + "\n"
        if agency_email:
            footer += agency_email + "\n"
        mail_text += footer

    out_path.write_text(mail_text, encoding="utf-8")
    print(f"[OK]  Mail opgeslagen: {out_path}")

    # Sla status op in prospects.json
    for i, p in enumerate(prospects):
        if p.get("name", "").strip().lower() == args.name.strip().lower():
            prospects[i]["mail_status"] = "done"
            prospects[i]["mail_path"]   = str(out_path)
            break
    save_prospects(prospects)


if __name__ == "__main__":
    main()
