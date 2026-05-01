"""
voice_profile.py — Meet de toon van een prospect uit text.txt.

Bedoeld om de copy in render aan de tone-of-voice van de bron aan te passen
zonder LLM. Concrete signalen:

  je_count / u_count    informele 'je/jij' versus formele 'u/uw' aanspreekvorm
  je_u_ratio            je/(je+u) - 1.0 = pure je-vorm, 0.0 = pure u-vorm
  avg_sentence_length   gemiddeld aantal woorden per zin
  exclamations_per_1k   uitroeptekens per 1000 woorden

Daaruit leiden we af:
  addressing : "je" | "u" | "mixed"
  formality  : "informeel" | "formeel" | "gemengd"

Geport uit v2-archive — nu onderdeel van de v1 inventory-laag.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class VoiceProfile:
    je_count: int = 0
    u_count: int = 0
    je_u_ratio: float = 0.5
    avg_sentence_length: float = 0.0
    exclamations_per_1k_words: float = 0.0
    addressing: str = "unknown"
    formality: str = "gemengd"
    sample_words: int = 0
    sample_sentences: int = 0
    method: str = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_JE_RE = re.compile(r"\b(?:je|jij|jou|jouw|jullie)\b", re.IGNORECASE)
_U_RE = re.compile(r"\b(?:u|uw)\b", re.IGNORECASE)
_WORD_RE = re.compile(r"\b[a-zA-ZÀ-ſ'-]+\b")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+\s+")

_VOICE_SKIP_PAGES = re.compile(
    r"(?:privacy|voorwaarden|disclaimer|cookie|sitemap|404|nieuwsbrief|algemene)",
    re.IGNORECASE,
)
_PAGE_MARKER_RE = re.compile(r"^=+\s*Pagina:\s*(.*?)\s*=+$", re.MULTILINE)


def _split_pages(text: str) -> list[tuple[str, str]]:
    parts = _PAGE_MARKER_RE.split(text)
    if len(parts) < 3:
        return [("", text)]
    pages: list[tuple[str, str]] = []
    if parts[0].strip():
        pages.append(("", parts[0]))
    for i in range(1, len(parts), 2):
        name = parts[i].strip()
        content = parts[i + 1] if i + 1 < len(parts) else ""
        pages.append((name, content))
    return pages


def extract_voice_profile(text: str) -> VoiceProfile:
    if not text or len(text) < 200:
        return VoiceProfile(method="unavailable")

    body_chunks: list[str] = []
    for page_name, content in _split_pages(text):
        if page_name and _VOICE_SKIP_PAGES.search(page_name):
            continue
        body_chunks.append(content)
    body = "\n".join(body_chunks)

    words = _WORD_RE.findall(body)
    n_words = len(words)
    if n_words < 100:
        return VoiceProfile(method="unavailable", sample_words=n_words)

    je = len(_JE_RE.findall(body))
    u = len(_U_RE.findall(body))
    total_pron = je + u
    if total_pron > 0:
        je_u_ratio = je / total_pron
        if je_u_ratio >= 0.7:
            addressing = "je"
        elif je_u_ratio <= 0.3:
            addressing = "u"
        else:
            addressing = "mixed"
    else:
        je_u_ratio = 0.5
        addressing = "unknown"

    sentences = [s for s in _SENTENCE_SPLIT_RE.split(body) if s.strip()]
    n_sentences = len(sentences)
    avg_sentence_length = n_words / max(n_sentences, 1)

    exclamations = body.count("!")
    excl_per_1k = (exclamations / n_words) * 1000

    if addressing == "u":
        formality = "formeel"
    elif addressing == "je" and avg_sentence_length < 18:
        formality = "informeel"
    elif addressing == "je":
        formality = "informeel"
    elif avg_sentence_length > 22:
        formality = "formeel"
    else:
        formality = "gemengd"

    return VoiceProfile(
        je_count=je,
        u_count=u,
        je_u_ratio=round(je_u_ratio, 3),
        avg_sentence_length=round(avg_sentence_length, 1),
        exclamations_per_1k_words=round(excl_per_1k, 2),
        addressing=addressing,
        formality=formality,
        sample_words=n_words,
        sample_sentences=n_sentences,
        method="deterministic",
    )
