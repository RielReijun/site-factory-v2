"""
component_registry.py — Vaste, geteste Tailwind-secties als Python-functies.

Claude genereert een JSON-plan (welke secties, welke variant, welke content).
Deze module assembleert dat plan naar een complete page.tsx.

Secties:
  hero        — volledig beeld, split, of gradient
  services    — kaartjes-grid (3 of 4 koloms) of icon-list
  about       — tekst + beeld split (links of rechts)
  stats       — getallen/highlights balk
  cta         — call-to-action blok
  faq         — accordeon via shadcn
  contact     — formulier + contactinfo
  team        — teamleden cards
  gallery     — foto-grid
  testimonials — reviews/citaten

Gebruik:
  from component_registry import assemble_page
  tsx = assemble_page(plan, page_slug, company_name)
"""
from __future__ import annotations

import re
from typing import Any

# Iconen die ECHT bestaan in lucide-react (veilige subset)
SAFE_ICONS = {
    "Phone", "Mail", "MapPin", "Clock", "ChevronRight", "ChevronDown",
    "Check", "Star", "Scissors", "Sparkles", "Heart", "User", "Users",
    "Home", "Building", "Wrench", "Hammer", "Paintbrush", "Truck",
    "Camera", "Image", "Video", "Play", "Globe", "ExternalLink",
    "Menu", "X", "ArrowRight", "ArrowLeft", "Search", "Plus", "Minus",
    "MessageCircle", "Send", "Briefcase", "Shield", "Award", "Leaf",
    "Sun", "Moon", "Coffee", "Smile", "Zap", "Gift", "Calendar",
    "FileText", "Info", "AlertCircle", "CheckCircle", "XCircle",
    "Cpu", "Layers", "Package", "Settings", "Tool", "Shovel",
}


def _safe_icon(name: str, fallback: str = "Check") -> str:
    """Geeft een veilig Lucide-icon naam terug."""
    if name in SAFE_ICONS:
        return name
    # Probeer Case-insensitive match
    for safe in SAFE_ICONS:
        if safe.lower() == name.lower():
            return safe
    return fallback


def _img(src: str | None, alt: str, cls: str) -> str:
    if src:
        return f'<img src="/{src.lstrip("/")}" alt="{alt}" className="{cls}" />'
    return f'<div className="{cls} bg-gradient-to-br from-gray-200 to-gray-300" />'


# ── HERO ──────────────────────────────────────────────────────────────────────

