"""Solve one question: build the base model, add the question's constraints,
ask Z3, cross-check the answer with python-chess, explain UNSAT."""
from __future__ import annotations

from dataclasses import dataclass, field

import chess
import z3

from . import queries, truth
from .base import MoveModel

SCOPE_BASE = "single move, this position; castling and en passant are outside the Z3 model"
MAX_REFINEMENTS = 256   # candidate moves that fail the python-chess cross-check before we give up


@dataclass
class Result:
    status: str                      # SAT | UNSAT | ERROR
    move: chess.Move | None = None
    san: str | None = None
    constraints_text: list[str] = field(default_factory=list)
    z3_code: str = ""
    scope: str = SCOPE_BASE
    unsat_core: list[str] = field(default_factory=list)
    drop_one: list[dict] = field(default_factory=list)
    message: str = ""
    model_values: dict = field(default_factory=dict)
    special_moves: list[str] = field(default_factory=list)   # castling / en passant moves (SAN) that would satisfy the question


def _special_only(board: chess.Board, constraints: list[dict]) -> tuple[list[str], list[str]]:
    """Castling / en passant moves that would satisfy the constraints: (kinds, SAN moves)."""
    kinds, sans = [], []
    for mv in board.legal_moves:
        if (board.is_castling(mv) or board.is_en_passant(mv)) and all(truth.holds(board, mv, c) for c in constraints):
            kinds.append("en passant" if board.is_en_passant(mv) else "castling")
            sans.append(board.san(mv))
    return sorted(set(kinds)), sans


def _code(lines: list[str], exclude: list[str], result_line: str) -> str:
    body = "\n".join(lines) if lines else "# (no extra constraints: any legal move)"
    excl = "".join(f"\ns.add(Not(move == {u}))  # already shown" for u in exclude)
    return f"# assertions for this question; the base model is already loaded (open it below)\n{body}{excl}\n{result_line}"


def solve(board: chess.Board, constraints: list[dict], exclude: list[str] | None = None) -> Result:
    exclude = list(exclude or [])
    m = MoveModel(board)
    trackers, texts, lines = [], [], []
    for i, c in enumerate(constraints):
        expr, text, line = queries.build(m, c)
        t = z3.Bool(f"c{i}_{c['kind']}")
        m.solver.add(z3.Implies(t, expr))
        trackers.append(t)
        texts.append(text)
        lines.append(line)
    for u in exclude:
        m.exclude_move(chess.Move.from_uci(u))

    wants_mate = any(c["kind"] == "mate_in_1" for c in constraints)
    scope = SCOPE_BASE
    if wants_mate:
        scope += "; mate confirmed by python-chess, not proved inside Z3"

    for _ in range(MAX_REFINEMENTS):
        if m.solver.check(*trackers) != z3.sat:
            break
        mv = m.model_move(m.solver.model())
        if mv not in board.legal_moves:
            return Result("ERROR", constraints_text=texts, scope=scope,
                          message=f"Z3 proposed {mv.uci()} but python-chess says it is illegal; refusing to show it.")
        bad = [c for c in constraints if not truth.holds(board, mv, c)]
        if bad and all(c["kind"] == "mate_in_1" for c in bad):
            m.exclude_move(mv)          # a check that is not mate: refine and continue
            continue
        if bad:
            return Result("ERROR", constraints_text=texts, scope=scope,
                          message=f"Z3 proposed {mv.uci()} but python-chess disagrees on {bad[0]['kind']}; refusing to show it.")
        vals = {"frm": mv.from_square, "to": mv.to_square, "promo": mv.promotion or 0}
        result_line = f"# check() -> sat; model: frm={vals['frm']} ({chess.square_name(mv.from_square)}), " \
                      f"to={vals['to']} ({chess.square_name(mv.to_square)}), promo={vals['promo']}"
        return Result("SAT", move=mv, san=board.san(mv), constraints_text=texts,
                      z3_code=_code(lines, exclude, result_line), scope=scope, model_values=vals)

    else:
        return Result("ERROR", constraints_text=texts, scope=scope,
                      message=f"gave up after {MAX_REFINEMENTS} candidate moves failed the python-chess cross-check; "
                              "this question needs more refinement than one session allows.")

    # UNSAT
    specials, special_moves = _special_only(board, constraints)
    if specials:
        scope += f"; a {' / '.join(specials)} move would satisfy this, which Z3 does not model"
    core = []
    if len(trackers) > 1:
        core = [str(t).split("_", 1)[1] for t in m.solver.unsat_core()] if m.solver.check(*trackers) == z3.unsat else []
    drop_one = []
    if len(trackers) > 1:
        for i, t in enumerate(trackers):
            others = [x for j, x in enumerate(trackers) if j != i]
            drop_one.append({"kind": constraints[i]["kind"], "text": texts[i],
                             "sat_without": m.solver.check(*others) == z3.sat})
    return Result("UNSAT", constraints_text=texts, z3_code=_code(lines, exclude, "# check() -> unsat"),
                  scope=scope, unsat_core=core, drop_one=drop_one, special_moves=special_moves)


def enumerate_solutions(board: chess.Board, constraints: list[dict], limit: int = 100) -> list[chess.Move]:
    """All moves Z3 finds, by repeated exclusion on one incremental solver."""
    m = MoveModel(board)
    for c in constraints:
        expr, _, _ = queries.build(m, c)
        m.solver.add(expr)
    found = []
    while len(found) < limit and m.solver.check() == z3.sat:
        mv = m.model_move(m.solver.model())
        m.exclude_move(mv)
        if mv in board.legal_moves and all(truth.holds(board, mv, c) for c in constraints):
            found.append(mv)
        elif all(c["kind"] != "mate_in_1" for c in constraints):
            found.append(mv)   # let the test see the disagreement
    return found
