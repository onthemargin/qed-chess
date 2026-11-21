import chess
from app.bubbles import suggest


def labels(board, last=None):
    out = suggest(board, last)
    assert 2 <= len(out) <= 4
    for b in out:
        assert len(b["label"]) <= 48
        assert "query" in b or "action" in b
    return [b["label"] for b in out]


def test_opening_bubbles():
    ls = labels(chess.Board())
    assert "Can I develop a piece safely?" in ls


def test_in_check_bubbles():
    ls = labels(chess.Board("rnb1kbnr/pppp1ppp/8/4p3/7q/5P2/PPPPP1PP/RNBQKBNR w KQkq - 1 3"))
    assert "Can I block instead of moving the king?" in ls
    assert "Can I escape without moving my king?" in ls


def test_hanging_piece_bubbles():
    # Black knight on e4 is attacked by Nc3-side? Use a plain hanging queen.
    ls = labels(chess.Board("4k3/8/8/3q4/8/8/3R4/4K3 w - - 0 1"))
    assert "Can I capture the queen?" in ls


def test_attacked_own_piece_bubbles():
    ls = labels(chess.Board("4k3/8/8/3r4/8/8/3B4/4K3 w - - 0 1"))  # Bd2 attacked by Rd5
    assert "Can I save my bishop on d2?" in ls


def test_quiet_middlegame_bubbles():
    ls = labels(chess.Board("r1bq1rk1/ppp2ppp/2n2n2/3pp3/1bPP4/2N1PN2/PP3PPP/R1BQKB1R w KQ - 0 7"))
    assert "Can I give check without moving my queen?" in ls or "Can I fork two pieces?" in ls or "Can I pin a piece?" in ls


def test_endgame_bubbles():
    ls = labels(chess.Board("8/4P1k1/8/8/8/8/8/4K3 w - - 0 1"))
    assert "Can I promote a pawn?" in ls


def test_followups_after_sat_and_unsat():
    board = chess.Board()
    sat = suggest(board, {"status": "SAT", "constraints": [{"kind": "develops"}], "move": "g1f3"})
    assert sat[0]["label"] == "Show another solution"
    assert sat[0]["query"]["exclude"] == ["g1f3"]
    unsat = suggest(board, {"status": "UNSAT", "constraints": [{"kind": "gives_check"}, {"kind": "piece_not", "piece": "queen"}]})
    ls = [b["label"] for b in unsat]
    assert "Which constraint breaks it?" in ls
    assert any(l.startswith("Drop ") for l in ls)


def test_game_over_bubble():
    out = suggest(chess.Board("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"))
    assert out == [{"label": "Start a new game", "action": "new_game"}]
