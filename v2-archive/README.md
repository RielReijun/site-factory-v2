# Site Factory v2 — Astro Experiment

Side-by-side experiment naast de bestaande `scripts/` Next.js pipeline. v1 blijft
ongemoeid: alle v2-code staat onder `v2/`, alle output onder `artifacts/v2/`.

## Doel

Per business-vorm één gepolijst archetype dat we deterministisch renderen. AI
schrijft géén Astro-code at runtime. AI mag wel structured data leveren
(`site_plan.json`), maar de Astro-templates op disk blijven het canonieke
contract.

## Beschikbare archetypes

| Naam              | Match-keywords                                                    | Status |
| ----------------- | ----------------------------------------------------------------- | ------ |
| `beauty_wellness` | beautysalon, kapsalon, schoonheidsspecialist, wellness, spa, etc. | klaar  |
| `local_service`   | stucadoor, schilder, bouwbedrijf, hovenier, loodgieter, etc.      | klaar  |
| `generic`         | fallback wanneer geen archetype matcht                            | klaar  |

Een archetype levert:

- **detect()** keyword-score op briefing + nav + meta.
- **get_contract()** lijst van `FieldCheck`s voor de quality gate.
- **build_plan()** rijke `site_plan.json` met hero, services, gallery, reviews,
  opening hours, about, contact CTA, theme en pagina's.
- **render()** kopieert vaste templates uit
  `v2/templates/astro/archetypes/<naam>/`, plaatst geselecteerde assets in
  `public/assets/`, schrijft `src/data/sitePlan.json`.

## Pipeline-stappen

```
collected data            inventory                 quality gate
data/<slug>/        →     content_inventory.json →  quality_report.json
                          (provenance per veld)     (passes/warns/fails)
                                                          ↓ (alleen bij ok)
                                                    archetype.render()
                                                          ↓
                                                    artifacts/v2/<slug>/astro-source/
```

Elke stap is afzonderlijk uitvoerbaar:

```bash
# 1. Inventory los draaien (alleen extractie + samenvatting)
python3 v2/scripts/build_inventory.py --slug www-beautysaloncarlijn-nl

# 2. Quality gate los draaien (inventory + validatie, geen render)
python3 v2/scripts/build_from_existing_brief.py --slug www-beautysaloncarlijn-nl --gate-only

# 3. Volledige pijplijn (gate → render)
python3 v2/scripts/build_from_existing_brief.py --slug www-beautysaloncarlijn-nl --force

# 3b. Negeer fail-niveau gate-issues (zelden gebruiken)
python3 v2/scripts/build_from_existing_brief.py --slug ... --force --ignore-quality-gate

# 4. Visual lint op de gebouwde dist (impeccable, non-blocking)
python3 v2/scripts/lint_visual.py --slug www-beautysaloncarlijn-nl
```

## Batch-mode + dashboard

Voor het draaien op meerdere prospects tegelijk en het bekijken van de
resultaten:

```bash
# Inventory + gate + render voor alle prospects in data/
docker run --rm \
  -v $(pwd):/workspace \
  -w /workspace \
  site-factory-worker \
  bash -lc 'pip install -q -r /workspace/v2/requirements.txt && \
            python3 /workspace/v2/scripts/batch_build.py --all --ignore-gate'
# → schrijft artifacts/v2/_index.json met status per prospect
```

Dashboard met overzicht en preview-iframes:

```bash
docker run --rm \
  -v $(pwd):/workspace \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -w /workspace \
  -p 8281:8281 \
  --name v2-dashboard \
  site-factory-worker \
  bash -lc 'pip install -q -r /workspace/v2/requirements.txt && \
            python3 /workspace/v2/dashboard/server.py'
```

Open `http://localhost:8281`. Toont alle prospects als kaarten met:
- archetype-badge en kleurpalet uit Visual DNA
- gate status (passes/warns/fails) en fail-redenen
- signature-zin per prospect
- "Bouw site" knop die `npx astro build` triggert via docker
- "Bekijk site" link (na build) die `dist/` live serveert via `/sites/<slug>/`

