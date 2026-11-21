"""Template explanations over solver results. Deterministic; the LLM never
writes these."""
from __future__ import annotations

import chess

from .z3model.solve import Result
from .z3model import truth

CLAUSES = {
    "gives_check": "gives check", "mate_in_1": "is checkmate", "captures": "captures",
    "not_captures": "is a quiet move", "safe": "cannot be taken", "fork": "forks", "pin": "pins",
    "develops": "develops a piece", "promotes": "promotes",
}


def _clause(c: dict, move: chess.Move | None = None) -> str:
    k = c["kind"]
    if k in CLAUSES:
        return CLAUSES[k]
    if k == "piece_safe" and move is not None and move.from_square == chess.parse_square(c["square"]):
        return f"stays safe on {chess.square_name(move.to_square)}"   # the piece itself moved
    if k == "piece_is":
        return f"with the {c['piece']}"
    if k == "piece_not":
        return f"without moving the {c['piece']}"
    if k == "captures_piece":
        return f"wins the {c['piece']}"
    if k == "to_square":
        return f"landing on {c['square']}"
    if k == "from_square":
        return f"starts from {c['square']}"
    if k == "to_color":
        return f"on a {c['color']} square"
    if k == "to_side":
        return f"on the {c['side']}"
    if k == "attacks_square":
        return f"attacks {c['square']}"
    if k == "attacks_piece":
        return f"attacks the {c['piece']}"
    if k == "piece_safe":
        return f"keeps {c['square']} safe"
    if k == "defends":
        return f"defends {c['square']}"
    return k.replace("_", " ")


def answer_text(res: Result, constraints: list[dict], board: chess.Board | None = None) -> str:
    if res.status == "SAT":
        qualifiers = {"piece_is", "piece_not", "to_square", "from_square", "to_color", "to_side"}
        effects = [_clause(c, res.move) for c in constraints if c["kind"] not in qualifiers]
        quals = [_clause(c, res.move) for c in constraints if c["kind"] in qualifiers]
        fallback = "gets out of check" if board is not None and board.is_check() else "is a legal move"
        parts = [" and ".join(effects) if effects else fallback] + quals
        return f"Yes. {res.san} {' '.join(parts)}."
    if res.status == "UNSAT":
        if res.special_moves:
            kinds = sorted({"castling" if s.startswith("O-O") else "en passant" for s in res.special_moves})
            return (f"Not with an ordinary move, but {' or '.join(kinds)} would do it: {', '.join(res.special_moves)}. "
                    f"Castling and en passant sit outside the Z3 model, so python-chess answered that part.")
        if not constraints:
            return "No legal move exists here."
        if len(constraints) == 1:
            return "No single move does that here."
        return "No move does all of this at once."
    return "I could not verify an answer: " + res.message


def highlights(board: chess.Board, res: Result, constraints: list[dict]) -> dict:
    if res.status != "SAT" or res.move is None:
        return {"squares": [], "arrows": []}
    mv = res.move
    frm, to = chess.square_name(mv.from_square), chess.square_name(mv.to_square)
    squares, arrows = [frm, to], [[frm, to]]
    after = board.copy(stack=False)
    after.push(mv)
    them = not board.turn
    kinds = {c["kind"] for c in constraints}
    if kinds & {"gives_check", "mate_in_1"}:
        ek = after.king(them)
        for a in after.attackers(board.turn, ek):
            arrows.append([chess.square_name(a), chess.square_name(ek)])
        squares.append(chess.square_name(ek))
    wanted = {truth.piece_type(c["piece"]) for c in constraints if c["kind"] == "attacks_piece"}
    if "fork" in kinds or wanted:
        for s in after.attacks(mv.to_square):
            p = after.piece_at(s)
            if p and p.color == them and (p.piece_type in wanted or ("fork" in kinds and p.piece_type != chess.PAWN)):
                arrows.append([to, chess.square_name(s)])
                squares.append(chess.square_name(s))
    for c in constraints:
        if c["kind"] == "attacks_square":
            arrows.append([to, c["square"]])
            squares.append(c["square"])
        if c["kind"] in ("defends", "piece_safe"):
            squares.append(to if c["kind"] == "piece_safe" and c["square"] == frm else c["square"])
    if "pin" in kinds:
        for v, b in truth.pins_after(board, mv):
            arrows.append([to, chess.square_name(b)])
            squares += [chess.square_name(v), chess.square_name(b)]
    # dedupe, keep order
    seen = set()
    squares = [s for s in squares if not (s in seen or seen.add(s))]
    seen = set()
    arrows = [a for a in arrows if not (tuple(a) in seen or seen.add(tuple(a)))]
    return {"squares": squares, "arrows": arrows}
