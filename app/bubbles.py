"""Suggested-question bubbles, chosen from the game state. No LLM involved:
every bubble maps to a structured query (or a UI action)."""
from __future__ import annotations

import chess

from .z3model.truth import PIECE_VALUE

NAMES = {chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop", chess.ROOK: "rook",
         chess.QUEEN: "queen", chess.KING: "king"}


def q(label, *constraints, **extra):
    return {"label": label, "query": {"constraints": list(constraints), **extra}}


def _followups(last: dict) -> list[dict]:
    out = []
    cons = last.get("constraints") or []
    if last.get("status") == "SAT":
        exclude = list(last.get("exclude") or []) + ([last["move"]] if last.get("move") else [])
        out.append(q("Show another solution", *cons, exclude=exclude))
        out.append({"label": "Show the Z3 code", "action": "code"})
    elif last.get("status") == "UNSAT":
        if len(cons) > 1:
            out.append({"label": "Which constraint breaks it?", "action": "why"})
            weakest = cons[-1]
            rest = [c for c in cons if c is not weakest]
            out.append(q(f"Drop “{_short(weakest)}”", *rest))
        else:
            out.append({"label": "Why is that impossible?", "action": "why"})
    return out


def _short(c: dict) -> str:
    k = c["kind"]
    if k == "piece_not":
        return f"not the {c['piece']}"
    if k == "piece_is":
        return f"with a {c['piece']}"
    if k in ("to_square", "from_square", "attacks_square", "defends", "piece_safe"):
        return f"{k.replace('_', ' ')} {c['square']}"
    if k in ("captures_piece", "attacks_piece"):
        return f"{k.split('_')[0]} {c['piece']}"
    return k.replace("_", " ")


def _state_bubbles(board: chess.Board) -> list[dict]:
    us, them = board.turn, not board.turn
    out = []
    if board.is_check():
        out += [q("Show me a way out of check"),
                q("Can I block instead of moving the king?", {"kind": "piece_not", "piece": "king"}, {"kind": "not_captures"}),
                q("Can I escape without moving my king?", {"kind": "piece_not", "piece": "king"}),
                q("Can I capture the checking piece?", {"kind": "captures"})]
        return out

    # most valuable enemy piece we can capture, and whether it is undefended
    best = None
    for mv in board.legal_moves:
        if board.is_capture(mv) and not board.is_en_passant(mv):
            t = board.piece_type_at(mv.to_square)
            if best is None or PIECE_VALUE[t] > PIECE_VALUE[best[0]]:
                best = (t, mv.to_square)
    if best and best[0] != chess.PAWN:
        out.append(q(f"Can I capture the {NAMES[best[0]]}?", {"kind": "captures_piece", "piece": NAMES[best[0]]}))
        out.append(q("Can I capture something safely?", {"kind": "captures"}, {"kind": "safe"}))

    # own non-king piece under attack
    attacked = [(PIECE_VALUE[p.piece_type], sq, p.piece_type) for sq, p in board.piece_map().items()
                if p.color == us and p.piece_type != chess.KING and board.is_attacked_by(them, sq)]
    if attacked:
        _, sq, t = max(attacked)
        name = chess.square_name(sq)
        out.append(q(f"Can I save my {NAMES[t]} on {name}?", {"kind": "piece_safe", "square": name}))
        out.append(q(f"Can I defend my {NAMES[t]} on {name}?", {"kind": "defends", "square": name}))

    non_pawn = sum(1 for p in board.piece_map().values() if p.piece_type not in (chess.PAWN, chess.KING))
    pawns_near = any(p.piece_type == chess.PAWN and p.color == us and
                     chess.square_rank(sq) == (6 if us == chess.WHITE else 1) for sq, p in board.piece_map().items())
    if pawns_near:
        out.append(q("Can I promote a pawn?", {"kind": "promotes"}))
    if board.fullmove_number <= 6 and non_pawn >= 10:
        out += [q("Can I develop a piece safely?", {"kind": "develops"}, {"kind": "safe"}),
                q("Can I give check?", {"kind": "gives_check"}),
                q("Can I attack the queen?", {"kind": "attacks_piece", "piece": "queen"})]
    elif non_pawn <= 4:
        out += [q("Is there a mate in 1?", {"kind": "mate_in_1"}),
                q("Can I give check safely?", {"kind": "gives_check"}, {"kind": "safe"}),
                q("Can I fork two pieces?", {"kind": "fork"})]
    else:
        has_queen = any(p == chess.Piece(chess.QUEEN, us) for p in board.piece_map().values())
        if has_queen:
            out.append(q("Can I give check without moving my queen?", {"kind": "gives_check"}, {"kind": "piece_not", "piece": "queen"}))
        else:
            out.append(q("Can I give check?", {"kind": "gives_check"}))
        out += [q("Can I fork two pieces?", {"kind": "fork"}), q("Can I pin a piece?", {"kind": "pin"})]
    return out


def suggest(board: chess.Board, last: dict | None = None) -> list[dict]:
    if board.is_game_over():
        return [{"label": "Start a new game", "action": "new_game"}]
    out = _followups(last) if last else []
    seen = {b["label"] for b in out}
    for b in _state_bubbles(board):
        if b["label"] not in seen and len(out) < 4:
            out.append(b)
            seen.add(b["label"])
    return out[:4]