def section_hero(c: dict, variant: str = "full-overlay") -> tuple[str, set[str]]:
    """Hero sectie. Varianten: full-overlay, split, gradient."""
    headline   = c.get("headline", "Welkom")
    subline    = c.get("subline", "")
    cta_text   = c.get("cta_primary_text", "Neem contact op")
    cta_link   = c.get("cta_primary_link", "/contact")
    cta2_text  = c.get("cta_secondary_text", "")
    cta2_link  = c.get("cta_secondary_link", "/diensten")
    image      = c.get("image", "")

    cta2_html = ""
    if cta2_text:
        cta2_html = f'''
              <Link href="{cta2_link}" className="inline-flex items-center gap-2 border-2 border-white/60 text-white hover:border-white hover:bg-white/10 font-semibold px-7 py-3.5 rounded-sm transition-all duration-200">
                {cta2_text} <ArrowRight className="w-4 h-4" />
              </Link>'''

    icons = {"ArrowRight"}

    if variant == "full-overlay":
        bg = f'style={{{{ backgroundImage: "url(/{image.lstrip("/")})" }}}}' if image else ""
        tsx = f'''
      <section className="relative min-h-[85vh] flex items-center overflow-hidden bg-gray-900" {bg}>
        {f'<div className="absolute inset-0 bg-cover bg-center bg-no-repeat" style={{{{ backgroundImage: "url(/{image.lstrip("/")})" }}}} />' if image else '<div className="absolute inset-0 bg-gradient-to-br from-gray-800 to-gray-900" />'}
        <div className="absolute inset-0 bg-black/55" />
        <div className="relative z-10 container mx-auto px-4 py-24">
          <div className="max-w-2xl">
            <h1 className="font-heading text-4xl md:text-5xl lg:text-6xl font-bold text-white leading-tight mb-5">
              {headline}
            </h1>
            {f'<p className="text-white/80 text-lg md:text-xl leading-relaxed mb-8 max-w-xl">{subline}</p>' if subline else ''}
            <div className="flex flex-col sm:flex-row gap-3">
              <Link href="{cta_link}" className="inline-flex items-center justify-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground font-semibold px-7 py-3.5 rounded-sm transition-colors duration-200">
                {cta_text} <ArrowRight className="w-4 h-4" />
              </Link>{cta2_html}
            </div>
          </div>
        </div>
      </section>'''
        return tsx, icons

    if variant == "split":
        tsx = f'''
      <section className="py-16 md:py-24 bg-background">
        <div className="container mx-auto px-4">
          <div className="flex flex-col md:flex-row items-center gap-12">
            <div className="flex-1">
              <h1 className="font-heading text-4xl md:text-5xl font-bold leading-tight mb-5">
                {headline}
              </h1>
              {f'<p className="text-muted-foreground text-lg leading-relaxed mb-8">{subline}</p>' if subline else ''}
              <div className="flex flex-col sm:flex-row gap-3">
                <Link href="{cta_link}" className="inline-flex items-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground font-semibold px-6 py-3 rounded-sm transition-colors">
                  {cta_text} <ArrowRight className="w-4 h-4" />
                </Link>{cta2_html}
              </div>
            </div>
            <div className="flex-1">
              {_img(image, headline, "w-full h-80 md:h-96 object-cover rounded-lg")}
            </div>
          </div>
        </div>
      </section>'''
        return tsx, icons

    # gradient fallback
    tsx = f'''
      <section className="py-24 md:py-32 bg-gradient-to-br from-primary/10 to-background">
        <div className="container mx-auto px-4 text-center">
          <h1 className="font-heading text-4xl md:text-5xl lg:text-6xl font-bold leading-tight mb-5 max-w-3xl mx-auto">
            {headline}
          </h1>
          {f'<p className="text-muted-foreground text-lg md:text-xl leading-relaxed mb-8 max-w-2xl mx-auto">{subline}</p>' if subline else ''}
          <div className="flex flex-col sm:flex-row gap-3 justify-center">
            <Link href="{cta_link}" className="inline-flex items-center gap-2 bg-primary hover:bg-primary/90 text-primary-foreground font-semibold px-7 py-3.5 rounded-sm transition-colors">
              {cta_text} <ArrowRight className="w-4 h-4" />
            </Link>{cta2_html}
          </div>
        </div>
      </section>'''
    return tsx, icons


# ── SERVICES ──────────────────────────────────────────────────────────────────

def section_services(c: dict, variant: str = "cards-3") -> tuple[str, set[str]]:
    headline = c.get("headline", "Onze diensten")
    intro    = c.get("intro", "")
    items    = c.get("items", [])
    icons_needed: set[str] = set()

    if not items:
        return "", set()

    cols = "3" if variant == "cards-3" else "4" if variant == "cards-4" else "3"
    grid_cls = f"grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-{cols} gap-6"

    cards_html = ""
    for item in items:
        icon_name = _safe_icon(item.get("icon", "Check"))
        icons_needed.add(icon_name)
        price = f'<p className="text-sm font-semibold text-primary mt-2">{item["price"]}</p>' if item.get("price") else ""
        cards_html += f'''
            <div className="bg-card border border-border rounded-xl p-6 hover:shadow-md transition-shadow">
              <{icon_name} className="w-8 h-8 text-primary mb-4" />
              <h3 className="font-heading font-semibold text-lg mb-2">{item.get("title", "")}</h3>
              <p className="text-muted-foreground text-sm leading-relaxed">{item.get("description", "")}</p>
              {price}
            </div>'''

    tsx = f'''
      <section className="py-16 md:py-20 bg-background">
        <div className="container mx-auto px-4">
          <div className="text-center mb-12">
            <h2 className="font-heading text-3xl md:text-4xl font-bold mb-3">{headline}</h2>
            {f'<p className="text-muted-foreground text-lg max-w-2xl mx-auto">{intro}</p>' if intro else ''}
          </div>
          <div className="{grid_cls}">{cards_html}
          </div>
        </div>
      </section>'''
    return tsx, icons_needed


# ── ABOUT ─────────────────────────────────────────────────────────────────────

