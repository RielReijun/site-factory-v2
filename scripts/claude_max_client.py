"""
claude_max_client.py — Drop-in vervanging voor anthropic.Anthropic()
die calls routeert via de lokale bridge (Claude Max-account).

Gebruik:
  from claude_max_client import get_claude_client
  client = get_claude_client()   # geeft Max-client of API-client op basis van USE_CLAUDE_MAX env

De client heeft dezelfde interface als anthropic.Anthropic() voor
de calls die we in de pipeline gebruiken:
  client.messages.create(model=..., max_tokens=..., messages=[...])
  client.messages.stream(...)   → context manager met .text_stream en .get_final_message()
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Iterator

import requests

def _resolve_bridge_url() -> str:
    """Probeer host.docker.internal, val terug op het standaard Docker gateway-adres."""
    default = os.getenv("BRIDGE_URL", "http://host.docker.internal:8182")
    if "host.docker.internal" not in default:
        return default
    try:
        import socket
        socket.getaddrinfo("host.docker.internal", 8182, timeout=1)
        return default
    except Exception:
        return "http://172.31.0.1:8182"

BRIDGE_URL = _resolve_bridge_url()


# ── Nep-response objecten die de Anthropic SDK nabootsen ─────────────────────

class _Usage:
    def __init__(self, input_tokens=0, output_tokens=0):
        self.input_tokens  = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens  = 0
        self.cache_creation_input_tokens = 0


class _ContentBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _Message:
    def __init__(self, content: str, input_tokens=0, output_tokens=0):
        self.content     = [_ContentBlock(content)]
        self.stop_reason = "end_turn"
        self.usage       = _Usage(input_tokens, output_tokens)


class _StreamContext:
    """Context manager die .text_stream en .get_final_message() biedt."""

    def __init__(self, content: str, input_tokens=0, output_tokens=0):
        self._content       = content
        self._input_tokens  = input_tokens
        self._output_tokens = output_tokens

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    @property
    def text_stream(self) -> Iterator[str]:
        # Lever de tekst in blokken van ~50 chars voor nep-streaming
        chunk_size = 50
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i:i + chunk_size]

    def get_final_message(self) -> _Message:
        return _Message(self._content, self._input_tokens, self._output_tokens)


# ── Max-client ────────────────────────────────────────────────────────────────

class _MaxMessages:
    """Vervangt client.messages — routeert via bridge."""

    def _call_bridge(self, messages: list, system: str = "", **kwargs) -> str:
        """Roep de bridge aan en geef de tekst terug."""
        # Bouw prompt op uit messages-lijst (zelfde als Anthropic SDK)
        parts = []
        for msg in messages:
            role    = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                # content met cache_control blokken
                content = " ".join(
                    b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in content
                )
            parts.append(f"[{role.upper()}]: {content}")
        prompt = "\n\n".join(parts)

        MAX_RETRIES = 3
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = requests.post(
                    f"{BRIDGE_URL}/api/claude",
                    json={"prompt": prompt, "system": system},
                    timeout=620,
                )
                data = r.json()
                if data.get("error"):
                    raise RuntimeError(data["error"])
                return data.get("content", "")
            except Exception as e:
                if attempt < MAX_RETRIES:
                    wait = 30 * attempt
                    print(f"[WARN] claude_max_client: poging {attempt} mislukt ({e}), wacht {wait}s")
                    time.sleep(wait)
                else:
                    raise

    def create(self, model: str = "", max_tokens: int = 4000,
               messages: list = None, system: str = "", **kwargs) -> _Message:
        """Vervangt client.messages.create()"""
        content = self._call_bridge(messages or [], system=system)
        # Schat tokens op basis van tekst (ruwe benadering)
        in_tok  = sum(len(str(m.get("content", ""))) for m in (messages or [])) // 4
        out_tok = len(content) // 4
        return _Message(content, in_tok, out_tok)

    def stream(self, model: str = "", max_tokens: int = 4000,
               messages: list = None, system: str = "", **kwargs) -> _StreamContext:
        """Vervangt client.messages.stream() — context manager"""
        content = self._call_bridge(messages or [], system=system)
        in_tok  = sum(len(str(m.get("content", ""))) for m in (messages or [])) // 4
        out_tok = len(content) // 4
        return _StreamContext(content, in_tok, out_tok)


class ClaudeMaxClient:
    """Drop-in vervanging voor anthropic.Anthropic() via Max-bridge."""

    def __init__(self):
        self.messages = _MaxMessages()

    @staticmethod
    def _check_bridge() -> bool:
        try:
            r = requests.get(f"{BRIDGE_URL}/health", timeout=5)
            return r.ok
        except Exception:
            return False


# ── Factory ───────────────────────────────────────────────────────────────────

def get_claude_client(api_key: str = ""):
    """
    Geeft de juiste client op basis van USE_CLAUDE_MAX env-variabele.

    USE_CLAUDE_MAX=true  → ClaudeMaxClient (via bridge, gratis met Max)
    USE_CLAUDE_MAX=false → anthropic.Anthropic() (API-billing)

    BELANGRIJK: bij USE_CLAUDE_MAX=true val NOOIT terug op de API — dit voorkomt
    onverwachte API-kosten als de bridge tijdelijk onbereikbaar is. Faal hard
    zodat je het probleem ziet en zelf kan kiezen om over te schakelen.
    """
    if os.getenv("USE_CLAUDE_MAX", "").lower() in ("true", "1", "yes"):
        client = ClaudeMaxClient()
        if client._check_bridge():
            print("[INFO] Claude Max-client actief (via bridge)")
            return client
        raise RuntimeError(
            f"USE_CLAUDE_MAX=true maar bridge niet bereikbaar op {BRIDGE_URL}. "
            "Geen automatische fallback naar Anthropic API om onverwachte kosten te vermijden. "
            "Start de bridge of zet USE_CLAUDE_MAX=false om bewust de API te gebruiken."
        )

    from anthropic import Anthropic
    key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY ontbreekt en USE_CLAUDE_MAX is niet ingesteld")
    return Anthropic(api_key=key)
