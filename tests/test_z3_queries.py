import chess
import pytest

from app.z3model import truth
from app.z3model.solve import solve, enumerate_solutions
from tests.positions import CORPUS, SINGLE_KINDS


def _ground_truth(board, constraints):
    """Every python-chess legal move satisfying all constraints, minus the moves
    the Z3 model deliberately leaves to python-chess (castling, en passant)."""
    out = set()
    for m in board.legal_moves:
        if board.is_castling(m) or board.is_en_passant(m):
            continue
        if all(truth.holds(board, m, c) for c in constraints):
            out.add(m)
    return out


@pytest.mark.parametrize("name,fen,tags", CORPUS, ids=[c[0] for c in CORPUS])
@pytest.mark.parametrize("constraint", SINGLE_KINDS, ids=[c["kind"] + (":" + str(list(c.values())[1]) if len(c) > 1 else "") for c in SINGLE_KINDS])
def test_z3_matches_python_chess(name, fen, tags, constraint):
    board = chess.Board(fen)
    expected = _ground_truth(board, [constraint])
    found = enumerate_solutions(board, [constraint], limit=200)
    # soundness: every Z3 move is legal and satisfies the predicate
    for m in found:
        assert m in board.legal_moves, f"{name}: Z3 returned illegal {m}"
        assert truth.holds(board, m, constraint), f"{name}: Z3 move {m} violates {constraint}"
    # completeness (outside castling / en passant)
    assert set(found) == expected, f"{name}: Z3 {sorted(map(str, found))} vs truth {sorted(map(str, expected))}"


def test_conjunction_check_without_queen():
    board = chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4")
    res = solve(board, [{"kind": "gives_check"}, {"kind": "piece_not", "piece": "queen"}])
    assert res.status == "SAT"
    assert res.move == chess.Move.from_uci("c4f7")
    assert res.san == "Bxf7+"
    assert "base model" in res.z3_code


def test_unsat_core_names_conflict():
    # Only the queen can give check here; "not the queen" conflicts with "gives check".
    board = chess.Board("4k3/8/8/8/8/8/8/3QK3 w - - 0 1")
    res = solve(board, [{"kind": "gives_check"}, {"kind": "piece_not", "piece": "queen"}])
    assert res.status == "UNSAT"
    assert "single move" in res.scope
    assert set(res.unsat_core) == {"gives_check", "piece_not"}
    assert any(d["kind"] == "piece_not" and d["sat_without"] for d in res.drop_one)


def test_exclude_gives_another_solution():
    board = chess.Board("4k3/8/8/8/8/8/8/3QK3 w - - 0 1")
    first = solve(board, [{"kind": "gives_check"}])
    assert first.status == "SAT"
    second = solve(board, [{"kind": "gives_check"}], exclude=[first.move.uci()])
    assert second.status == "SAT"
    assert second.move != first.move


def test_mate_in_one_fools_mate():
    board = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2")
    res = solve(board, [{"kind": "mate_in_1"}])
    assert res.status == "SAT"
    assert res.san == "Qh4#"
    assert "python-chess" in res.scope  # mate confirmation is explicitly outside the Z3 single-move model


def test_en_passant_is_out_of_scope():
    board = chess.Board("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2")
    res = solve(board, [{"kind": "captures"}])
    assert res.status == "UNSAT"
    assert "en passant" in res.scope


def test_illegal_due_to_own_pin_is_rejected():
    # Bishop on e2 is pinned by the rook on h1... (king e1, rook h1 -> bishop e2 is not between).
    # Use a real pin: king e1, enemy rook e8, own bishop e2. Bishop may not leave the e-file.
    board = chess.Board("4r2k/8/8/8/8/8/4B3/4K3 w - - 0 1")
    found = enumerate_solutions(board, [{"kind": "piece_is", "piece": "bishop"}], limit=50)
    assert found == []  # every bishop move exposes the king


def test_constraints_text_is_plain_english():
    board = chess.Board()
    res = solve(board, [{"kind": "piece_is", "piece": "knight"}, {"kind": "to_side", "side": "kingside"}])
    assert res.status == "SAT"
    assert res.constraints_text == ["the moved piece is a knight", "the destination is on the kingside (files e-h)"]