def section_about(c: dict, variant: str = "split-image-right") -> tuple[str, set[str]]:
    headline  = c.get("headline", "Over ons")
    body      = c.get("body", "")
    image     = c.get("image", "")
    cta_text  = c.get("cta_text", "")
    cta_link  = c.get("cta_link", "/contact")
    bullets   = c.get("bullets", [])
    icons: set[str] = {"Check"}

    bullets_html = ""
    if bullets:
        items = "".join(f'<li className="flex items-start gap-2"><Check className="w-5 h-5 text-primary mt-0.5 shrink-0" /><span>{b}</span></li>' for b in bullets)
        bullets_html = f'<ul className="space-y-2 mt-4 text-sm text-muted-foreground">{items}</ul>'

    cta_html = ""
    if cta_text:
        icons.add("ArrowRight")
        cta_html = f'<Link href="{cta_link}" className="inline-flex items-center gap-2 text-primary font-semibold hover:underline mt-4"><span>{cta_text}</span><ArrowRight className="w-4 h-4" /></Link>'

    img_html = _img(image, headline, "w-full h-72 md:h-full object-cover rounded-xl")
    text_col  = f'''
            <div className="flex flex-col justify-center">
              <h2 className="font-heading text-3xl md:text-4xl font-bold mb-4">{headline}</h2>
              <p className="text-muted-foreground leading-relaxed">{body}</p>
              {bullets_html}
              {cta_html}
            </div>'''
    image_col = f'<div className="w-full md:w-1/2">{img_html}</div>'
    text_div  = f'<div className="w-full md:w-1/2">{text_col}</div>'

    order = f'{image_col}{text_div}' if variant == "split-image-left" else f'{text_div}{image_col}'

    tsx = f'''
      <section className="py-16 md:py-20 bg-muted/30">
        <div className="container mx-auto px-4">
          <div className="flex flex-col md:flex-row items-center gap-10 md:gap-16">
            {order}
          </div>
        </div>
      </section>'''
    return tsx, icons


# ── STATS ─────────────────────────────────────────────────────────────────────

def section_stats(c: dict, variant: str = "bar") -> tuple[str, set[str]]:
    headline = c.get("headline", "")
    items    = c.get("items", [])
    if not items:
        return "", set()

    stats_html = "".join(f'''
            <div className="text-center">
              <div className="font-heading text-4xl md:text-5xl font-bold text-primary">{item.get("value", "")}</div>
              <div className="text-sm text-muted-foreground mt-1">{item.get("label", "")}</div>
            </div>''' for item in items)

    tsx = f'''
      <section className="py-14 bg-primary text-primary-foreground">
        <div className="container mx-auto px-4">
          {f'<h2 className="font-heading text-2xl font-bold text-center mb-8 text-primary-foreground">{headline}</h2>' if headline else ''}
          <div className="grid grid-cols-2 md:grid-cols-{min(len(items), 4)} gap-8">
            {stats_html}
          </div>
        </div>
      </section>'''
    return tsx, set()


# ── CTA ───────────────────────────────────────────────────────────────────────

def section_cta(c: dict, variant: str = "centered") -> tuple[str, set[str]]:
    headline   = c.get("headline", "Klaar om te beginnen?")
    body       = c.get("body", "")
    cta_text   = c.get("cta_text", "Neem contact op")
    cta_link   = c.get("cta_link", "/contact")
    phone      = c.get("phone", "")
    whatsapp   = c.get("whatsapp", "")
    icons: set[str] = {"ArrowRight"}

    extra = ""
    if phone:
        icons.add("Phone")
        extra += f'<a href="tel:{re.sub(r"[^+0-9]", "", phone)}" className="inline-flex items-center gap-2 text-primary-foreground/80 hover:text-primary-foreground transition-colors"><Phone className="w-4 h-4" />{phone}</a>'
    if whatsapp:
        icons.add("MessageCircle")
        wa_num = re.sub(r"[^0-9]", "", whatsapp)
        extra += f'<a href="https://wa.me/31{wa_num.lstrip("0")}" target="_blank" rel="noopener" className="inline-flex items-center gap-2 text-primary-foreground/80 hover:text-primary-foreground transition-colors"><MessageCircle className="w-4 h-4" />WhatsApp</a>'

    tsx = f'''
      <section className="py-16 md:py-20 bg-primary text-primary-foreground">
        <div className="container mx-auto px-4 text-center">
          <h2 className="font-heading text-3xl md:text-4xl font-bold mb-3">{headline}</h2>
          {f'<p className="text-primary-foreground/80 text-lg mb-8 max-w-xl mx-auto">{body}</p>' if body else '<div className="mb-8" />'}
          <div className="flex flex-col sm:flex-row gap-4 justify-center items-center">
            <Link href="{cta_link}" className="inline-flex items-center gap-2 bg-white text-primary hover:bg-white/90 font-semibold px-7 py-3.5 rounded-sm transition-colors">
              {cta_text} <ArrowRight className="w-4 h-4" />
            </Link>
            {extra}
          </div>
        </div>
      </section>'''
    return tsx, icons


