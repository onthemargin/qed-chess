"""The Z3 base model: one chess move as a constraint problem.

Everything below is the actual code that runs for every question. The UI's
"View the model" panel shows these sections verbatim (see source.py), so keep
the `# --- section:` markers and the prose comments honest.

Scope: exactly one move by the side to move, in the given position. Castling,
en passant, repetition and the fifty-move rule are NOT encoded here; they are
handled by python-chess (see the scope-boundary section at the end).

Design note: the position is known, so geometry (which squares a piece on a1
can ever reach) is enumerated in Python and the *conditions* (is the path
empty, is the destination free, is the king attacked afterwards, does the
move give check...) are Z3 constraints over the move variables. That keeps
every check well under 100 ms while leaving all the logic in the solver.
"""
from __future__ import annotations

import chess
import z3

PAWN, KNIGHT, BISHOP, ROOK, QUEEN, KING = 1, 2, 3, 4, 5, 6
VALUE = {PAWN: 1, KNIGHT: 3, BISHOP: 3, ROOK: 5, QUEEN: 9, KING: 100}


# --- section: Board encoding ---
# Squares are 0..63 like python-chess (a1 = 0, h8 = 63). The position is a
# table of signed piece codes:
#   0 = empty, +1..+6 = white pawn..king, -1..-6 = black pawn..king
# `occ_before(x)` is that table as a Z3 constant, so every rule below is
# written against the board, not against a hand-computed move list.
def encode_board(board):
    table = {}
    for sq, p in board.piece_map().items():
        table[sq] = p.piece_type if p.color == chess.WHITE else -p.piece_type

    def occ_before(x):
        return z3.IntVal(table.get(x, 0))
    return table, occ_before
# --- end section ---


# --- section: Move variables ---
# A move is four integers. Z3 searches over these; everything else is a rule
# that constrains them.
#   frm, to : squares 0..63, frm != to
#   piece   : the type of the piece being moved (1..6, always ours)
#   promo   : 0 for no promotion, else the new piece type 2..5
def declare_move(solver, table, sign):
    frm, to, piece, promo = z3.Ints("frm to piece promo")
    solver.add(frm >= 0, frm <= 63, to >= 0, to <= 63, frm != to)
    own = [(sq, c * sign) for sq, c in table.items() if c * sign > 0]
    solver.add(z3.Or([z3.And(frm == sq, piece == t) for sq, t in own]))   # we move one of our pieces
    solver.add(z3.And([to != sq for sq, _ in own]))                         # never onto our own piece
    solver.add(z3.Or(promo == 0, z3.And(promo >= 2, promo <= 5)))
    return frm, to, piece, promo
# --- end section ---


# --- section: Piece movement ---
# `reach(ptype, a, sign)` lists every square a piece of that type could reach
# from `a` on an empty board, together with the squares in between that must
# be empty. Pawn *captures* are listed by `reach`; pawn pushes are separate
# because they need empty squares rather than an enemy piece.
KNIGHT_D = [(1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1)]
KING_D = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
ROOK_D = [(1, 0), (-1, 0), (0, 1), (0, -1)]
BISHOP_D = [(1, 1), (1, -1), (-1, 1), (-1, -1)]


def _walk(a, step):
    f, r = chess.square_file(a) + step[0], chess.square_rank(a) + step[1]
    while 0 <= f < 8 and 0 <= r < 8:
        yield chess.square(f, r)
        f, r = f + step[0], r + step[1]


def reach(ptype, a, sign):
    """[(target, [squares strictly between])] for attacks/moves from `a`."""
    out = []
    if ptype == KNIGHT or ptype == KING:
        for step in (KNIGHT_D if ptype == KNIGHT else KING_D):
            for t in _walk(a, step):
                out.append((t, []))
                break
    elif ptype == PAWN:
        for step in ((1, sign), (-1, sign)):
            for t in _walk(a, step):
                out.append((t, []))
                break
    else:
        dirs = {BISHOP: BISHOP_D, ROOK: ROOK_D, QUEEN: BISHOP_D + ROOK_D}[ptype]
        for step in dirs:
            between = []
            for t in _walk(a, step):
                out.append((t, list(between)))
                between.append(t)
    return out


def path_clear(between, occ):
    return z3.And([occ(m) == 0 for m in between]) if between else z3.BoolVal(True)


def movement_rules(solver, table, occ_before, frm, to, piece, promo, sign):
    start_rank, last_rank = (1, 7) if sign == 1 else (6, 0)
    options = []
    for a, code in table.items():
        if code * sign <= 0:
            continue
        t_type = code * sign
        here = z3.And(frm == a, piece == t_type)
        if t_type == PAWN:
            one = a + 8 * sign
            moves = [z3.And(to == one, occ_before(one) == 0)]
            if chess.square_rank(a) == start_rank:
                two = a + 16 * sign
                moves.append(z3.And(to == two, occ_before(one) == 0, occ_before(two) == 0))
            for t, _ in reach(PAWN, a, sign):
                moves.append(z3.And(to == t, occ_before(t) * sign < 0))       # diagonal needs an enemy
            options.append(z3.And(here, z3.Or(moves)))
        else:
            moves = [z3.And(to == t, path_clear(between, occ_before)) for t, between in reach(t_type, a, sign)]
            options.append(z3.And(here, z3.Or(moves)))
    solver.add(z3.Or(options))
    # A pawn reaching the last rank must promote; nothing else may.
    last = [chess.square(f, last_rank) for f in range(8)]
    on_last = z3.Or([to == s for s in last])
    solver.add(z3.If(z3.And(piece == PAWN, on_last), promo != 0, promo == 0))
# --- end section ---


