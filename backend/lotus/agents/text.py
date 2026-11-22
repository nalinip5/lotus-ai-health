"""Small text helpers shared by agents and the offline mock LLM."""
from __future__ import annotations

import re

_SENT = re.compile(r"(?<=[.!?])\s+|\n+")
SECTION_HEADER = re.compile(r"^([A-Z][A-Z &/]{2,40}):\s*(.*)$")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text) if s.strip()]


def find_sentence(text: str, keywords: list[str]) -> str | None:
    low_kw = [k.lower() for k in keywords]
    for s in sentences(text):
        ls = s.lower()
        if any(k in ls for k in low_kw):
            return s
    return None


def split_sections(text: str) -> dict[str, str]:
    """Split 'HEADER: body' style clinical text into canonical sections."""
    sections: dict[str, list[str]] = {}
    current = "BODY"
    for line in text.splitlines():
        m = SECTION_HEADER.match(line.strip())
        if m:
            current = m.group(1).strip().title()
            sections.setdefault(current, [])
            if m.group(2):
                sections[current].append(m.group(2))
        elif line.strip():
            sections.setdefault(current, []).append(line.strip())
    return {k: " ".join(v) for k, v in sections.items() if v}


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_in(quote: str, text: str) -> bool:
    return bool(quote) and normalize(quote) in normalize(text)
