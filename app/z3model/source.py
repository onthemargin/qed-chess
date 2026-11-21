"""Expose the base model's own source for the 'View the model' panel."""
from __future__ import annotations

import re
from pathlib import Path

INTROS = {
    "Board encoding": "The position becomes a function from squares to signed piece codes. Sixty-four facts, one per square.",
    "Move variables": "A move is four integers Z3 is free to choose. Every rule below narrows them.",
    "Piece movement": "How each piece type may travel, as arithmetic on file and rank differences. Sliders also need an empty path.",
    "Attack relation": "The board after the move, and 'X attacks Y' evaluated on it. Check, forks, pins and safety all reuse this.",
    "Legality: own king safe": "After the move no surviving enemy piece may attack our king.",
    "Scope boundary": "What Z3 does not model and python-chess handles instead, and how every answer is cross-checked.",
}

_PATTERN = re.compile(r"# --- section: (.+?) ---\n(.*?)# --- end section ---", re.S)


def sections() -> list[dict]:
    text = (Path(__file__).parent / "base.py").read_text()
    out = []
    for title, code in _PATTERN.findall(text):
        out.append({"title": title, "intro": INTROS.get(title, ""), "code": code.rstrip()})
    return out
