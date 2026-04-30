from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from v2.pipeline.models import SitePlan


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def route_href(slug: str) -> str:
    return "/" if not slug else f"/{slug}/"


def build_astro_source(plan: SitePlan, out_dir: Path, force: bool = False) -> None:
    if out_dir.exists() and force:
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    plan_json = json.dumps(plan.to_dict(), indent=2, ensure_ascii=False)

    write_file(out_dir / "package.json", json.dumps({
        "scripts": {
            "dev": "astro dev --host 0.0.0.0",
            "build": "astro build",
            "preview": "astro preview --host 0.0.0.0"
        },
        "dependencies": {
            "astro": "^5.0.0"
        },
        "devDependencies": {}
    }, indent=2))

    write_file(out_dir / "astro.config.mjs", """import { defineConfig } from "astro/config";

export default defineConfig({
  output: "static",
});
""")

    write_file(out_dir / "tsconfig.json", """{
  "extends": "astro/tsconfigs/strict"
}
""")

    write_file(out_dir / "src" / "data" / "sitePlan.json", plan_json + "\n")

    write_file(out_dir / "src" / "layouts" / "BaseLayout.astro", """---
import sitePlan from "../data/sitePlan.json";
const { title = sitePlan.company_name, description = "" } = Astro.props;
const theme = sitePlan.theme;
---
<!doctype html>
<html lang="nl">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="description" content={description || `Website preview voor ${sitePlan.company_name}`} />
    <title>{title}</title>
    <style define:vars={{
      primary: theme.primary,
      secondary: theme.secondary,
      accent: theme.accent,
      background: theme.background,
      text: theme.text,
    }}>
      :root {
        color-scheme: light;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: var(--background);
        color: var(--text);
      }
      * { box-sizing: border-box; }
      body { margin: 0; min-height: 100vh; background: var(--background); color: var(--text); }
      a { color: inherit; text-decoration: none; }
      .container { width: min(1120px, calc(100% - 32px)); margin: 0 auto; }
      .site-header { position: sticky; top: 0; z-index: 10; border-bottom: 1px solid color-mix(in srgb, var(--secondary), transparent 86%); background: color-mix(in srgb, white, var(--background) 20%); backdrop-filter: blur(14px); }
      .nav { height: 68px; display: flex; align-items: center; justify-content: space-between; gap: 24px; }
      .brand { font-weight: 800; letter-spacing: 0; color: var(--secondary); }
      .links { display: flex; align-items: center; gap: 18px; font-size: 14px; color: color-mix(in srgb, var(--secondary), white 25%); }
      .button { display: inline-flex; align-items: center; justify-content: center; min-height: 42px; padding: 0 18px; border-radius: 8px; background: var(--primary); color: white; font-weight: 700; }
      .button.secondary { background: transparent; color: var(--secondary); border: 1px solid color-mix(in srgb, var(--secondary), transparent 75%); }
      main { overflow: hidden; }
      .hero { padding: 88px 0 72px; background: linear-gradient(135deg, color-mix(in srgb, var(--primary), white 88%), color-mix(in srgb, var(--accent), white 86%)); }
      .hero-grid { display: grid; grid-template-columns: 1.1fr .9fr; gap: 48px; align-items: center; }
      .eyebrow { color: var(--primary); font-weight: 800; text-transform: uppercase; font-size: 12px; letter-spacing: .08em; }
      h1 { font-size: clamp(42px, 6vw, 76px); line-height: .95; margin: 12px 0 18px; color: var(--secondary); letter-spacing: 0; }
      h2 { font-size: clamp(28px, 4vw, 44px); line-height: 1.05; color: var(--secondary); margin: 0 0 14px; letter-spacing: 0; }
      p { line-height: 1.7; }
      .lead { font-size: 18px; color: color-mix(in srgb, var(--secondary), white 28%); max-width: 620px; }
      .actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 28px; }
      .visual { min-height: 360px; border-radius: 16px; background: radial-gradient(circle at 20% 20%, var(--accent), transparent 32%), linear-gradient(135deg, var(--secondary), var(--primary)); box-shadow: 0 24px 80px color-mix(in srgb, var(--primary), transparent 65%); }
      section { padding: 76px 0; }
      .muted-band { background: color-mix(in srgb, var(--primary), white 94%); }
      .cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; margin-top: 28px; }
      .card { border: 1px solid color-mix(in srgb, var(--secondary), transparent 86%); border-radius: 10px; padding: 22px; background: white; box-shadow: 0 10px 32px rgba(15, 23, 42, .06); }
      .card strong { color: var(--secondary); }
      .split { display: grid; grid-template-columns: .85fr 1.15fr; gap: 44px; align-items: start; }
      .contact-box { border-radius: 12px; padding: 28px; background: var(--secondary); color: white; }
      .contact-box a { text-decoration: underline; text-underline-offset: 4px; }
      .site-footer { padding: 36px 0; color: white; background: var(--secondary); }
      @media (max-width: 820px) {
        .links { display: none; }
        .hero-grid, .split, .cards { grid-template-columns: 1fr; }
        .hero { padding-top: 58px; }
        .visual { min-height: 240px; }
      }
    </style>
  </head>
  <body>
    <header class="site-header">
      <nav class="container nav">
        <a class="brand" href="/">{sitePlan.company_name}</a>
        <div class="links">
          {sitePlan.pages.map((page) => <a href={page.slug ? `/${page.slug}/` : "/"}>{page.title}</a>)}
        </div>
        {sitePlan.contact.phone && <a class="button" href={`tel:${sitePlan.contact.phone}`}>Bel direct</a>}
      </nav>
    </header>
    <main>
      <slot />
    </main>
    <footer class="site-footer">
      <div class="container">
        <strong>{sitePlan.company_name}</strong>
        <p>{sitePlan.contact.address || sitePlan.source_url}</p>
      </div>
    </footer>
  </body>
</html>
""")

    write_file(out_dir / "src" / "components" / "SectionRenderer.astro", """---
const { section, sitePlan } = Astro.props;
const content = section.content || {};
---
{section.type === "hero" && (
  <section class="hero">
    <div class="container hero-grid">
      <div>
        <div class="eyebrow">{content.eyebrow}</div>
        <h1>{content.headline}</h1>
        <p class="lead">{content.body}</p>
        <div class="actions">
          <a class="button" href="/contact/">{content.primaryCta}</a>
          <a class="button secondary" href="/diensten/">{content.secondaryCta}</a>
        </div>
      </div>
      <div class="visual" aria-hidden="true"></div>
    </div>
  </section>
)}
{section.type === "services" && (
  <section>
    <div class="container">
      <h2>{content.title}</h2>
      <div class="cards">
        {(content.items || []).map((item) => (
          <article class="card">
            <strong>{item}</strong>
            <p>Een heldere presentatie met focus op vertrouwen, lokale herkenbaarheid en directe actie.</p>
          </article>
        ))}
      </div>
    </div>
  </section>
)}
{section.type === "about" && (
  <section class="muted-band">
    <div class="container split">
      <div><h2>{content.title}</h2></div>
      <div><p>{content.body}</p></div>
    </div>
  </section>
)}
{section.type === "contact" && (
  <section>
    <div class="container contact-box">
      <h2>{content.title}</h2>
      {sitePlan.contact.phone && <p>Telefoon: <a href={`tel:${sitePlan.contact.phone}`}>{sitePlan.contact.phone}</a></p>}
      {sitePlan.contact.email && <p>E-mail: <a href={`mailto:${sitePlan.contact.email}`}>{sitePlan.contact.email}</a></p>}
      {sitePlan.contact.address && <p>Adres: {sitePlan.contact.address}</p>}
    </div>
  </section>
)}
""")

    write_file(out_dir / "src" / "pages" / "index.astro", """---
import BaseLayout from "../layouts/BaseLayout.astro";
import SectionRenderer from "../components/SectionRenderer.astro";
import sitePlan from "../data/sitePlan.json";
---
<BaseLayout title={sitePlan.company_name}>
  {sitePlan.sections.map((section) => <SectionRenderer section={section} sitePlan={sitePlan} />)}
</BaseLayout>
""")

    for page in plan.pages:
        if not page.slug:
            continue
        write_file(out_dir / "src" / "pages" / page.slug / "index.astro", f"""---
import BaseLayout from "../../layouts/BaseLayout.astro";
import SectionRenderer from "../../components/SectionRenderer.astro";
import sitePlan from "../../data/sitePlan.json";
const page = sitePlan.pages.find((item) => item.slug === "{page.slug}") || {{ title: "{page.title}", description: "" }};
---
<BaseLayout title={{`${{page.title}} | ${{sitePlan.company_name}}`}} description={{page.description}}>
  <section class="hero">
    <div class="container">
      <div class="eyebrow">{{sitePlan.company_name}}</div>
      <h1>{{page.title}}</h1>
      <p class="lead">{{page.description || "Een heldere pagina op basis van de bestaande briefing."}}</p>
    </div>
  </section>
  {{sitePlan.sections.filter((section) => section.type !== "hero").map((section) => <SectionRenderer section={{section}} sitePlan={{sitePlan}} />)}}
</BaseLayout>
""")


def maybe_build(out_dir: Path) -> bool:
    npm = shutil.which("npm")
    if not npm:
        print("[WARN] npm niet gevonden; Astro build overgeslagen")
        return False
    if not (out_dir / "node_modules").exists():
        print("[WARN] node_modules ontbreekt; run eerst `npm install` in astro-source")
        return False
    result = subprocess.run([npm, "run", "build"], cwd=str(out_dir))
    return result.returncode == 0
