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

from pipeline_utils import get_claude_client

from pipeline_utils import slugify, load_prospects, get_model, PROSPECTS_FILE, DATA_DIR


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    model   = get_model()
    if not api_key:
        print("[FAIL] ANTHROPIC_API_KEY ontbreekt")
        sys.exit(1)

    agency_name  = os.getenv("AGENCY_NAME",  "")
    agency_email = os.getenv("AGENCY_EMAIL", "")
    agency_url   = os.getenv("AGENCY_URL",   "")

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
- Sluit af met iets in de trant van: "Het is nog niet perfect, maar de nieuwe basis is gezet. Ik hoop dat we samen verder kunnen bouwen." Pas de exacte bewoording aan zodat het past bij de toon van de mail, maar de boodschap (bescheiden, samenwerkingsgericht, ruimte voor verbetering) moet erin zitten.
- Daarna alleen de naam van de afzender, geen verdere afsluiting

Bedrijfscontext uit de briefing:
{briefing[:3000]}

Genereer:
1. Een onderwerpregel (prefix: "Onderwerp: ")
2. De e-mailtekst

Schrijf in het Nederlands. Noem iets specifieks over hun bedrijf zodat het niet generiek aanvoelt.
Verwijs expliciet naar de demo-URL: {demo_url}
Gebruik GEEN em-dashes (—). Gebruik een komma of punt waar dat natuurlijker klinkt.
"""

    client = get_claude_client(api_key)
    response = client.messages.create(
        model=model,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )

    mail_text = response.content[0].text.strip()

    footer_lines = []
    if agency_name:
        footer_lines.append(agency_name)
    if agency_email:
        footer_lines.append(agency_email)
    if agency_url:
        footer_lines.append(agency_url)
    if footer_lines:
        mail_text += "\n\n--\n" + "\n".join(footer_lines) + "\n"

    out_path.write_text(mail_text, encoding="utf-8")
    print(f"[OK]  Mail opgeslagen: {out_path}")

    from prospects_utils import update_prospect
    update_prospect(args.name, mail_status="done", mail_path=str(out_path))


if __name__ == "__main__":
    main()