# ── FAQ ───────────────────────────────────────────────────────────────────────

def section_faq(c: dict, variant: str = "default") -> tuple[str, set[str]]:
    headline = c.get("headline", "Veelgestelde vragen")
    items    = c.get("items", [])
    if not items:
        return "", set()

    items_html = "".join(f'''
            <AccordionItem value="q{i}">
              <AccordionTrigger className="font-heading font-semibold text-left">{item.get("question", "")}</AccordionTrigger>
              <AccordionContent className="text-muted-foreground leading-relaxed">{item.get("answer", "")}</AccordionContent>
            </AccordionItem>''' for i, item in enumerate(items))

    tsx = f'''
      <section className="py-16 md:py-20 bg-background">
        <div className="container mx-auto px-4 max-w-3xl">
          <h2 className="font-heading text-3xl md:text-4xl font-bold text-center mb-10">{headline}</h2>
          <Accordion type="single" collapsible className="space-y-2">
            {items_html}
          </Accordion>
        </div>
      </section>'''
    return tsx, set()


# ── CONTACT ───────────────────────────────────────────────────────────────────

def section_contact(c: dict, variant: str = "with-form") -> tuple[str, set[str]]:
    headline = c.get("headline", "Contact")
    phone    = c.get("phone", "")
    email    = c.get("email", "")
    address  = c.get("address", "")
    icons: set[str] = {"Phone", "Mail", "MapPin"}

    info_html = ""
    if phone:
        wa_num = re.sub(r"[^0-9]", "", phone)
        icons.add("MessageCircle")
        info_html += f'''
              <div className="flex items-start gap-3">
                <Phone className="w-5 h-5 text-primary mt-0.5 shrink-0" />
                <div>
                  <div className="font-medium">Telefoon</div>
                  <a href="tel:{re.sub(r"[^+0-9]", "", phone)}" className="text-muted-foreground hover:text-primary">{phone}</a>
                  <br /><a href="https://wa.me/31{wa_num.lstrip("0")}" target="_blank" rel="noopener" className="text-sm text-primary hover:underline flex items-center gap-1 mt-1"><MessageCircle className="w-3.5 h-3.5" />WhatsApp</a>
                </div>
              </div>'''
    if email:
        info_html += f'''
              <div className="flex items-start gap-3">
                <Mail className="w-5 h-5 text-primary mt-0.5 shrink-0" />
                <div>
                  <div className="font-medium">E-mail</div>
                  <a href="mailto:{email}" className="text-muted-foreground hover:text-primary">{email}</a>
                </div>
              </div>'''
    if address:
        info_html += f'''
              <div className="flex items-start gap-3">
                <MapPin className="w-5 h-5 text-primary mt-0.5 shrink-0" />
                <div>
                  <div className="font-medium">Adres</div>
                  <p className="text-muted-foreground">{address}</p>
                </div>
              </div>'''

    form_html = '''
              <form action="https://formspree.io/f/FORMSPREE_ID" method="POST" className="space-y-4">
                <input type="hidden" name="_subject" value="Nieuw bericht via website" />
                <input type="text" name="_gotcha" style={{ display: "none" }} />
                <div>
                  <label className="block text-sm font-medium mb-1.5">Naam</label>
                  <input type="text" name="name" required className="w-full border border-input bg-background rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary" />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1.5">E-mail</label>
                  <input type="email" name="email" required className="w-full border border-input bg-background rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary" />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1.5">Bericht</label>
                  <textarea name="message" rows={5} required className="w-full border border-input bg-background rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary resize-none" />
                </div>
                <button type="submit" className="w-full bg-primary hover:bg-primary/90 text-primary-foreground font-semibold py-2.5 rounded-md transition-colors">
                  Verstuur bericht
                </button>
                {/* Vervang FORMSPREE_ID door uw eigen Formspree endpoint */}
              </form>''' if variant == "with-form" else ""

    tsx = f'''
      <section className="py-16 md:py-20 bg-muted/30">
        <div className="container mx-auto px-4">
          <h2 className="font-heading text-3xl md:text-4xl font-bold text-center mb-12">{headline}</h2>
          <div className="flex flex-col md:flex-row gap-12 max-w-4xl mx-auto">
            <div className="flex-1 space-y-6">
              {info_html}
            </div>
            {f'<div className="flex-1">{form_html}</div>' if form_html else ''}
          </div>
        </div>
      </section>'''
    return tsx, icons


