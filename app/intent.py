"""Deterministic question parser: text -> structured query, or None.

Runs before any LLM call. Bubbles never come here; only typed text does."""
from __future__ import annotations

import re

import chess

PIECES = {"pawn": "pawn", "knight": "knight", "horse": "knight", "bishop": "bishop", "rook": "rook",
          "castle": "rook", "queen": "queen", "king": "king"}
PIECE_RE = r"(pawn|knight|horse|bishop|rook|queen|king)s?"
SQ_RE = r"\b([a-h][1-8])\b"
TYPES = {"pawn": chess.PAWN, "knight": chess.KNIGHT, "bishop": chess.BISHOP, "rook": chess.ROOK,
         "queen": chess.QUEEN, "king": chess.KING}


def _own_square(board: chess.Board, piece: str):
    """Resolve "my bishop" to a square: unique piece, else the attacked one,
    else the developed one, else the first found."""
    sqs = [sq for sq, p in board.piece_map().items() if p.color == board.turn and p.piece_type == TYPES[piece]]
    if not sqs:
        return None
    if len(sqs) > 1:
        attacked = [sq for sq in sqs if board.is_attacked_by(not board.turn, sq)]
        back = 0 if board.turn == chess.WHITE else 7
        developed = [sq for sq in sqs if chess.square_rank(sq) != back]
        sqs = attacked or developed or sqs
    return chess.square_name(sorted(sqs)[0])


def parse(text: str, board: chess.Board):
    t = (text or "").lower().strip()
    if not t:
        return None
    if re.search(r"\b(another|other|different|next)\b.*\b(solution|move|one|option|way)\b", t) or t in ("another", "next"):
        return {"action": "another"}
    if re.search(r"\bwhy\b|\bimpossible\b|\bbreaks\b", t):
        return {"action": "why"}
    if re.search(r"\b(z3|smt|solver)\b.*\bcode\b|\bcode\b", t):
        return {"action": "code"}

    cons = []

    def add(c):
        if c not in cons:
            cons.append(c)

    if re.search(r"\b(mate|checkmate)\b", t):
        add({"kind": "mate_in_1"})
    elif re.search(r"\bcheck\b", t):
        add({"kind": "gives_check"})
    if re.search(r"\bfork", t):
        add({"kind": "fork"})
    if re.search(r"\bpin", t):
        add({"kind": "pin"})
    if re.search(r"\b(develop)", t):
        add({"kind": "develops"})
    if re.search(r"\b(promot|queen a pawn)", t):
        add({"kind": "promotes"})
    if re.search(r"\b(block)\b", t) and board.is_check():
        add({"kind": "piece_not", "piece": "king"})
        add({"kind": "not_captures"})
    if re.search(r"\b(escape|get out of check|out of check)\b", t) and board.is_check():
        pass  # any legal move escapes

    m = re.search(r"\b(capture|take|win|grab)\b(?:\s+(?:the|a|an|my|his|her|their))?\s+" + PIECE_RE, t)
    if m:
        add({"kind": "captures_piece", "piece": PIECES[m.group(2)]})
    elif re.search(r"\b(capture|take|win material|grab)\b", t):
        add({"kind": "captures"})

    m = re.search(r"\battack(?:s|ing)?\s+(?:the|a|an|his|her|their)?\s*" + PIECE_RE, t)
    if m:
        add({"kind": "attacks_piece", "piece": PIECES[m.group(1)]})
    m = re.search(r"\battack(?:s|ing)?\s+" + SQ_RE, t)
    if m:
        add({"kind": "attacks_square", "square": m.group(1)})

    for m in re.finditer(r"\b(defend|protect|cover|guard)\b(?:\s+(?:the|my))?\s+(?:" + PIECE_RE + r"|" + SQ_RE + r")", t):
        if m.group(1) and m.group(2):
            sq = _own_square(board, PIECES[m.group(2)])
            if sq:
                add({"kind": "defends", "square": sq})
        elif m.group(3):
            add({"kind": "defends", "square": m.group(3)})

    m = re.search(r"\b(?:is|keep)\s+(?:my|the)\s+" + PIECE_RE + r"\s+(?:on\s+" + SQ_RE + r"\s+)?safe\b", t) \
        or re.search(r"\bsave\s+(?:my|the)\s+" + PIECE_RE + r"(?:\s+on\s+" + SQ_RE + r")?", t)
    if m:
        sq = m.group(2) or _own_square(board, PIECES[m.group(1)])
        if sq:
            add({"kind": "piece_safe", "square": sq})
    elif re.search(r"\bsafe(ly)?\b|\bwithout losing\b|\bwithout hanging\b|\bnot lose\b", t):
        add({"kind": "safe"})

    m = re.search(r"\bwithout\s+(?:moving\s+)?(?:my\s+|the\s+)?" + PIECE_RE, t)
    if m:
        add({"kind": "piece_not", "piece": PIECES[m.group(1)]})
    for m in re.finditer(r"\b(?:with|using)\s+(?:a|an|my|the)\s+" + PIECE_RE, t):
        add({"kind": "piece_is", "piece": PIECES[m.group(1)]})
    m = re.search(r"\b(?:can|could)\s+(?:a|an|my|the)\s+" + PIECE_RE + r"\s+(?:go|move|get|jump)\b", t) \
        or re.search(r"\b" + PIECE_RE + r"\s+move\b", t)
    if m:
        add({"kind": "piece_is", "piece": PIECES[m.group(1)]})

    m = re.search(r"\b(?:to|on|onto|reach|land on)\s+" + SQ_RE, t)
    if m and not any(c.get("square") == m.group(1) for c in cons):
        add({"kind": "to_square", "square": m.group(1)})
    m = re.search(r"\bfrom\s+" + SQ_RE, t)
    if m:
        add({"kind": "from_square", "square": m.group(1)})
    m = re.search(r"\b(dark|light|black|white)[- ]squares?\b", t)
    if m:
        add({"kind": "to_color", "color": "dark" if m.group(1) in ("dark", "black") else "light"})
    m = re.search(r"\b(kingside|queenside|king side|queen side)\b", t)
    if m:
        add({"kind": "to_side", "side": "kingside" if m.group(1).startswith("king") else "queenside"})

    if cons or (board.is_check() and re.search(r"\b(escape|out of check|ways? out)\b", t)):
        return {"constraints": cons}
    return None
