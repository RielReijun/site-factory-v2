# Site Factory — Technische Architectuur

> **Onderhoud:** Dit bestand bijwerken bij elke significante pipeline-wijziging.
> Laatste update: zie git log.

## Overzicht

Site Factory is een AI-gedreven websitegenerator voor MKB-bedrijven.
Input: een URL. Output: een volledig statische Next.js-site, gepubliceerd op Cloudflare Pages.

**Stack:**
- Pipeline: Python 3.11, Docker Compose
- Output: Next.js 16 + TypeScript + Tailwind v4 + DaisyUI
- AI: Anthropic Claude Sonnet 4.6
- Hosting: Cloudflare Pages

---

## Repository-structuur

```
site-factory/
├── scripts/               # Python pipeline scripts
│   ├── run_pipeline.py    # Orchestrator — roept alle stappen aan
│   ├── collect.py         # Website crawlen + assets downloaden
│   ├── research.py        # Concurrentenanalyse via Claude
│   ├── brief.py           # Briefing genereren via Claude
│   ├── validate_brief.py  # Briefing valideren
│   ├── repair_brief.py    # Briefing repareren via Claude
│   ├── discover_pages.py  # Pagina-structuur bepalen via Claude
│   ├── generate_site.py   # TSX-bestanden genereren via Claude
│   ├── parse_generated_site.py  # Raw Claude-output → bestanden
│   ├── validate_generated_site.py
│   ├── validate_generated_content.py
│   ├── repair_generated_site.py
│   ├── cohesion_pass.py   # CSS-consistentie via Claude
│   ├── polish_site.py     # Footer, OG tags, schema.org, demo-banner
│   ├── screenshot_validate.py  # Playwright + Claude Vision
│   ├── generate_mail.py   # Outreach-mail via Claude
│   ├── color_utils.py     # OKLCH kleurenpalet-generator
│   ├── template_engine.py # Recursive descent parser voor TSX-templates
│   ├── component_registry.py  # Template assembler (legacy, niet in kritiek pad)
│   └── pipeline_utils.py  # Gedeelde utilities (slugify, with_retry, etc.)
│
├── prompts/
│   ├── impeccable/        # Design-referentiebestanden (typography, color, etc.)
│   └── components/        # TSX-sectiontemplates (19 .tsx bestanden)
│       ├── hero/          # full-overlay, split, gradient
│       ├── services/      # cards-3, cards-4
│       ├── about/         # split-image-left, split-image-right
│       ├── contact/       # with-form, with-map, info-only
│       ├── faq/           # default (shadcn Accordion)
│       ├── cta/           # centered
│       ├── gallery/       # grid-3, carousel (Embla)
│       ├── team/          # cards-3
│       ├── testimonials/  # cards
│       ├── stats/         # bar
│       ├── page_hero/     # default
│       └── pricing/       # table (auto-gegenereerd)
│
├── app/                   # Flask dashboard (poort 8181)
├── bridge/                # Lokale Claude-bridge voor dashboard-chat
├── data/                  # Docker volume: prospects.json, logs, collected data
├── output/                # Docker volume: gegenereerde Next.js projecten
├── Dockerfile
└── compose.yaml
```

---

## Pipeline-stappen

De orchestrator `run_pipeline.py` roept elke stap als subprocess aan.

### Stap 1 — Collect (`collect.py`)

**Input:** `prospect["url"]`

**Werking:**
1. Phase 0: lees `sitemap.xml` voor volledige paginalijst
2. Crawl alle HTML-pagina's (max 50, zie `config.py`)
3. Download assets: CSS, afbeeldingen (max 150)
4. Extract per pagina: `meta_tags`, `json_ld`, `nav`, `social_links`, `contact`
5. Detect en download logo via `extract_logo_url()`:
   zoekt in volgorde: JSON-LD schema:logo → apple-touch-icon → img[class/alt/id*=logo] → eerste img in `<header>`