# ── TEAM ──────────────────────────────────────────────────────────────────────

def section_team(c: dict, variant: str = "cards-3") -> tuple[str, set[str]]:
    headline = c.get("headline", "Ons team")
    members  = c.get("members", [])
    if not members:
        return "", set()

    cols = "3" if variant == "cards-3" else "4"
    cards = "".join(f'''
            <div className="text-center">
              {_img(m.get("photo", ""), m.get("name", ""), "w-32 h-32 rounded-full object-cover mx-auto mb-4")}
              <h3 className="font-heading font-semibold text-lg">{m.get("name", "")}</h3>
              <p className="text-primary text-sm font-medium">{m.get("role", "")}</p>
              {f'<p className="text-muted-foreground text-sm mt-1">{m["bio"]}</p>' if m.get("bio") else ''}
            </div>''' for m in members)

    tsx = f'''
      <section className="py-16 md:py-20 bg-background">
        <div className="container mx-auto px-4">
          <h2 className="font-heading text-3xl md:text-4xl font-bold text-center mb-12">{headline}</h2>
          <div className="grid grid-cols-2 md:grid-cols-{cols} gap-8">
            {cards}
          </div>
        </div>
      </section>'''
    return tsx, set()


# ── GALLERY ───────────────────────────────────────────────────────────────────

def section_gallery(c: dict, variant: str = "grid-3") -> tuple[str, set[str]]:
    headline = c.get("headline", "Galerij")
    images   = c.get("images", [])
    if not images:
        return "", set()

    cols = "3" if variant == "grid-3" else "4"
    imgs = "".join(f'<div className="overflow-hidden rounded-lg aspect-square"><img src="/{img.lstrip("/")}" alt="galerij" className="w-full h-full object-cover hover:scale-105 transition-transform duration-300" /></div>' for img in images[:12])

    tsx = f'''
      <section className="py-16 md:py-20 bg-muted/30">
        <div className="container mx-auto px-4">
          {f'<h2 className="font-heading text-3xl md:text-4xl font-bold text-center mb-10">{headline}</h2>' if headline else ''}
          <div className="grid grid-cols-2 md:grid-cols-{cols} gap-3">
            {imgs}
          </div>
        </div>
      </section>'''
    return tsx, set()


# ── TESTIMONIALS ──────────────────────────────────────────────────────────────

def section_testimonials(c: dict, variant: str = "cards") -> tuple[str, set[str]]:
    headline = c.get("headline", "Wat klanten zeggen")
    items    = c.get("items", [])
    if not items:
        return "", set()
    icons: set[str] = {"Star"}

    cards = "".join(f'''
            <div className="bg-card border border-border rounded-xl p-6">
              <div className="flex gap-1 mb-3">
                {"".join('<Star className="w-4 h-4 fill-primary text-primary" />' for _ in range(min(int(item.get("rating", 5)), 5)))}
              </div>
              <p className="text-muted-foreground leading-relaxed mb-4">"{item.get("text", "")}"</p>
              <div className="font-semibold text-sm">{item.get("name", "")}</div>
              {f'<div className="text-xs text-muted-foreground">{item["subtitle"]}</div>' if item.get("subtitle") else ''}
            </div>''' for item in items)

    tsx = f'''
      <section className="py-16 md:py-20 bg-muted/30">
        <div className="container mx-auto px-4">
          <h2 className="font-heading text-3xl md:text-4xl font-bold text-center mb-10">{headline}</h2>
          <div className="grid grid-cols-1 md:grid-cols-{min(len(items), 3)} gap-6">
            {cards}
          </div>
        </div>
      </section>'''
    return tsx, icons


# ── PAGE-HERO (subpagina intro) ────────────────────────────────────────────────

def section_page_hero(c: dict, variant: str = "default") -> tuple[str, set[str]]:
    title    = c.get("title", "")
    subtitle = c.get("subtitle", "")

    tsx = f'''
      <section className="py-16 bg-primary text-primary-foreground">
        <div className="container mx-auto px-4">
          <h1 className="font-heading text-3xl md:text-4xl font-bold mb-2">{title}</h1>
          {f'<p className="text-primary-foreground/80 text-lg">{subtitle}</p>' if subtitle else ''}
        </div>
      </section>'''
    return tsx, set()


# ── ASSEMBLER ─────────────────────────────────────────────────────────────────

