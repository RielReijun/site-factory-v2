"""
deploy.py — Push gegenereerde site naar GitHub en stel Cloudflare Pages in.

Eerste deploy: maakt GitHub repo aan, pusht bestanden, maakt Cloudflare Pages
               project aan gekoppeld aan die repo.
Volgende deploys: alleen push naar GitHub (Cloudflare deployt automatisch).

Vereiste env-variabelen:
  GITHUB_TOKEN           — GitHub personal access token (repo scope)
  GITHUB_USERNAME        — GitHub gebruikersnaam of org
  CLOUDFLARE_ACCOUNT_ID  — Cloudflare account ID (optioneel)
  CLOUDFLARE_API_TOKEN   — Cloudflare API token met Pages:Edit permissie (optioneel)
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import requests

GITHUB_API = "https://api.github.com"
CF_API     = "https://api.cloudflare.com/client/v4"


def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _cf_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ── GitHub ────────────────────────────────────────────────────────────────────

def github_repo_exists(token: str, username: str, repo_name: str) -> bool:
    r = requests.get(
        f"{GITHUB_API}/repos/{username}/{repo_name}",
        headers=_gh_headers(token), timeout=15,
    )
    return r.status_code == 200


def create_github_repo(token: str, username: str, repo_name: str, description: str = "") -> dict:
    payload = {
        "name":        repo_name,
        "description": description,
        "private":     True,
        "auto_init":   False,
    }
    r = requests.post(
        f"{GITHUB_API}/user/repos",
        headers=_gh_headers(token), json=payload, timeout=15,
    )
    if not r.ok:
        raise RuntimeError(f"GitHub repo aanmaken mislukt: {r.status_code} {r.text[:200]}")
    return r.json()


def _run(cmd: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def push_to_github(site_dir: Path, slug: str, company_name: str,
                   token: str, username: str) -> str:
    """Push site_dir naar GitHub. Maakt repo aan als die nog niet bestaat. Geeft HTML-URL terug."""
    repo_name = f"site-{slug}"
    clone_url = f"https://{username}:{token}@github.com/{username}/{repo_name}.git"
    html_url  = f"https://github.com/{username}/{repo_name}"

    if not github_repo_exists(token, username, repo_name):
        print(f"[INFO] GitHub repo aanmaken: {html_url}")
        create_github_repo(token, username, repo_name,
                           description=f"Gegenereerde site voor {company_name}")
        print(f"[OK]  Repo aangemaakt: {html_url}")
    else:
        print(f"[INFO] GitHub repo bestaat al: {html_url}")

    git_dir = site_dir / ".git"
    cmds: list[list[str]] = []

    if not git_dir.exists():
        cmds += [
            ["git", "init", "-b", "main"],
            ["git", "remote", "add", "origin", clone_url],
        ]
    else:
        cmds += [
            ["git", "remote", "set-url", "origin", clone_url],
        ]

    cmds += [
        ["git", "add", "."],
        [
            "git", "-c", "user.email=deploy@site-factory.local",
            "-c", "user.name=SiteFactory",
            "commit", "-m", f"Deploy {company_name}", "--allow-empty",
        ],
        ["git", "push", "-f", "origin", "main"],
    ]

    for cmd in cmds:
        display = " ".join(cmd[:4])
        print(f"[INFO] git {display}...")
        rc, out, err = _run(cmd, cwd=str(site_dir))
        if rc != 0:
            # commit --allow-empty kan slagen met exit 0 maar ook met een melding
            relevant = err or out
            if relevant:
                print(f"[WARN] {relevant[:300]}")
        elif out:
            print(f"       {out[:200]}")

    print(f"[OK]  Gepusht naar GitHub: {html_url}")
    return html_url


# ── Cloudflare ────────────────────────────────────────────────────────────────

def cf_project_exists(token: str, account_id: str, project_name: str) -> dict | None:
    r = requests.get(
        f"{CF_API}/accounts/{account_id}/pages/projects/{project_name}",
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    if r.status_code == 200:
        return r.json().get("result")
    return None


def create_cf_pages_project(token: str, account_id: str, project_name: str,
                             github_username: str, repo_name: str) -> dict:
    payload = {
        "name":             project_name,
        "production_branch": "main",
        "source": {
            "type": "github",
            "config": {
                "owner":               github_username,
                "repo_name":           repo_name,
                "production_branch":   "main",
                "deployments_enabled": True,
            },
        },
        "build_config": {
            "build_command":   "",
            "destination_dir": "/",
            "root_dir":        "/",
        },
    }
    r = requests.post(
        f"{CF_API}/accounts/{account_id}/pages/projects",
        headers=_cf_headers(token), json=payload, timeout=30,
    )
    if not r.ok:
        raise RuntimeError(
            f"Cloudflare Pages project aanmaken mislukt: {r.status_code} {r.text[:300]}"
        )
    return r.json().get("result", {})


def trigger_cf_deployment(token: str, account_id: str, project_name: str) -> None:
    """Triggert een nieuwe deployment voor een bestaand Cloudflare Pages project."""
    r = requests.post(
        f"{CF_API}/accounts/{account_id}/pages/projects/{project_name}/deployments",
        headers={"Authorization": f"Bearer {token}"}, timeout=30,
    )
    if r.ok:
        print(f"[OK]  Cloudflare deployment getriggerd")
    else:
        print(f"[WARN] Deployment trigger mislukt: {r.status_code} {r.text[:200]}")


def setup_cloudflare(token: str, account_id: str, project_name: str,
                     github_username: str, repo_name: str) -> str:
    """Maakt Cloudflare Pages project aan als het nog niet bestaat. Geeft live URL terug."""
    existing = cf_project_exists(token, account_id, project_name)
    if existing:
        subdomain = existing.get("subdomain", "").removesuffix(".pages.dev")
        url = f"https://{subdomain}.pages.dev" if subdomain else ""
        print(f"[INFO] Cloudflare project bestaat al: {url or project_name}")
        return url

    print(f"[INFO] Cloudflare Pages project aanmaken: {project_name}")
    result = create_cf_pages_project(token, account_id, project_name, github_username, repo_name)
    subdomain = result.get("subdomain", "")
    url = f"https://{subdomain}.pages.dev" if subdomain else ""
    print(f"[OK]  Cloudflare project aangemaakt: {url or project_name}")

    # Eerste deployment triggeren (anders staat het project leeg)
    print(f"[INFO] Eerste Cloudflare deployment triggeren...")
    trigger_cf_deployment(token, account_id, project_name)

    return url


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug",     required=True, help="Prospect slug")
    parser.add_argument("--company",  required=True, help="Bedrijfsnaam")
    parser.add_argument("--site-dir", required=True, help="Pad naar gegenereerde site")
    args = parser.parse_args()

    github_token  = os.getenv("GITHUB_TOKEN",          "")
    github_user   = os.getenv("GITHUB_USERNAME",        "")
    cf_account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID",  "")
    cf_api_token  = os.getenv("CLOUDFLARE_API_TOKEN",   "")

    if not github_token or not github_user:
        print("[FAIL] GITHUB_TOKEN en GITHUB_USERNAME zijn verplicht in .env")
        sys.exit(1)

    site_dir = Path(args.site_dir)
    if not site_dir.is_dir():
        print(f"[FAIL] Site-map niet gevonden: {site_dir}")
        sys.exit(1)

    slug      = args.slug
    company   = args.company
    repo_name = f"site-{slug}"
    cf_name   = f"site-{slug}"

    # Stap 1: push naar GitHub
    print(f"\n[INFO] Stap 1/2: Push naar GitHub...")
    github_url = push_to_github(site_dir, slug, company, github_token, github_user)

    # Stap 2: Cloudflare Pages (optioneel)
    cloudflare_url = ""
    if cf_account_id and cf_api_token:
        print(f"\n[INFO] Stap 2/2: Cloudflare Pages instellen...")
        try:
            cloudflare_url = setup_cloudflare(cf_api_token, cf_account_id,
                                              cf_name, github_user, repo_name)
        except RuntimeError as e:
            print(f"[WARN] Cloudflare stap overgeslagen: {e}")
    else:
        print(f"\n[INFO] Stap 2/2: Cloudflare niet geconfigureerd — overgeslagen")

    # Stuur resultaat als JSON naar stdout zodat server.py het kan parsen
    print("\n===DEPLOY_RESULT===")
    print(json.dumps({"github_url": github_url, "cloudflare_url": cloudflare_url}))
    print("===END_DEPLOY_RESULT===")

    print(f"\n[OK]  Deploy klaar")
    if cloudflare_url:
        print(f"[OK]  Live op: {cloudflare_url}")
    else:
        print(f"[OK]  GitHub: {github_url}")


if __name__ == "__main__":
    main()
