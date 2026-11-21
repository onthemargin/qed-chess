import chess
from app.intent import parse

B = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4")


def c(text):
    return parse(text, B)


def test_check_without_queen():
    assert c("Can I give check without moving my queen?") == {"constraints": [{"kind": "gives_check"}, {"kind": "piece_not", "piece": "queen"}]}


def test_fork_and_pin_and_mate():
    assert c("can I fork two pieces") == {"constraints": [{"kind": "fork"}]}
    assert c("pin something?") == {"constraints": [{"kind": "pin"}]}
    assert c("is there a mate in 1") == {"constraints": [{"kind": "mate_in_1"}]}
    assert c("can i checkmate") == {"constraints": [{"kind": "mate_in_1"}]}


def test_capture_variants():
    assert c("can I take the knight") == {"constraints": [{"kind": "captures_piece", "piece": "knight"}]}
    assert c("capture something safely") == {"constraints": [{"kind": "captures"}, {"kind": "safe"}]}


def test_squares_and_sides():
    assert c("can a knight go to d5") == {"constraints": [{"kind": "piece_is", "piece": "knight"}, {"kind": "to_square", "square": "d5"}]}
    assert c("attack f7 with the bishop") == {"constraints": [{"kind": "attacks_square", "square": "f7"}, {"kind": "piece_is", "piece": "bishop"}]}
    assert c("move something to a dark square on the queenside") == {"constraints": [{"kind": "to_color", "color": "dark"}, {"kind": "to_side", "side": "queenside"}]}


def test_defend_resolves_unique_piece_square():
    assert c("can I defend my bishop") == {"constraints": [{"kind": "defends", "square": "c4"}]}
    assert c("is my bishop safe") == {"constraints": [{"kind": "piece_safe", "square": "c4"}]}


def test_actions():
    assert c("show another solution") == {"action": "another"}
    assert c("why is that impossible?") == {"action": "why"}
    assert c("show the z3 code") == {"action": "code"}


def test_unmatched_returns_none():
    assert c("what is the weather like") is None
    assert c("") is None