# --- section: Attack relation ---
# The board after the move is the table with the origin emptied and the
# destination holding the moved (possibly promoted) piece. "A piece on `a`
# attacks `t`" after the move means: the geometry allows it and every square
# between them is empty on that after-board. Check, forks, pins and safety
# are all phrased with this one relation.
def board_after(occ_before, frm, to, piece_after, sign):
    def occ_after(x):
        return z3.If(x == frm, 0, z3.If(x == to, piece_after * sign, occ_before(x)))
    return occ_after


def attacks(ptype, a, target, sign, occ):
    """Constant attacker square `a` and constant `target`."""
    for t, between in reach(ptype, a, sign):
        if t == target:
            return path_clear(between, occ)
    return z3.BoolVal(False)


def attacker_squares(target, sign):
    """[(square, ptype, between)]: everywhere a piece of `ptype` (moving in
    direction `sign`) could stand to attack `target`."""
    out = []
    for ptype in (PAWN, KNIGHT, BISHOP, ROOK, QUEEN, KING):
        # a piece on `a` attacks `target` iff a piece of the same type on
        # `target` would reach `a` (pawns: mirrored direction)
        for a, between in reach(ptype, target, -sign if ptype == PAWN else sign):
            out.append((a, ptype, between))
    return out
# --- end section ---


# --- section: Legality: own king safe ---
# After the move, no surviving enemy piece may attack our king. If the king
# itself moved, the square to protect is its destination. An enemy piece that
# was just captured (it stood on `to`) no longer attacks anything.
def king_safety(solver, table, frm, to, piece, sign, occ_after):
    k0 = next(sq for sq, c in table.items() if c == KING * sign)
    enemies = [(sq, -c * sign) for sq, c in table.items() if c * sign < 0]

    def attacked(square):
        return z3.Or([z3.And(to != e, attacks(t, e, square, -sign, occ_after)) for e, t in enemies])

    solver.add(z3.Implies(piece != KING, z3.Not(attacked(k0))))
    for t, _ in reach(KING, k0, sign):
        solver.add(z3.Implies(z3.And(piece == KING, to == t), z3.Not(attacked(t))))
# --- end section ---


# --- section: Scope boundary ---
# Not encoded in Z3, delegated to python-chess:
#   * castling (needs rights, path, and "not through check" history)
#   * en passant (needs the previous move)
#   * threefold repetition and the fifty-move rule (need the game history)
#   * checkmate: Z3 proves "this move gives check"; python-chess then confirms
#     the opponent has no legal reply. A mate answer always says so.
# Consequence: a Z3 UNSAT means "no ordinary single move does this". If the only
# such move is a castle or an en passant capture, the answer states that too.
# Every SAT model is additionally re-validated with python-chess before it is
# shown; a disagreement is reported as an error, never as a move.
# --- end section ---


class MoveModel:
    """A solver pre-loaded with the base model for one position."""

    def __init__(self, board: chess.Board):
        self.board = board
        self.us = board.turn
        self.sign = 1 if self.us == chess.WHITE else -1
        self.solver = z3.Solver()
        self.table, self.occ_before = encode_board(board)
        self.frm, self.to, self.piece, self.promo = declare_move(self.solver, self.table, self.sign)
        movement_rules(self.solver, self.table, self.occ_before, self.frm, self.to, self.piece, self.promo, self.sign)
        self.piece_after = z3.If(self.promo != 0, self.promo, self.piece)
        self.occ_after = board_after(self.occ_before, self.frm, self.to, self.piece_after, self.sign)
        king_safety(self.solver, self.table, self.frm, self.to, self.piece, self.sign, self.occ_after)

    # -- helpers used by the query constraints ---------------------------------
    def own_pieces(self):
        return [(sq, c * self.sign) for sq, c in self.table.items() if c * self.sign > 0]

    def enemy_pieces(self, types=None):
        return [(sq, -c * self.sign) for sq, c in self.table.items()
                if c * self.sign < 0 and (types is None or -c * self.sign in types)]

    def moved_piece_attacks(self, target: int):
        """The moved piece, standing on `to`, attacks constant `target` after the move."""
        return z3.Or([z3.And(self.to == a, self.piece_after == pt, path_clear(between, self.occ_after))
                      for a, pt, between in attacker_squares(target, self.sign)])

    def we_attack(self, target: int):
        """Any of our pieces attacks constant `target` after the move."""
        others = [z3.And(self.frm != s, attacks(t, s, target, self.sign, self.occ_after)) for s, t in self.own_pieces()]
        return z3.Or([self.moved_piece_attacks(target)] + others)

    def enemy_attacks(self, target):
        """Any surviving enemy piece attacks `target` (constant or Z3 term) after the move."""
        terms = []
        for e, t in self.enemy_pieces():
            for sq, between in reach(t, e, -self.sign):
                if isinstance(target, int) and sq != target:
                    continue
                hit = z3.BoolVal(True) if isinstance(target, int) else target == sq
                terms.append(z3.And(hit, self.to != e, path_clear(between, self.occ_after)))
        return z3.Or(terms) if terms else z3.BoolVal(False)

    def exclude_move(self, move: chess.Move):
        promo = move.promotion or 0
        self.solver.add(z3.Not(z3.And(self.frm == move.from_square, self.to == move.to_square,
                                      self.promo == promo)))

    def model_move(self, m: z3.ModelRef) -> chess.Move:
        frm = m.eval(self.frm, model_completion=True).as_long()
        to = m.eval(self.to, model_completion=True).as_long()
        promo = m.eval(self.promo, model_completion=True).as_long()
        return chess.Move(frm, to, promotion=promo or None)
