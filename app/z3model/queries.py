"""Per-question constraints layered on the base model.

Each builder returns (z3_expr, plain_english, code_line). The code line is
what the UI shows under "Show Z3 code (+ base model)"; it is the Python-level
form of the assertion made here, not a paraphrase.
"""
from __future__ import annotations

import chess
import z3

from .base import BISHOP, KING, KNIGHT, PAWN, QUEEN, ROOK, VALUE, MoveModel
from .truth import piece_type, square

PIECE_CODE = {"pawn": PAWN, "knight": KNIGHT, "bishop": BISHOP, "rook": ROOK, "queen": QUEEN, "king": KING}
CODE_NAME = {v: k.upper() for k, v in PIECE_CODE.items()}


def _ray(a: int, b: int):
    df = chess.square_file(b) - chess.square_file(a)
    dr = chess.square_rank(b) - chess.square_rank(a)
    if (df, dr) == (0, 0) or not (df == 0 or dr == 0 or abs(df) == abs(dr)):
        return None
    return ((df > 0) - (df < 0), (dr > 0) - (dr < 0))


def _walk(sq: int, step):
    f, r = chess.square_file(sq) + step[0], chess.square_rank(sq) + step[1]
    while 0 <= f < 8 and 0 <= r < 8:
        yield chess.square(f, r)
        f, r = f + step[0], r + step[1]


def _between(a: int, b: int, step):
    out = []
    for s in _walk(a, step):
        if s == b:
            return out
        out.append(s)
    return out


def _ep_trap(m: MoveModel):
    """Double pawn pushes that land beside an enemy pawn, i.e. moves the enemy could answer en passant.
    Returns (Z3 term over frm/to, [(from, to), ...])."""
    board, sign = m.board, m.sign
    start_rank = 1 if m.us == chess.WHITE else 6
    pairs = []
    for sq, p in board.piece_map().items():
        if p != chess.Piece(chess.PAWN, m.us) or chess.square_rank(sq) != start_rank:
            continue
        to = sq + 16 * sign
        f, r = chess.square_file(to), chess.square_rank(to)
        if any(0 <= f + d < 8 and board.piece_at(chess.square(f + d, r)) == chess.Piece(chess.PAWN, not m.us) for d in (-1, 1)):
            pairs.append((sq, to))
    term = z3.Or([z3.And(m.frm == a, m.to == b) for a, b in pairs]) if pairs else z3.BoolVal(False)
    return term, pairs


def _ep_code(pairs):
    if not pairs:
        return ""
    return "\ns.add(Not(Or([" + ", ".join(f"And(frm == {a}, to == {b})" for a, b in pairs) + "])))  # a double push an enemy pawn could take en passant"