SECTION_MAP = {
    "hero":         section_hero,
    "services":     section_services,
    "about":        section_about,
    "stats":        section_stats,
    "cta":          section_cta,
    "faq":          section_faq,
    "contact":      section_contact,
    "team":         section_team,
    "gallery":      section_gallery,
    "testimonials": section_testimonials,
    "page_hero":    section_page_hero,
}

ALWAYS_IMPORTS = [
    'import Link from "next/link";',
]
SHADCN_IMPORTS = {
    "Accordion": 'import { Accordion, AccordionItem, AccordionTrigger, AccordionContent } from "@/components/ui/accordion";',
    "Badge":     'import { Badge } from "@/components/ui/badge";',
    "Button":    'import { Button } from "@/components/ui/button";',
}


def assemble_page(plan: dict, page_slug: str, company_name: str) -> str:
    """
    Assembleert een complete page.tsx uit een JSON-plan.

    plan = {
        "sections": [
            {"type": "hero", "variant": "full-overlay", "content": {...}},
            ...
        ]
    }
    """
    sections     = plan.get("sections", [])
    all_icons:  set[str] = set()
    all_shadcn: set[str] = set()
    body_parts:  list[str] = []

    for sec in sections:
        sec_type = sec.get("type", "")
        variant  = sec.get("variant", "default")
        content  = sec.get("content", {})

        fn = SECTION_MAP.get(sec_type)
        if not fn:
            continue

        tsx, icons = fn(content, variant)
        if tsx.strip():
            body_parts.append(tsx)
            all_icons.update(icons)

        # Detecteer shadcn gebruik
        if "AccordionItem" in tsx:
            all_shadcn.add("Accordion")

    # Bouw component naam uit slug
    fn_name = "".join(w.capitalize() for w in re.sub(r"[^a-zA-Z0-9]", " ", page_slug).split()) or "Page"
    fn_name += "Page"

    # Bouw imports
    imports = list(ALWAYS_IMPORTS)
    if all_icons:
        safe_icons = sorted(i for i in all_icons if i in SAFE_ICONS)
        if safe_icons:
            imports.append(f'import {{ {", ".join(safe_icons)} }} from "lucide-react";')
    for key in sorted(all_shadcn):
        if key in SHADCN_IMPORTS:
            imports.append(SHADCN_IMPORTS[key])

    body = "\n".join(body_parts) if body_parts else "      <div />"

    return f"""{chr(10).join(imports)}

export default function {fn_name}() {{
  return (
    <main>
{body}
    </main>
  );
}}
"""


# ── SCHEMA VOOR CLAUDE ────────────────────────────────────────────────────────

def get_schema_description() -> str:
    """Geeft een beschrijving van het plan-schema voor gebruik in prompts."""
    return """
## Beschikbare secties en hun content-velden

### hero (verplicht op homepage)
Varianten: full-overlay (aanbevolen met foto), split (tekst+foto naast elkaar), gradient (geen foto)
Content: headline, subline, cta_primary_text, cta_primary_link, cta_secondary_text (opt), cta_secondary_link (opt), image (opt: pad naar foto)

### page_hero (verplicht op subpagina's)
Content: title, subtitle (opt)

### services
Varianten: cards-3, cards-4
Content: headline, intro (opt), items: [{icon (Lucide naam), title, description, price (opt)}]

### about
Varianten: split-image-right, split-image-left
Content: headline, body, image (opt), cta_text (opt), cta_link, bullets (opt: lijst strings)

### stats
Content: headline (opt), items: [{value, label}]

### cta (verplicht op elke pagina voor footer)
Content: headline, body (opt), cta_text, cta_link, phone (opt), whatsapp (opt)

### faq
Content: headline, items: [{question, answer}]

### contact
Varianten: with-form, info-only
Content: headline, phone, email, address

### team
Varianten: cards-3, cards-4
Content: headline, members: [{name, role, photo (opt), bio (opt)}]

### gallery
Varianten: grid-3, grid-4
Content: headline (opt), images: [paden naar foto's]

### testimonials
Content: headline, items: [{text, name, subtitle (opt), rating (1-5)}]

## Veilige Lucide iconen voor services:
Phone, Mail, MapPin, Clock, Check, Star, Scissors, Sparkles, Heart, User, Users,
Home, Building, Wrench, Hammer, Paintbrush, Truck, Camera, Globe, Shield, Award,
Leaf, Coffee, Smile, Zap, Gift, Calendar, FileText, Briefcase, Package, Layers
"""
