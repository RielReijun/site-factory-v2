"""Render een archetype-Astro project: kopieer templates, schrijf sitePlan.json,
plaats assets in public/.

De template-bestanden zijn statisch op disk onder
``v2/templates/astro/archetypes/<name>/``. Deze module produceert nooit Astro
code on-the-fly, dat blijft de verantwoordelijkheid van de templates.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable

PROJECT_DIR = Path(__file__).resolve().parents[2]
TEMPLATES_ROOT = PROJECT_DIR / "templates" / "astro" / "archetypes"

AssetPicker = Callable[[Path, dict[str, Any]], dict[str, Any]]


def render_archetype_site(
    *,
    template_dir_name: str,
    plan: dict[str, Any],
    collected_path: Path,
    out_dir: Path,
    force: bool = False,
    asset_picker: AssetPicker | None = None,
) -> None:
    """Bouw een Astro-project voor een archetype. Pure copy-en-render, geen LLM."""
    template_root = TEMPLATES_ROOT / template_dir_name
    if not template_root.exists():
        raise FileNotFoundError(f"Archetype template-dir niet gevonden: {template_root}")

    if out_dir.exists() and force:
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    _copy_templates(template_root, out_dir)

    if asset_picker is not None:
        asset_result = asset_picker(collected_path, plan)
        _copy_assets(asset_result.get("copies", []), out_dir / "public")
        plan = _apply_patches(plan, asset_result.get("patches", {}))

    plan_path = out_dir / "src" / "data" / "sitePlan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps(plan, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ── copy helpers ─────────────────────────────────────────────────────────────
_IGNORE_NAMES = {"node_modules", "dist", ".astro", ".gitkeep"}


def _copy_templates(src_root: Path, dst_root: Path) -> None:
    for src in src_root.rglob("*"):
        rel = src.relative_to(src_root)
        # Skip .gitkeep; die zijn alleen om lege dirs te bewaren in git.
        if any(part in _IGNORE_NAMES for part in rel.parts):
            continue
        dst = dst_root / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def _copy_assets(copies: list[tuple[Path, str]], public_root: Path) -> None:
    public_root.mkdir(parents=True, exist_ok=True)
    for src, rel_dst in copies:
        if not src.exists():
            continue
        dst = public_root / rel_dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


# ── plan patch helpers ───────────────────────────────────────────────────────
def _apply_patches(plan: dict[str, Any], patches: dict[str, Any]) -> dict[str, Any]:
    """Vul URL-velden in plan in op basis van wat de asset_picker heeft gekopieerd."""
    if not patches:
        return plan

    if "logo" in patches:
        plan["logo"] = patches["logo"]

    if "hero_image" in patches and isinstance(plan.get("hero"), dict):
        plan["hero"]["image"] = patches["hero_image"]

    if "about_image" in patches and isinstance(plan.get("about"), dict):
        plan["about"]["image"] = patches["about_image"]

    if "service_images" in patches:
        services = plan.get("services") or []
        for index, image_url in enumerate(patches["service_images"]):
            if index >= len(services):
                break
            if image_url:
                services[index]["image"] = image_url

    if "gallery" in patches:
        plan["gallery"] = patches["gallery"]

    return plan
