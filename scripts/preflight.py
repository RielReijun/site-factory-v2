#!/usr/bin/env python3
"""
preflight.py — snelle sanity-check voor Site Factory.

Controleert lokale config zonder secrets te printen:
- .env aanwezig en basisvariabelen gezet
- prospects.json parsebaar
- Docker services bereikbaar
- dashboard/bridge auth werkt
- geen tokenized GitHub remotes in output-repo's
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_DIR / ".env"
DATA_DIR = PROJECT_DIR / "data"
OUTPUT_DIR = PROJECT_DIR / "output"


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if not ENV_FILE.exists():
        return env
    for raw in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def _http_status(url: str, token: str = "", method: str = "GET") -> int | None:
    headers = {}
    if token:
        headers["X-Site-Factory-Token"] = token
    req = Request(url, method=method, headers=headers)
    try:
        with urlopen(req, timeout=5) as response:
            return response.status
    except HTTPError as e:
        return e.code
    except URLError:
        return None


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode, (result.stdout + result.stderr).strip()
    except Exception as e:
        return 1, str(e)


def _read_text_with_container_fallback(host_path: Path, container_path: str) -> str:
    try:
        return host_path.read_text(encoding="utf-8", errors="ignore")
    except PermissionError:
        rc, out = _run(["docker", "exec", "site-factory-worker", "cat", container_path])
        if rc == 0:
            return out
        raise


def _check_tokenized_remotes() -> list[Path]:
    pattern = re.compile(r"https://[^/\s]+@github\.com/")
    hits: list[Path] = []
    if not OUTPUT_DIR.exists():
        return hits
    for config in OUTPUT_DIR.glob("*/out/.git/config"):
        try:
            if pattern.search(config.read_text(encoding="utf-8", errors="ignore")):
                hits.append(config)
        except OSError:
            hits.append(config)
    return hits


def main() -> int:
    env = _load_env()
    failures: list[str] = []
    warnings: list[str] = []

    def ok(msg: str) -> None:
        print(f"[OK]   {msg}")

    def warn(msg: str) -> None:
        warnings.append(msg)
        print(f"[WARN] {msg}")

    def fail(msg: str) -> None:
        failures.append(msg)
        print(f"[FAIL] {msg}")

    if ENV_FILE.exists():
        ok(".env gevonden")
    else:
        fail(".env ontbreekt")

    if env.get("USE_CLAUDE_MAX", "").lower() in {"true", "1", "yes"}:
        ok("Claude Max mode actief")
    elif env.get("ANTHROPIC_API_KEY"):
        ok("Anthropic API key aanwezig")
    else:
        fail("Geen Claude Max mode en geen ANTHROPIC_API_KEY ingesteld")

    if env.get("DASHBOARD_TOKEN"):
        ok("DASHBOARD_TOKEN ingesteld")
    else:
        warn("DASHBOARD_TOKEN ontbreekt; zet deze voordat je dashboard remote expose't")

    if env.get("GITHUB_TOKEN") and env.get("GITHUB_USERNAME"):
        ok("GitHub deploy env aanwezig")
    else:
        warn("GitHub deploy env niet compleet")

    prospects_file = DATA_DIR / "prospects.json"
    if prospects_file.exists():
        try:
            prospects = json.loads(
                _read_text_with_container_fallback(
                    prospects_file,
                    "/workspace/data/prospects.json",
                )
            )
            ok(f"prospects.json parsebaar ({len(prospects)} prospects)")
        except Exception as e:
            fail(f"prospects.json niet parsebaar: {e}")
    else:
        fail("data/prospects.json ontbreekt")

    rc, out = _run(["docker", "compose", "ps", "--format", "json"])
    if rc == 0:
        ok("Docker Compose bereikbaar")
    else:
        fail(f"Docker Compose niet bereikbaar: {out[:160]}")

    token = env.get("DASHBOARD_TOKEN", "")
    health = _http_status("http://127.0.0.1:8181/health")
    if health == 200:
        ok("dashboard /health bereikbaar")
    else:
        fail(f"dashboard /health niet bereikbaar (status: {health})")

    api_no_token = _http_status("http://127.0.0.1:8181/api/status")
    if token:
        if api_no_token == 401:
            ok("dashboard API blokkeert requests zonder token")
        else:
            fail(f"dashboard API blokkeert zonder token niet correct (status: {api_no_token})")
        api_with_token = _http_status("http://127.0.0.1:8181/api/status", token=token)
        if api_with_token == 200:
            ok("dashboard API accepteert token")
        else:
            fail(f"dashboard API met token faalt (status: {api_with_token})")

    bridge = _http_status("http://127.0.0.1:8182/health")
    if bridge == 200:
        ok("bridge /health bereikbaar")
    else:
        warn(f"bridge /health niet bereikbaar (status: {bridge})")

    tokenized = _check_tokenized_remotes()
    if tokenized:
        fail(f"{len(tokenized)} tokenized GitHub remote(s) gevonden in output")
    else:
        ok("geen tokenized GitHub remotes in output gevonden")

    if warnings:
        print(f"\n[INFO] {len(warnings)} waarschuwing(en)")
    if failures:
        print(f"[FAIL] Preflight mislukt met {len(failures)} blocker(s)")
        return 1
    print("[OK]   Preflight geslaagd")
    return 0


if __name__ == "__main__":
    sys.exit(main())