def build(m: MoveModel, c: dict):
    kind = c["kind"]
    sign, occ, frm, to, piece, promo = m.sign, m.occ_before, m.frm, m.to, m.piece, m.promo
    board = m.board

    if kind == "piece_is":
        t = piece_type(c["piece"])
        return piece == t, f"the moved piece is a {c['piece']}", f"s.add(piece == {CODE_NAME[t]})"
    if kind == "piece_not":
        t = piece_type(c["piece"])
        return piece != t, f"the moved piece is not the {c['piece']}", f"s.add(piece != {CODE_NAME[t]})"
    if kind == "from_square":
        sq = square(c["square"])
        return frm == sq, f"the move starts on {c['square']}", f"s.add(frm == {sq})  # {c['square']}"
    if kind == "to_square":
        sq = square(c["square"])
        return to == sq, f"the move ends on {c['square']}", f"s.add(to == {sq})  # {c['square']}"
    if kind == "to_color":
        dark = c["color"] == "dark"
        squares = [sq for sq in range(64) if ((chess.square_file(sq) + chess.square_rank(sq)) % 2 == 0) == dark]
        expr = z3.Or([to == sq for sq in squares])
        return expr, f"the destination is a {'dark' if dark else 'light'} square", \
            f"s.add(Or([to == sq for sq in {'DARK' if dark else 'LIGHT'}_SQUARES]))"
    if kind == "to_side":
        ks = c["side"] == "kingside"
        squares = [sq for sq in range(64) if (chess.square_file(sq) >= 4) == ks]
        expr = z3.Or([to == sq for sq in squares])
        return expr, f"the destination is on the {'kingside (files e-h)' if ks else 'queenside (files a-d)'}", \
            f"s.add(Or([to == sq for sq in {'KINGSIDE' if ks else 'QUEENSIDE'}_SQUARES]))"
    if kind == "gives_check":
        ek = board.king(not m.us)
        return m.we_attack(ek), "the move gives check", f"s.add(we_attack({ek}))  # enemy king on {chess.square_name(ek)}, after the move"
    if kind == "mate_in_1":
        ek = board.king(not m.us)
        return m.we_attack(ek), "the move is checkmate (Z3: gives check; python-chess confirms there is no reply)", \
            f"s.add(we_attack({ek}))  # check; mate confirmed by python-chess, see scope"
    if kind == "captures":
        enemies = [sq for sq, _ in m.enemy_pieces()]
        return z3.Or([to == sq for sq in enemies]), "the move captures something", \
            "s.add(Or([to == e for e in enemy_squares]))  # an enemy piece stands on `to`"
    if kind == "not_captures":
        enemies = [sq for sq, _ in m.enemy_pieces()]
        return z3.And([to != sq for sq in enemies]), "the move is not a capture", "s.add(And([to != e for e in enemy_squares]))"
    if kind == "captures_piece":
        t = piece_type(c["piece"])
        targets = [sq for sq, _ in m.enemy_pieces(types={t})]
        return z3.Or([to == sq for sq in targets]) if targets else z3.BoolVal(False), f"the move captures a {c['piece']}", \
            f"s.add(Or([to == e for e in enemy_{c['piece']}_squares]))"
    if kind == "safe":
        ep, pairs = _ep_trap(m)
        return z3.And(z3.Not(m.enemy_attacks(to)), z3.Not(ep)), \
            "after the move no enemy piece attacks the moved piece (en passant included)", \
            "s.add(Not(enemy_attacks(to)))  # evaluated on the board after the move" + _ep_code(pairs)
    if kind == "attacks_square":
        sq = square(c["square"])
        return m.moved_piece_attacks(sq), f"after the move the moved piece attacks {c['square']}", \
            f"s.add(moved_piece_attacks({sq}))  # {c['square']}"
    if kind == "attacks_piece":
        t = piece_type(c["piece"])
        targets = m.enemy_pieces(types={t})
        expr = z3.Or([m.moved_piece_attacks(sq) for sq, _ in targets]) if targets else z3.BoolVal(False)
        return expr, f"after the move the moved piece attacks an enemy {c['piece']}", \
            f"s.add(Or([moved_piece_attacks(sq) for sq in {[chess.square_name(s) for s, _ in targets]}]))"
    if kind == "fork":
        targets = [sq for sq, t in m.enemy_pieces() if t != PAWN]
        count = z3.Sum([z3.If(m.moved_piece_attacks(sq), 1, 0) for sq in targets]) if targets else z3.IntVal(0)
        return count >= 2, "after the move the moved piece attacks two or more enemy pieces (not pawns)", \
            "s.add(Sum([If(moved_piece_attacks(e), 1, 0) for e in enemy_non_pawns]) >= 2)"
    if kind == "pin":
        return _pin(m), "after the move the moved piece pins an enemy piece to a more valuable one behind it", \
            "s.add(Or([pin_along_ray(victim, behind) for victim, behind in collinear_enemy_pairs]))"
    if kind == "piece_safe":
        x = square(c["square"])
        if board.color_at(x) != m.us:
            return z3.BoolVal(False), f"there is no piece of yours on {c['square']}", "s.add(False)  # no own piece there"
        target = z3.If(frm == x, to, x)
        ep, pairs = _ep_trap(m)
        return z3.And(z3.Not(m.enemy_attacks(target)), z3.Not(z3.And(frm == x, ep))), \
            f"after the move your piece from {c['square']} is not attacked (en passant included)", \
            f"s.add(Not(enemy_attacks(If(frm == {x}, to, {x}))))  # {c['square']}, wherever it ends up" + _ep_code(pairs)
    if kind == "defends":
        x = square(c["square"])
        if board.color_at(x) != m.us:
            return z3.BoolVal(False), f"there is no piece of yours on {c['square']}", "s.add(False)  # no own piece there"
        return z3.And(frm != x, m.we_attack(x)), f"after the move your piece on {c['square']} is defended", \
            f"s.add(And(frm != {x}, we_attack({x})))  # {c['square']}"
    if kind == "develops":
        back = 0 if m.us == chess.WHITE else 7
        back_sqs = [chess.square(f, back) for f in range(8)]
        expr = z3.And(z3.Or(piece == KNIGHT, piece == BISHOP), z3.Or([frm == s for s in back_sqs]),
                      z3.And([to != s for s in back_sqs]))
        return expr, "the move develops a minor piece off the back rank", \
            f"s.add(And(Or(piece == KNIGHT, piece == BISHOP), frm on rank {back + 1}, to not on rank {back + 1}))"
    if kind == "promotes":
        return promo != 0, "the move promotes a pawn", "s.add(promo != 0)"
    raise ValueError(f"unknown constraint kind {kind!r}")


def _pin(m: MoveModel):
    """Moved slider at `to` attacks enemy `victim`; the first piece behind the
    victim on the same ray is a more valuable enemy piece."""
    enemies = m.enemy_pieces()
    terms = []
    for v_sq, v_t in enemies:
        for b_sq, b_t in enemies:
            if b_sq == v_sq or VALUE[b_t] <= VALUE[v_t]:
                continue
            step = _ray(b_sq, v_sq)          # direction from the piece behind, through the victim
            if step is None:
                continue
            gap = _between(b_sq, v_sq, step)
            gap_clear = z3.And([z3.Or(m.occ_before(s) == 0, s == m.frm) for s in gap]) if gap else z3.BoolVal(True)
            allowed = [QUEEN, ROOK] if 0 in step else [QUEEN, BISHOP]
            kind_ok = z3.Or([m.piece_after == t for t in allowed])
            ahead = []
            for t_sq in _walk(v_sq, step):      # candidate squares for the pinning piece
                line_clear = z3.And([z3.Or(m.occ_before(s) == 0, s == m.frm) for s in ahead]) if ahead else z3.BoolVal(True)
                terms.append(z3.And(m.to == t_sq, kind_ok, gap_clear, line_clear))
                ahead.append(t_sq)
    return z3.Or(terms) if terms else z3.BoolVal(False)
