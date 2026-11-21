import chess
from app.z3model import truth


def test_fork_truth():
    b = chess.Board("r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1")
    assert truth.holds(b, chess.Move.from_uci("b5c7"), {"kind": "fork"})      # Nc7+ hits a8 and e8
    assert not truth.holds(b, chess.Move.from_uci("b5d6"), {"kind": "fork"})  # Nd6+ hits only the king


def test_pin_truth():
    b = chess.Board("4k3/4q3/8/8/8/8/8/R3K3 w - - 0 1")
    assert truth.holds(b, chess.Move.from_uci("a1e1"), {"kind": "pin"})       # Re1 pins Qe7 to Ke8
    assert not truth.holds(b, chess.Move.from_uci("a1a7"), {"kind": "pin"})   # Ra7 attacks Qe7, nothing behind
    assert not truth.holds(b, chess.Move.from_uci("a1a8"), {"kind": "pin"})   # Ra8+ is a check, not a pin


def test_safe_and_defends_truth():
    b = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3")
    assert truth.holds(b, chess.Move.from_uci("f1c4"), {"kind": "safe"})
    assert truth.holds(b, chess.Move.from_uci("f1b5"), {"kind": "piece_safe", "square": "e4"})
    assert truth.holds(b, chess.Move.from_uci("b1c3"), {"kind": "defends", "square": "e4"})
    assert not truth.holds(b, chess.Move.from_uci("f1c4"), {"kind": "defends", "square": "e4"})
