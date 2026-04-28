"""
config.py — Gecentraliseerde configuratieconstanten voor de site-factory pipeline.

Importeer vanuit elk script in plaats van magic numbers inline te definiëren.
"""

# ── Crawler (collect.py) ──────────────────────────────────────────────────────
CRAWL_DELAY  = 0.5   # seconden tussen requests
MAX_PAGES    = 50    # maximaal aantal HTML-pagina's om te crawlen per site
MAX_ASSETS   = 150   # maximaal aantal assets te downloaden per site

# ── Research crawler ──────────────────────────────────────────────────────────
RESEARCH_CRAWL_DELAY = 0.8
MAX_COMP_PAGES       = 3    # homepage + max 2 interne pagina's per concurrent
CRAWL_TIMEOUT        = 15   # seconden per HTTP-request

# ── Generatie ─────────────────────────────────────────────────────────────────
import os as _os
MAX_WORKERS          = 1 if _os.getenv("USE_CLAUDE_MAX", "").lower() in ("true", "1", "yes") else 3    # 1 bij Claude Max (serieel), 3 bij API
MAX_RATE_RETRIES     = 5    # max pogingen bij rate-limit in generate_site.py
RETRY_BASE_WAIT      = 30.0 # seconden basiswachttijd voor exponential backoff

# ── Afbeeldingen ─────────────────────────────────────────────────────────────
MIN_IMAGE_SIZE       = 2000  # bytes — sla te kleine icons/favicons over
MAX_IMAGES_MANIFEST  = 40    # max afbeeldingen in images.json voor generatie
MAX_IMAGES_BRIEF     = 30    # max afbeeldingen in briefing-prompt

# ── Tekst ─────────────────────────────────────────────────────────────────────
MAX_SITE_TEXT_CHARS   = 20000  # tekens websitetekst in briefing-prompt
MAX_CSS_CHARS         = 10000  # tekens CSS in briefing-prompt
MAX_RESEARCH_CHARS    = 15000  # tekens research in briefing-prompt
MAX_REF_TEXT_CHARS    = 8000   # tekens referentiesite-tekst in briefing-prompt
MAX_REF_CSS_CHARS     = 3000   # tekens referentiesite-CSS in briefing-prompt

# ── Kwaliteitschecks ──────────────────────────────────────────────────────────
MINIMUM_PAGES         = 1
MINIMUM_TEXT_CHARS    = 500
MIN_HOMEPAGE_CHARS    = 3000

# ── Screenshot-validatie ──────────────────────────────────────────────────────
SCREENSHOT_MAX_PAGES  = 4   # homepage altijd + 3 willekeurige subpagina's
SCREENSHOT_PORT       = 9876
