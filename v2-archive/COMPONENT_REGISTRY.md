# Component Registry — v2

Alle Astro-componenten in `v2/templates/astro/archetypes/<archetype>/src/components/`,
plus per component **welke data nodig** is en **wanneer hij rendert** (de
heuristiek in `index.astro`).

Het rendering-pad is altijd: data uit `src/data/sitePlan.json` (geschreven door
`build_plan()`) wordt aan de component doorgegeven via Astro-props. Een
component die geen of onvoldoende data ontvangt rendert `null` — zo verschijnen
secties alleen wanneer de inventory ze ondersteunt, geen lege blokken.

## Layout & chrome

| Component | Wat het doet | Wanneer |
|---|---|---|
| `BaseLayout.astro` | HTML-shell met theme/typography/personality CSS-vars, Header, Footer, WhatsApp-float | altijd |
| `Header.astro` | Logo + nav + CTA-knop | altijd |
| `Footer.astro` | 4-koloms info: bedrijf, nav, behandelingen, contact | altijd |
| `WhatsAppFloat.astro` | Floating WhatsApp-knop rechtsonder | als `social.whatsapp` aanwezig |

## Hero & sections op homepage

In de volgorde waarin ze in `index.astro` worden ingeschakeld door de heuristiek.

| Component | Data-bron in plan | Render-conditie |
|---|---|---|
| `Hero.astro` | `plan.hero` (eyebrow/headline/body/image/variant/primaryCta/secondaryCta/meta) | altijd op home |
| `Stats.astro` | `plan.stats` (lijst van `{value, label}`) | render als `>= 2` stats — uit `source_copy.stats` (regex op "X jaar" / "X klanten" / etc.) |
| `Services.astro` | `plan.services` (lijst van `{title, description, image, href}`) | render als `services.length >= 1` |
| `FeaturedService.astro` | `plan.featured` (`{title, description, price, image, href}`) | render als `featured.title` set — `_pick_featured_service` kiest duurste behandeling met foto |
| `About.astro` | `plan.about` (`{title, body, signature, image}`) | render als `about` aanwezig |
| `QuoteCallout.astro` | `plan.quoteCallout` (`{quote, attribution}`) | render als minstens 2 signatures (gebruikt `signatures[1]` voor spotlight; `signatures[0]` zit al in hero/about) |
| `Gallery.astro` | `plan.gallery` (lijst van `{src, alt}`), `variants.gallery` (`grid`/`masonry`/`strip`) | render als `gallery.length >= 1` |
| `Reviews.astro` | `plan.reviews` (`{title, items: [{quote, attribution}], note}`) | render als `reviews.items.length >= 1` |
| `OpeningHours.astro` | `plan.openingHours` (`{items, fallback}`), `plan.contact` | render als `items` of `fallback` aanwezig |
| `ProcessSteps.astro` | `plan.process` (lijst van `{title, body}`) | render als `process.length >= 3` — local_service altijd, beauty alleen bij "werkwijze"-keyword in briefing |
| `MapSection.astro` | `plan.contact.address`, `plan.contact.phone`, `plan.contact.email` | render als `contact.address` >= 12 chars |
| `ContactCta.astro` | `plan.contactCta` (`{title, body, primary, secondary, tertiary}`), `plan.contact`, `plan.social` | altijd op home |

## Sub-page secties (in `[slug]/index.astro`)

| Component | Data-bron | Render-conditie |
|---|---|---|
| `PageContent.astro` | `page.content` (`{eyebrow, title, lead, body, bullets, note}`) | als `pageContent` in `page.sections` |
| `PriceList.astro` | `plan.prices` (`{title, groups: [{title, items: [{label, price}]}]}`) | als `prices` in `page.sections` en `prices.groups.length >= 1` |
| `TreatmentDetails.astro` | `plan.treatments` | als `treatments` in `page.sections` |
| `ProductStory.astro` | `plan.products` (`{title, lead, body, bullets}`) | als `products` in `page.sections` en `products.title` aanwezig |

## Personality-overrides

Op `<body data-personality="soft|sharp|luxe|playful">` worden in `site.css`
selector-overrides toegepast die het uiterlijk van de bovenstaande componenten
fundamenteel veranderen:

- **luxe** — italic headings, dramatic shadows, full-bleed dark hero, scherpe radius
- **sharp** — square corners, geen schaduw, uppercase buttons, formele uitstraling
- **playful** — 24px rounded corners, pill-shape buttons, energieke spacing
- **soft** — default huidige look (zachte shadows, mid radius)

Personality wordt gekozen door `pick_personality()` in `v2/pipeline/personality.py`
op basis van Visual DNA chroma + tone, voice-formality, en uitroep-frequentie.

## Sectie-volgorde-heuristiek per personality

`_pick_home_section_order()` in `v2/archetypes/beauty_wellness.py`:

- **identity-signature** (`Ik ben X`) → about-eerst
- **invitation-signature** (`Welkom`, `Ben je toe aan`) → gallery vroeg
- **mission/generic** → services-eerst (default)

Plus de extra-secties (stats/featured/process/map/quoteCallout) worden ingevoegd
in de volgorde die `index.astro` bepaalt op basis van data-aanwezigheid.

## Wanneer voeg ik een nieuwe sectie toe?

Drie criteria:

1. **Data-driven**: er moet een veld in `inventory` zijn dat de sectie voedt.
   Geen sectie zonder bron-data — anders bouwen we generieke fluff terug in.
2. **Conditioneel renderen**: component returnt `null` als z'n input ontbreekt.
   Geen "leeg blok"-renders.
3. **Heuristiek in `index.astro`**: voeg de sectie alleen aan de pagina toe
   wanneer relevante data aanwezig is. Hardcoded altijd-aan secties beperken
   we tot Hero + ContactCta.

Zo blijft de regel: "geen lege secties, alleen secties wanneer de bron ze
ondersteunt." Dat is het verschil tussen een templated AI-generator en een
echte content-driven site-fabriek.
