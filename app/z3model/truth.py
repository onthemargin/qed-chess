"""Ground truth for every supported constraint, computed with python-chess.

The Z3 model (base.py) must agree with these functions on every move it
returns; solve.py cross-checks each SAT model here and the tests compare the
full solution sets. This file is deliberately boring: it is the oracle.
"""
from __future__ import annotations

import chess

PIECE_NAMES = {
    "pawn": chess.PAWN, "knight": chess.KNIGHT, "bishop": chess.BISHOP,
    "rook": chess.ROOK, "queen": chess.QUEEN, "king": chess.KING,
}
PIECE_VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100}

KINDS = (
    "piece_is", "piece_not", "from_square", "to_square", "to_color", "to_side",
    "gives_check", "captures", "not_captures", "captures_piece", "safe",
    "attacks_square", "attacks_piece", "fork", "pin", "piece_safe", "defends",
    "mate_in_1", "develops", "promotes",
)


def piece_type(name: str) -> int:
    try:
        return PIECE_NAMES[name.lower()]
    except KeyError:
        raise ValueError(f"unknown piece {name!r}")


def square(name: str) -> int:
    try:
        return chess.parse_square(name.lower())
    except ValueError:
        raise ValueError(f"unknown square {name!r}")


def _ray_step(a: int, b: int):
    """Unit (file, rank) step from a towards b if collinear, else None."""
    df = chess.square_file(b) - chess.square_file(a)
    dr = chess.square_rank(b) - chess.square_rank(a)
    if df == 0 and dr == 0:
        return None
    if df == 0 or dr == 0 or abs(df) == abs(dr):
        return ((df > 0) - (df < 0), (dr > 0) - (dr < 0))
    return None


def _walk(sq: int, step):
    f, r = chess.square_file(sq) + step[0], chess.square_rank(sq) + step[1]
    while 0 <= f < 8 and 0 <= r < 8:
        yield chess.square(f, r)
        f, r = f + step[0], r + step[1]


def pins_after(board: chess.Board, move: chess.Move) -> list[tuple[int, int]]:
    """(pinned_square, behind_square) pairs created by the moved piece: it is a
    slider attacking an enemy piece with a more valuable enemy piece directly
    behind it on the same ray."""
    after = board.copy(stack=False)
    after.push(move)
    us = board.turn
    piece = after.piece_at(move.to_square)
    if piece is None or piece.piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN):
        return []
    out = []
    for e in after.attacks(move.to_square):
        victim = after.piece_at(e)
        if victim is None or victim.color == us:
            continue
        step = _ray_step(move.to_square, e)
        if step is None:
            continue
        if piece.piece_type == chess.ROOK and 0 not in step:
            continue
        if piece.piece_type == chess.BISHOP and 0 in step:
            continue
        for b in _walk(e, step):
            behind = after.piece_at(b)
            if behind is None:
                continue
            if behind.color != us and PIECE_VALUE[behind.piece_type] > PIECE_VALUE[victim.piece_type]:
                out.append((e, b))
            break
    return out


def holds(board: chess.Board, move: chess.Move, c: dict) -> bool:
    kind = c["kind"]
    us = board.turn
    them = not us
    mover = board.piece_type_at(move.from_square)
    if kind == "piece_is":
        return mover == piece_type(c["piece"])
    if kind == "piece_not":
        return mover != piece_type(c["piece"])
    if kind == "from_square":
        return move.from_square == square(c["square"])
    if kind == "to_square":
        return move.to_square == square(c["square"])
    if kind == "to_color":
        dark = (chess.square_file(move.to_square) + chess.square_rank(move.to_square)) % 2 == 0
        return dark if c["color"] == "dark" else not dark
    if kind == "to_side":
        kingside = chess.square_file(move.to_square) >= 4
        return kingside if c["side"] == "kingside" else not kingside
    if kind == "gives_check":
        return board.gives_check(move)
    if kind == "captures":
        return board.is_capture(move)
    if kind == "not_captures":
        return not board.is_capture(move)
    if kind == "captures_piece":
        if board.is_en_passant(move):
            return piece_type(c["piece"]) == chess.PAWN
        return board.piece_type_at(move.to_square) == piece_type(c["piece"]) and board.color_at(move.to_square) == them
    if kind == "promotes":
        return move.promotion is not None
    if kind == "develops":
        back = 0 if us == chess.WHITE else 7
        return mover in (chess.KNIGHT, chess.BISHOP) and chess.square_rank(move.from_square) == back \
            and chess.square_rank(move.to_square) != back
    if kind == "mate_in_1":
        after = board.copy(stack=False)
        after.push(move)
        return after.is_checkmate()

    after = board.copy(stack=False)
    after.push(move)
    if kind == "safe":
        return not after.is_attacked_by(them, move.to_square)
    if kind == "attacks_square":
        return square(c["square"]) in after.attacks(move.to_square)
    if kind == "attacks_piece":
        t = piece_type(c["piece"])
        return any(after.piece_at(s) == chess.Piece(t, them) for s in after.attacks(move.to_square))
    if kind == "fork":
        n = sum(1 for s in after.attacks(move.to_square)
                if (p := after.piece_at(s)) is not None and p.color == them and p.piece_type != chess.PAWN)
        return n >= 2
    if kind == "pin":
        return bool(pins_after(board, move))
    if kind == "piece_safe":
        x = square(c["square"])
        if board.color_at(x) != us:
            return False
        target = move.to_square if move.from_square == x else x
        return not after.is_attacked_by(them, target)
    if kind == "defends":
        x = square(c["square"])
        if board.color_at(x) != us or move.from_square == x:
            return False
        return after.is_attacked_by(us, x)
    raise ValueError(f"unknown constraint kind {kind!r}")
