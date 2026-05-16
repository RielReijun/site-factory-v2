# Site Factory project rules

## Projectdoel
Dit project is een interne websitefabriek die:
1. een bestaande website of bedrijfsomschrijving analyseert
2. een markdown briefing genereert
3. die briefing valideert en repareert
4. daarna een nieuwe statische website genereert
5. later GitHub repos en deploys automatiseert

## Huidige status
De pipeline heeft nu deze stappen (in volgorde):

**Fase 1 — Dataverzameling**
- `collect.py` — website crawlen, assets downloaden, logo detecteren, verbatim feiten extraheren
- `research.py` — concurrentenanalyse + marktrapport via Claude

**Fase 2 — Strategie**
- `brief.py` — strategische briefing genereren via Claude (streaming)
- `validate_brief.py` — verplichte secties + risico-claims controleren
- `repair_brief.py` — deterministische + LLM-herstelronde

**Fase 3 — Feiten & structuur**
- `inventory.py` — deterministische feitenextractie (prijzen, openingstijden, reviews, contactinfo, voice profile) — GEEN LLM, verbatim uit brondata
- `discover_pages.py` — paginastructuur bepalen via Claude (max 20 auto-pagina's)

**Fase 4 — Generatie**
- `generate_site.py` — Next.js TSX genereren via Claude (scaffold + layout + homepage + subpagina's parallel)

**Fase 5 — Build & validatie**
- `[npm run build]` — TypeScript-check + statische export via Turbopack
- `validate_generated_site.py` — structuurcheck (DOCTYPE, links, markers, placeholders)
- `repair_generated_site.py` — HTML-herstellaag (markers, gebroken links, lazy loading)
- `validate_generated_content.py` — inhoudscheck (naam, telefoon, prijzen, AANNEMELIJK-tokens)
- `screenshot_validate.py` — visuele QA via Playwright + Claude Vision (altijd exit 0, nooit blokkerend)
- `auto_repair.py` — herstelronden (max 3) op basis van validatie-issues

**Fase 6 — Afwerking**
- `cohesion_pass.py` — CSS-consistentiepass via Claude
- `polish_site.py` — footer normalisatie, OG-tags, schema.org JSON-LD, demo-banner
- `generate_mail.py` — outreach-email genereren via Claude

**Fase 7 — Deploy**
- `deploy.py` — GitHub repo aanmaken/pushen + Cloudflare Pages koppelen (handmatig via dashboard)

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

### Het feitenmodel (inventory.py)
`inventory.py` is het kerncomponent voor anti-hallucination. Het draait na `repair_brief.py` en vóór `generate_site.py` en extraheert **zonder LLM** verbatim feiten uit de brondata:
- Prijzen (bedrag, label, duur, categorie, confidence-niveau)
- Openingstijden (regex op text.txt)
- Reviews (blockquotes uit raw.html)
- Contactinfo (telefoon, e-mail, adres)
- Voice profile (je/u-vorm, formaliteitsniveau)
- Source copy (tagline, CTA-labels, h2-koppen, statistieken)

`generate_site.py` injecteert deze feiten direct in de prompt. Wanneer de generator iets moet toevoegen dat niet in `inventory.json` staat, plaatst hij het token `AANNEMELIJK` voor die zin. `validate_generated_content.py` detecteert alle AANNEMELIJK-tokens en rapporteert ze als kritieke bevinding. Er mogen nul AANNEMELIJK-tokens in de uiteindelijke output staan.

## Coding style
- Schrijf duidelijke Python
- Gebruik kleine functies
- Gebruik expliciete bestandsnamen en paden
- Voeg logging toe met heldere [INFO], [OK], [WARN], [FAIL] stijl
- Houd dependencies beperkt
- Houd scripts CLI-vriendelijk

### Regels voor gegenereerde websites
- **Geen em-dashes** (`—`) in kopij — gebruik komma of punt (afgedwongen in generate_site.py prompt + ux-writing.md)
- **Geen zelfverzonnen Tailwind-kleurnamen** (`bg-cream`, `bg-forest`) — gebruik uitsluitend DaisyUI semantische klassen (`bg-base-100`, `text-primary`) of exacte hex-waarden
- **MAX_AUTO_PAGES = 20** — pagina's boven dit maximum worden als `overflow_pages` in `pages.json` opgeslagen en worden niet automatisch gegenereerd
- **DaisyUI-thema** altijd via `data-theme="brand"` op `<html>` in layout.tsx — nooit hardcoded kleuren in component-bestanden
- **Vaste footer-structuur:** `site-footer > container > footer-grid > footer-col` — afwijken hiervan breekt de polish-stap

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

### Bekende security-aandachtspunten
- **Query-parameter token** in `app/server.py`: `?token=...` verschijnt in server-logs en browser-history. Alleen header of httpOnly cookie gebruiken.
- **Bridge op host-netwerk** (`network_mode: host` in compose.yaml): de bridge heeft toegang tot alle host-poorten. Dit was nodig voor Claude CLI-integratie maar vormt een vergroot aanvalsoppervlak als de bridge gecompromitteerd raakt.
- **Scheduler** is een while-true-sleep loop (geen echte job scheduler): als een pipeline-run langer dan 3600s duurt, lopen runs over elkaar heen. Geen concurrency-controle of dead-job-detectie aanwezig.
