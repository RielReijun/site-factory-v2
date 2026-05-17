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
- Je hebt pro-actief een nieuwe homepage gebouwd voor {company} op basis van hun bestaande site
- De demo staat live op: {demo_url}
- Wat je hebt gebouwd: alleen de homepage, volledig opnieuw ontworpen
- Wat nog niet gebouwd is: de overige pagina's (die maak je af als ze ja zeggen)
- Prijs: €499 voor de complete website (homepage + alle overige pagina's + overdracht van alle bestanden)
- Hosting en onderhoud bespreek je alleen als ze reageren, noem dit NIET in de mail

Toon:
- Kort, direct, menselijk, geen marketingtaal
- Geen bulleted lijstjes, geen headers, gewone alinea's
- Maximaal 3 korte alinea's (totaal max 120 woorden)
- Benoem 1 of 2 CONCRETE problemen die je zag op hun huidige site (gebruik de context hieronder)
- Benoem 1 CONCREET verschil dat je hebt aangebracht in de demo (niet generiek: "mooier" of "sneller")
- Wees transparant: je hebt de homepage gebouwd als showcase. De €499 is voor het complete werk
- Sluit zelfverzekerd af, geen verontschuldigingen, geen "het is nog niet af"
- Alleen voornaam als afsluiting, geen titel of verdere info

Wat er mis was op de originele site en wat je hebt verbeterd (haal hier de 1-2 beste, meest specifieke punten uit):
{briefing[briefing.find('## Gewenste verbeteringen') if '## Gewenste verbeteringen' in briefing else briefing.find('## Problemen') if '## Problemen' in briefing else 0:briefing.find('## Tone') if '## Tone' in briefing else 3000][:1500]}

Volledige briefing context:
{briefing[:2000]}

Genereer:
1. Een onderwerpregel (prefix: "Onderwerp: ") — prikkelend, specifiek, geen clickbait
2. De e-mailtekst

Schrijf in het Nederlands.
Verwijs naar de demo-URL: {demo_url}
Gebruik GEEN em-dashes (—).
Noem €499 letterlijk in de mail.
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