De Docker-socket-mount is nodig zodat het dashboard `docker run` kan
gebruiken voor on-demand builds.

Detectie is automatisch (`--archetype auto` is default). Forceer een specifieke
keuze met `--archetype beauty_wellness` of skip met `--archetype generic`.

Resultaat per slug onder `artifacts/v2/<slug>/`:

- `content_inventory.json` — alles wat de extractor in de bron vond, met
  `source: "text.txt:524"` provenance per veld
- `quality_report.json` — pass/warn/fail per archetype-contract check
- `site_plan.json` — leesbaar JSON-plan voor de templates
- `astro-source/` — kant-en-klaar Astro project, inclusief `public/assets/`
- `visual_lint.json` — impeccable findings (alleen na `lint_visual.py`)

## Bouwen en previewen

Astro is geen Python: je hebt `node` + `npm` nodig. Op de host:

```bash
cd artifacts/v2/www-beautysaloncarlijn-nl/astro-source
npm install
npm run build      # output naar dist/
npm run preview    # serveert dist/ op http://localhost:4321
```

Geen `npm` op de host? Gebruik de bestaande worker-image:

```bash
docker run --rm \
  -v /home/ryan-poser/site-factory:/workspace \
  -w /workspace/artifacts/v2/www-beautysaloncarlijn-nl/astro-source \
  -p 4321:4321 \
  site-factory-worker \
  sh -lc 'npm install && npm run build && npm run preview -- --host 0.0.0.0'
```

Voor dev met hot reload: `npm run dev` (of `--host 0.0.0.0` in Docker).

## Een nieuwe archetype toevoegen

1. Maak `v2/templates/astro/archetypes/<naam>/` met een complete Astro
   skeleton (`package.json`, `astro.config.mjs`, `src/layouts`, `src/pages`,
   `src/components`, `src/styles`, `public/`).
2. Maak `v2/archetypes/<naam>.py` met een class die voldoet aan `Archetype`
   (zie `archetypes/base.py`):
   - `name`
   - `detect(briefing, structured, meta) -> ArchetypeMatch`
   - `get_contract() -> list[FieldCheck]` — wat moet de inventory minimaal
     bevatten voor dit archetype?
   - `build_plan(...) -> dict` met de site-plan velden die je templates lezen
   - `render(...)`: gebruik
     `v2/generators/astro/render_archetype.render_archetype_site` als helper
3. Registreer het in `v2/archetypes/detect.py` (`registered_archetypes`).
4. Run `python3 v2/scripts/build_from_existing_brief.py --slug <slug> --gate-only`
   om te verifieren dat detectie en contract werken, daarna zonder `--gate-only`
   voor render.

## Quality gate FieldCheck-rules

Het contract is een lijst `FieldCheck`s. Beschikbare rules:

| Rule | Wat het doet |
| --- | --- |
| `required` | Veld moet niet-leeg zijn (anders fail/warn). |
| `recommended` | Soft `required`: missing geeft alleen warn. |
| `min_count` | `len(veld) >= min_count`. |
| `required_if_evidence` | Veld leeg + `evidence_pattern` matcht in `text.txt` → fail/warn. Zonder evidence: pass. |

`severity` is `"fail"` of `"warn"`. Fails blokkeren de render (override met
`--ignore-quality-gate`); warns niet.

## Designprincipes

- **v1 is heilig**: niets onder `scripts/`, `app/`, `bridge/` mag wijzigen voor
  v2.
- **Templates op disk, niet in code**: alle Astro-bestanden staan als statische
  files in `v2/templates/`. De Python-renderer kopieert en levert data.
- **Geen LLM at runtime in v2**: planopbouw is deterministisch uit de bestaande
  collected data. Wil je later een LLM-planner inbouwen, schrijf hem als een
  variant van `build_plan()` die hetzelfde `site_plan.json`-contract retourneert.
- **Geen verzonnen content**: reviews, openingstijden en cijfers blijven
  expliciet leeg of worden duidelijk gelabeld als merkbeloften tot de klant
  echte data aanlevert.