6. Crawl eventuele referentiesite (`prospect["reference_url"]`)

**Output in `/workspace/data/[slug]/`:**
- `text.txt` — gecombineerde zichtbare tekst
- `structured_data.json` — contactinfo, JSON-LD, nav, socials
- `meta.json` — crawl-statistieken
- `assets/css/*.css`, `assets/images/*`
- `logo.{ext}` — gedetecteerd logo
- `raw.html` — homepage HTML

---

### Stap 2 — Research (`research.py`)

**Werking:**
1. Claude `identify_competitors`: bepaalt bedrijfstype + 4–5 concurrenten
2. Crawl elke concurrent (homepage + 2 subpagina's)
3. Claude `generate_research_report`: schrijft marktonderzoeksrapport

**Output:** `research.md`, `research/competitors.json`, `research_meta.json`

**Token-gebruik:** ~25K tokens · ~€0.10/site

---

### Stap 3 — Brief (`brief.py`)

**Input:** `text.txt`, CSS, `research.md`, `structured_data.json`, logo, afbeeldingen, referentiesite

**Werking:** Claude genereert via streaming een briefing (~200 regels markdown) met:
- Bedrijfsprofiel, doelgroep, tone of voice
- Designrichting: **exacte hex-kleurcodes** (primair, secundair, achtergrond, tekst)
- Heading- en bodyfont
- Aanbevolen paginastructuur
- Wat niet verzonnen mag worden

**Output:** `briefing.md`, `briefing_meta.json`

---

### Stap 4 — Validate + Repair Brief

`validate_brief.py`: controleert verplichte secties en risicovolle claims.
Bij problemen: `repair_brief.py` laat Claude herziening maken + slaat diff op als `repair_diff.txt`.

---

### Stap 5 — Discover Pages (`discover_pages.py`)

Claude leest de briefing en bepaalt de routes van de nieuwe site.

**Output:** `pages.json` — lijst van `{file, title, description}`

---

### Stap 6 — Scaffold Next.js

**Locatie:** `OUTPUT_DIR/[slug]-next/`

**Werking:**
1. `npx create-next-app@latest` met TypeScript + Tailwind + App Router + src-dir
2. `npm install`:
   - lucide-react, daisyui, tailwindcss-animate
   - @tailwindcss/typography, @tailwindcss/forms
   - next-sitemap
   - embla-carousel-react, embla-carousel-autoplay
   - leaflet, react-leaflet, @types/leaflet
3. `next.config.ts`: `output: "export"`, `trailingSlash: true`, `basePath: "/sites/[slug]"` (voor dashboard-preview)
4. UI-componenten aanmaken in `src/components/ui/`:
   Button (CVA + Slot), Card, Badge, Accordion (Radix), Separator, Sheet (React context)
5. Gedeelde componenten: `GalleryCarousel.tsx` (Embla + Autoplay), `LeafletMap.tsx` (dynamic import), `BookingWidget.tsx` (Calendly/Treatwell iframe)
6. `next-sitemap.config.js` met `SITE_URL` env-variabele
7. Logo + afbeeldingen kopiëren naar `public/`

---

### Stap 7 — Generate Layout

**Script:** `generate_site.py --unit layout`

**Output:**
- `src/app/layout.tsx` — root layout, `<html data-theme="brand">`, metadata
- `src/components/Header.tsx` — sticky header, mobiel menu (Sheet), active nav, `tel:` link
- `src/components/Footer.tsx` — navigatie, contactinfo, social, copyright
- `src/app/globals.css` — Tailwind v4 + DaisyUI + merkkleur-variabelen

**Automatische fixes na generatie:**
1. `_fix_globals_css(project_dir, collected_path)`:
   - Patcht `@tailwind base` → `@import "tailwindcss"`
   - Activeert `@plugin "daisyui"`, `@plugin "@tailwindcss/typography"`
   - Roept `color_utils.generate_palette()` aan: genereert 18 DaisyUI CSS-variabelen uit merkkleur
2. `_fix_lucide_icons()`: vervangt niet-bestaande icons (Instagram→Camera, etc.)
3. `_fix_layout_tsx()`: repareer backtick-syntax in className, darkMode config
4. `_fix_daisyui_colors()`: vervangt `bg-[--color-bg]` en uitgevonden kleurnamen (bg-cream, bg-forest) → `bg-base-100/95 backdrop-blur-sm`
5. `_patch_daisyui_theme()`: zet `data-theme="brand"` op `<html>` in layout.tsx

---

### Kleurenpalet-generator (`color_utils.py`)

Pure Python OKLCH-implementatie (geen externe dependencies).

**Conversie:**
```
hex → sRGB → linear RGB → LMS (M1 matrix) → OKLab → OKLCH
```

Van primaire + optionele secundaire merkkleur genereert het 18 DaisyUI CSS-variabelen:
- primary/content, secondary/content, accent/content
- neutral/content (altijd donker voor footer)
- base-100/200/300 (lichte achtergronden met brand-tint)
- base-content (donkere tekst)
- info, success, warning, error (generiek)

Kleuren worden geëxtraheerd uit `briefing.md` via regex op "Primaire kleur: #hex".

---

### Stap 8 — Generate Homepage + Subpagina's

**Script:** `generate_site.py --unit home / --unit page`

**Prompting-architectuur (prompt caching):**
- `base` (~6K tokens, `cache_control: ephemeral`): gemeenschappelijke regels + briefing + Impeccable design-referenties + afbeeldingen
- `unit_part` (niet-gecached): unit-specifieke instructies

Cache hit rate: ~82% bij parallel genererende subpagina's → ~87% kostenbesparing op de base.

**Subpagina's:** parallel via `ThreadPoolExecutor(max_workers=3)`, ontvangen homepage als TSX-referentie.

**Impeccable design-referenties** worden geladen via `_load_impeccable()` uit `prompts/impeccable/`:
typography.md, color-and-contrast.md, spatial-design.md, responsive-design.md, interaction-design.md, ux-writing.md (inclusief em-dash anti-pattern)

**Design-instructies in de prompt:**
- NOOIT em-dashes in kopij
- NOOIT zelfverzonnen Tailwind-kleurnamen (bg-cream, bg-forest); alleen DaisyUI semantisch of hex
- Header: ALTIJD `bg-base-100/95 backdrop-blur-sm`
- DaisyUI component-klassen met merksfeer-context (table voor prijslijsten, stat voor USPs, etc.)
- Openingstijden verplicht tonen als ze in de briefing staan
- `tel:` links, CTA-sectie voor elke footer, WhatsApp link
- Vaste footer-structuur: `site-footer > container > footer-grid > footer-col`
- FAQ altijd via shadcn Accordion

---

### Stap 9 — Build

```
tsc --noEmit --skipLibCheck  (TypeScript pre-check)
  → auto-fixes bij fouten: _fix_lucide_icons, _fix_invalid_style_props, _fix_page_function_names
npm run build               (Turbopack → statische export)
  → retry met auto-fixes bij build-fout
npx next-sitemap            (sitemap.xml + robots.txt in /out)
```

**Output:** `out/` directory met statische HTML (`/over-ons/index.html` etc.)

**Bekende auto-fixes:**
- Font weights 500/600/800 → 400 (Lato ondersteunt die niet)
- Functienamen die beginnen met cijfer: `360TourPage` → `TourPage360`
- Ongeldige inline CSS properties (divideColor etc.)

---

### Stap 10 — Validate Site

Controleert `/out/` op: DOCTYPE, viewport meta, geen raw markers, geen placeholders, geen gebroken links, minimale homepage-lengte.

---

### Stap 11 — Repair Site

Werkt **alleen** op HTML (geen CSS/JS bestanden in Next.js `/out/`):
- Raw file-markers verwijderen
- Gebroken interne links → `#`
- Favicon toevoegen
- `loading="lazy"` op afbeeldingen
- CSS coverage check (diagnostisch)

Bij screenshot-issues: CSS-overrides als `<style>` blok in `<head>` injecteren.

---

### Stap 12 — Content Check

Niet-blokkerend. Controleert: bedrijfsnaam aanwezig, contactinfo uit `structured_data.json`, geen placeholders, verwachte pagina's aanwezig (handelt Next.js subdirectory-structuur).

---

### Stap 13 — Screenshot Validatie

Playwright headless Chromium:
- `index.html` op 375px (mobiel) + 1280px (desktop)
- 2 willekeurige subpagina's op 1280px

Claude Vision analyseert op: contrast, gebroken layouts, placeholders (demo-banner genegeerd), lege secties.
Bij issues: CSS-overrides gegenereerd en als `<style>` geïnjecteerd.

---

### Stap 14 — Cohesion Pass

Claude leest alle HTML + CSS-context uit `_next/static/chunks/*.css`. Schrijft CSS-overrides voor subpagina-consistentie (responsive breakpoints, kaart-contrast, typografie). Injectie als `<style>` blok in alle HTML `<head>` elementen. Daarna spot-check via extra screenshot van willekeurige subpagina.

---

### Stap 15 — Polish

Werkt op `/out/` via `rglob("*.html")`:
1. Dubbele pagina's dedupliceren (Jaccard-similariteit op CSS class names)
2. Footer normaliseren (alle subpagina's → footer van `index.html`)
3. Open Graph meta-tags (`og:title`, `og:description`)
4. Canonical links
5. Schema.org LocalBusiness JSON-LD (data uit `structured_data.json`)
6. Demo-banner injecteren (sluitbaar, sessionstorage)

---

### Stap 16 — Generate Mail

Claude schrijft outreach-e-mail op basis van briefing.

---

## Dashboard (`app/server.py`)

Flask op poort 8181. Belangrijke routes:

| Route | Beschrijving |
|---|---|
| `GET /sites/[slug]/` | Serveert `[slug]-next/out/index.html` |
| `GET /sites/[slug]/[route]/` | Serveert `/out/[route]/index.html` (directory mapping) |
| `GET /_next/[path]` | Serveert `_next/` assets via Referer-header detectie |
| `GET /assets/[path]` | Serveert `public/assets/` via Referer-header detectie |
| `POST /api/chat` | Proxy naar bridge (poort 8182) als SSE |
| `POST /api/bulk-trash` | Meerdere prospects naar prullenbak |

`_find_site_dir(slug)`: geeft voorrang aan `[slug]-next/out/` boven `[slug]-site/`.

**Kleurpalet per site:** Kosten berekend via `get_prospect_costs()` — leest `*.meta.json` bestanden per slug uit `OUTPUT_DIR`. Slaat oude HTML-units over als er een Next.js `/out/` bestaat.

---

## Bridge (`bridge/bridge.py`)

Docker service met `network_mode: host`. Mount:
- `/vscode-extensions:ro` — Claude Code binary
- `~/.claude.json:ro` — Max-account authenticatie

Roept `claude --print --output-format text` aan in project-directory. Streamt antwoorden als Server-Sent Events. Maakt gebruik van Max-account (geen API-kosten).

---

## MCP Server (`scripts/mcp_server.py`)

FastMCP server voor Claude Code VS Code extensie. Tools:
`list_prospects`, `add_prospect`, `run_pipeline`, `get_pipeline_status`, `get_pipeline_log`, `reactivate_prospect`, `get_prospect_details`

---

## Template Engine (`template_engine.py`)

Recursive descent parser voor TSX-templates in `prompts/components/`.

**Syntax:**
```
[[ field ]]              waarde-substitutie
[[ ?field ]]...[[ / ]]   conditioneel (ondersteunt nesting)
[[ *items ]]...[[ / ]]   loop over lijst
[[ .subfield ]]          item-eigenschap in loop
[[ ~icon ]]              Lucide-icon met SAFE_ICONS whitelist
[[ ~.icon ]]             item-level icon in loop
```

Nieuwe sectie = nieuw `.tsx` bestand in `prompts/components/{type}/{variant}.tsx`.
Auto-generate via Claude als onbekend type wordt opgevraagd → opgeslagen voor hergebruik.

---

## Bekende beperkingen

| Probleem | Huidige staat |
|---|---|
| Claude gebruikt DaisyUI klassen niet altijd | Auto-fix aanwezig voor worst cases; prompt benadrukt het |
| `basePath` voor preview vs. deploy | Preview: `/sites/[slug]` (correct). Deploy: moet worden geleegd (anders werken links niet op live URL) |
| Schema.org siteUrl | Gebruikt prospect-URL (originele site), niet de Cloudflare-URL |
| Font weights | Auto-fix: 500/600/800 → 400 voor Lato |
| Zelfverzonnen kleurnamen | Auto-fix in `_fix_daisyui_colors()` voor Header.tsx |
| Booking widget | Component aangemaakt maar Claude moet het zelf inzetten op basis van briefing |

---

## Configuratie (`config.py`)

Alle magic constants:

```python
CRAWL_DELAY        = 0.5    # seconden tussen requests
MAX_PAGES          = 50     # max HTML-pagina's per crawl
MAX_ASSETS         = 150    # max asset-downloads
MAX_WORKERS        = 3      # parallelle TSX-generatie threads
MAX_RATE_RETRIES   = 5      # retries bij rate limit
RETRY_BASE_WAIT    = 30.0   # backoff-basis (seconden)
SCREENSHOT_MAX_PAGES = 4    # screenshots per run (homepage mobiel+desktop + 2 random)
```

---

## Data-flow diagram

```
URL
 │
 ▼
collect.py ─────────────────────────────────── /data/[slug]/
 │  text.txt, structured_data.json,              text.txt
 │  assets/, logo.*, raw.html                    structured_data.json
 │
 ▼
research.py (Claude) ──────────────────────── research.md
 │
 ▼
brief.py (Claude streaming) ───────────────── briefing.md
 │
 ▼
validate_brief → repair_brief (Claude)
 │
 ▼
discover_pages (Claude) ───────────────────── pages.json
 │
 ▼
scaffold_nextjs ────────────────────────────── /output/[slug]-next/
 │  create-next-app + npm install               src/components/ui/
 │  UI-componenten aanmaken                     src/components/{Carousel,Map,Booking}
 │
 ▼
generate layout (Claude streaming) ──────────── src/app/layout.tsx
 │  + _fix_globals_css (color_utils)             src/components/{Header,Footer}.tsx
 │  + _fix_lucide_icons                          src/app/globals.css (OKLCH palet)
 │  + _patch_daisyui_theme
 │
 ▼
generate home + pages (Claude, parallel) ────── src/app/page.tsx
 │  prompt caching (~82% hit rate)               src/app/[route]/page.tsx
 │  homepage als TSX-referentie voor subpagina's
 │
 ▼
npm run build ──────────────────────────────── out/ (statische HTML)
 │  TypeScript pre-check + auto-fixes            out/index.html
 │  next-sitemap                                 out/sitemap.xml
 │
 ▼
validate + repair + content-check
 │
 ▼
screenshot validate (Playwright + Claude Vision)
 │  375px mobiel + 1280px desktop
 │
 ▼
cohesion pass (Claude) ─────────────────────── <style> in HTML heads
 │
 ▼
polish ─────────────────────────────────────── footer normalisatie
 │  schema.org, OG tags, canonical               schema.org JSON-LD
 │  demo-banner
 │
 ▼
generate_mail (Claude)
 │
 ▼
mark_site_done → prospects.json["site_status"] = "done"
 │
 ▼
deploy (handmatig via dashboard)
  GitHub repo aanmaken/updaten → Cloudflare Pages
```
