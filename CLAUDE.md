# Site Factory project rules

## Projectdoel
Dit project is een interne websitefabriek die:
1. een bestaande website of bedrijfsomschrijving analyseert
2. een markdown briefing genereert
3. die briefing valideert en repareert
4. daarna een nieuwe statische website genereert
5. later GitHub repos en deploys automatiseert

## Huidige status
De pipeline heeft nu deze stappen:
- collect.py
- research.py
- brief.py
- validate_brief.py
- repair_brief.py
- discover_pages.py
- generate_site.py
- validate_generated_site.py
- validate_generated_content.py
- screenshot_validate.py
- auto_repair.py
- deploy.py

De huidige testcases zijn:
- Sander Appel Media
- Omnitour

Omnitour is nu de belangrijkste test-case.

## Belangrijke technische regels
- Gebruik Python
- Gebruik Docker Compose
- Werk binnen de bestaande projectstructuur
- Gegenereerde websites zijn statische exports van Next.js App Router projecten
- Gebruik React/Next.js, TypeScript, Tailwind v4 en DaisyUI volgens de bestaande pipeline
- Houd de uiteindelijke deploy-output statisch exporteerbaar via `next build` met `output: "export"`
- Houd scripts simpel, leesbaar en uitbreidbaar
- Schrijf geen grote refactors zonder noodzaak
- Wijzig bestaande scripts voorzichtig en leg uit waarom

## Architectuurprincipes
- Elke stap in de pipeline moet apart uitvoerbaar zijn
- Elke stap moet duidelijke input en output hebben
- Valideren moet expliciet zijn, niet impliciet
- AI-output mag niet blind vertrouwd worden
- Claims, cijfers en testimonials mogen niet verzonnen worden
- Fouten moeten reproduceerbaar en debugbaar zijn

## Coding style
- Schrijf duidelijke Python
- Gebruik kleine functies
- Gebruik expliciete bestandsnamen en paden
- Voeg logging toe met heldere [INFO], [OK], [WARN], [FAIL] stijl
- Houd dependencies beperkt
- Houd scripts CLI-vriendelijk

## Wat Claude moet vermijden
- Geen onnodige abstrahering
- Geen over-engineering
- Geen frameworkwissel voorstellen zonder expliciete reden; Next.js is nu de gekozen output-stack
- Geen verzonnen bedrijfsinformatie in prompts of gegenereerde sites
- Geen grote wijzigingen zonder eerst het plan uit te leggen

## Huidige prioriteit
De huidige prioriteit is:
1. security hardening voor dashboard, bridge en deploy credentials
2. queue/statusmodel betrouwbaarder maken
3. validatie en repair-rondes beter observeerbaar maken
4. templates en sitekwaliteit verbeteren zonder de pipeline minder reproduceerbaar te maken
