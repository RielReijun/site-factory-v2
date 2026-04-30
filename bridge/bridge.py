#!/usr/bin/env python3
"""
bridge.py — Lokale HTTP-brug die claude CLI aanroept namens het dashboard.

Draait op de HOST (niet in Docker) op poort 8182.
Het dashboard (in Docker) roept http://host.docker.internal:8182/chat aan.
Claude gebruikt het Max-account en de MCP-tools uit .claude/settings.json.

Start: python3 bridge.py
"""
import glob
import json
import os
import subprocess
from pathlib import Path

from flask import Flask, Response, request, stream_with_context

PROJECT_DIR = Path(__file__).parent.parent
BRIDGE_PORT = 8182

app = Flask(__name__)
BRIDGE_TOKEN = (os.environ.get("BRIDGE_TOKEN") or os.environ.get("DASHBOARD_TOKEN") or "").strip()


def _authorized() -> bool:
    if not BRIDGE_TOKEN:
        return True
    return request.headers.get("X-Site-Factory-Token", "") == BRIDGE_TOKEN


@app.before_request
def require_bridge_token():
    if request.endpoint == "health":
        return None
    if _authorized():
        return None
    return {"error": "Unauthorized"}, 401


def _find_claude() -> str:
    """Zoek de claude-binary via gemounte extensiepaden of PATH."""
    patterns = [
        "/vscode-extensions/anthropic.claude-code-*/resources/native-binary/claude",
        str(Path.home() / ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude"),
    ]
    for pattern in patterns:
        matches = sorted(glob.glob(pattern), reverse=True)
        if matches:
            return matches[0]
    for candidate in ["/usr/local/bin/claude", "/usr/bin/claude"]:
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError("claude binary niet gevonden. Stel CLAUDE_BIN in als env-variabele.")


CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or _find_claude()


@app.route("/health")
def health():
    return {"ok": True, "claude": CLAUDE_BIN}


@app.route("/api/claude", methods=["POST"])
def claude_api():
    """
    Drop-in vervanging voor anthropic.messages.create() via Claude Max.
    Verwacht: {"prompt": "...", "max_tokens": 4000, "system": "..."}
    Geeft terug: {"content": "...", "error": null}
    """
    data       = request.get_json(silent=True) or {}
    prompt     = data.get("prompt", "").strip()
    system     = data.get("system", "").strip()
    max_tokens = data.get("max_tokens", 8000)

    if not prompt:
        return {"error": "Geen prompt"}, 400

    # Combineer system + user prompt voor claude --print
    full_prompt = f"{system}\n\n{prompt}" if system else prompt

    # ANTHROPIC_API_KEY uit env strippen voor claude subprocess: anders pakt
    # de CLI die voor billing i.p.v. de Claude Max OAuth-sessie ('apiKeySource'
    # = ANTHROPIC_API_KEY → API charges). Met lege key valt hij terug op OAuth.
    claude_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    try:
        result = subprocess.run(
            [CLAUDE_BIN, "--print", "--output-format", "text", full_prompt],
            cwd="/tmp",  # neutrale dir, geen CLAUDE.md/project-context die tool-use triggert
            capture_output=True, text=True, timeout=600,
            env=claude_env,
        )
        if result.returncode == 0:
            return json.dumps({"content": result.stdout.strip(), "error": None}), 200, {"Content-Type": "application/json"}
        else:
            err_msg = (result.stderr.strip() or result.stdout.strip() or "Claude fout")
            return json.dumps({"content": "", "error": err_msg}), 500, {"Content-Type": "application/json"}
    except subprocess.TimeoutExpired:
        return json.dumps({"content": "", "error": "Timeout na 600s"}), 504, {"Content-Type": "application/json"}
    except Exception as e:
        return json.dumps({"content": "", "error": str(e)}), 500, {"Content-Type": "application/json"}


@app.route("/chat", methods=["POST"])
def chat():
    data    = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    if not message:
        return {"error": "Geen bericht"}, 400

    def _generate():
        try:
            proc = subprocess.Popen(
                [CLAUDE_BIN, "--print", "--output-format", "text", message],
                cwd=str(PROJECT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            for line in iter(proc.stdout.readline, ""):
                yield f"data: {json.dumps({'text': line})}\n\n"
            proc.wait(timeout=120)
            if proc.returncode != 0:
                err = proc.stderr.read().strip()
                yield f"data: {json.dumps({'error': err or 'Claude gaf een fout terug'})}\n\n"
            yield "data: [DONE]\n\n"
        except subprocess.TimeoutExpired:
            proc.kill()
            yield f"data: {json.dumps({'error': 'Timeout — Claude reageerde niet op tijd'})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
        },
    )


if __name__ == "__main__":
    print(f"[INFO] Bridge gestart op poort {BRIDGE_PORT}")
    print(f"[INFO] Claude binary: {CLAUDE_BIN}")
    print(f"[INFO] Project dir:   {PROJECT_DIR}")
    app.run(host="0.0.0.0", port=BRIDGE_PORT, debug=False, threaded=True)
