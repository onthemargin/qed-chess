"""Fixes from the 2026-11-21 core review: parser phrasing, bubbles, explanations, en passant
safety, engine resilience, refinement exhaustion and client guards."""
import json
import re
from pathlib import Path

import chess
import pytest

from app import bubbles, explain, intent
from app.engine import Engine
from app.z3model import solve as solve_mod
from app.z3model import truth
from tests.test_api import ITALIAN, START, make_client

ROOT = Path(__file__).resolve().parent.parent
IN_CHECK = "rnb1kbnr/pppp1ppp/8/4p3/7q/5P2/PPPPP1PP/RNBQKBNR w KQkq - 1 3"      # Qh4+ ; g3 blocks
ROOK_CHECK = "4k3/8/8/8/8/8/8/4Kr2 w - - 0 1"                                   # Rf1+ ; Kxf1 captures the checker
FAR_CHECK = "k3r3/8/8/8/8/8/3n4/4K3 w - - 0 1"                                  # Re8+ ; Kxd2 is NOT the checker
EP_TRAP = "4k3/8/8/8/3p4/8/4P3/4K3 w - - 0 1"                                   # e4 can be taken en passant
NO_EP = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"
QUEENS_OFF = "r1b1kbnr/pppnpppp/8/8/2pP4/8/PP2PPPP/RNB1KBNR w KQkq - 0 5"


def kinds(q):
    return [c["kind"] for c in q["constraints"]]


# ---- 1. "check" while in check means the check you are in, not giving one
def test_in_check_questions_are_not_parsed_as_giving_check():
    b = chess.Board(IN_CHECK)
    assert intent.parse("How do I get out of check?", b) == {"constraints": []}
    assert intent.parse("I'm in check, what can I do?", b) == {"constraints": []}
    assert kinds(intent.parse("Can I block the check?", b)) == ["piece_not", "not_captures"]
    assert kinds(intent.parse("can I give check?", b)) == ["gives_check"]          # explicit giving still works
    assert kinds(intent.parse("can I check him back", b)) == ["gives_check"]
    assert kinds(intent.parse("can I give check", chess.Board(START))) == ["gives_check"]


# ---- 7. the checking piece is a specific square
def test_capture_the_checking_piece_targets_the_checker():
    b = chess.Board(ROOK_CHECK)
    q = intent.parse("Can I capture the checking piece?", b)
    assert q["constraints"] == [{"kind": "captures"}, {"kind": "to_square", "square": "f1"}]
    res = solve_mod.solve(b, q["constraints"])
    assert res.status == "SAT" and res.san == "Kxf1"
    labels = {x["label"]: x for x in bubbles.suggest(chess.Board(FAR_CHECK))}
    assert labels["Can I capture the checking piece?"]["query"]["constraints"] == [{"kind": "captures"}, {"kind": "to_square", "square": "e8"}]
    assert solve_mod.solve(chess.Board(FAR_CHECK), labels["Can I capture the checking piece?"]["query"]["constraints"]).status == "UNSAT"
    double = chess.Board("4k3/8/8/8/8/2b5/8/r3K3 w - - 0 1")   # Ra1+ and Bc3+: no capture helps
    assert double.is_check() and "Can I capture the checking piece?" not in {x["label"] for x in bubbles.suggest(double)}


# ---- 8. no queen bubble without an enemy queen
def test_attack_the_queen_bubble_needs_an_enemy_queen():
    labels = {x["label"] for x in bubbles.suggest(chess.Board(QUEENS_OFF))}
    assert "Can I attack the queen?" not in labels
    assert "Can I attack the queen?" in {x["label"] for x in bubbles.suggest(chess.Board(START))}


# ---- 4. "<piece> on <sq>" qualifies the piece; the destination is the "to" square
def test_on_square_qualifies_the_piece_and_to_is_the_destination():
    b = chess.Board(START)
    q = intent.parse("Can the knight on g1 go to f3?", b)
    assert q["constraints"] == [{"kind": "piece_is", "piece": "knight"}, {"kind": "to_square", "square": "f3"}, {"kind": "from_square", "square": "g1"}]
    b2 = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
    q = intent.parse("Can I protect the pawn on e4?", b2)
    assert q["constraints"] == [{"kind": "defends", "square": "e4"}]


# ---- 5. "move my <piece>" names the piece
def test_move_my_piece_yields_piece_is():
    b = chess.Board(START)
    assert kinds(intent.parse("Can I move my knight to f3", b)) == ["piece_is", "to_square"]
    assert kinds(intent.parse("Can I move my queen safely?", b)) == ["safe", "piece_is"]


# ---- 6. castling is answered by python-chess, not misread as a side question
def test_castle_is_an_action_answered_from_the_rules():
    assert intent.parse("can I castle kingside", chess.Board(ITALIAN)) == {"action": "castle", "side": "kingside"}
    assert intent.parse("can I castle?", chess.Board(START)) == {"action": "castle"}
    assert "castle" not in intent.PIECES
    c = make_client()
    j = c.post("/qed-chess/api/ask", json={"fen": ITALIAN, "moves": [], "text": "can I castle kingside"}).json()
    assert j["status"] == "CHAT" and "O-O" in j["answer"] and "python-chess" in j["answer"]
    j = c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "can I castle"}).json()
    assert j["status"] == "CHAT" and "cannot castle" in j["answer"].lower()


# ---- 3. UNSAT headline names the castling / en passant move that would do it
def test_unsat_headline_mentions_the_special_move():
    b = chess.Board("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2")
    res = solve_mod.solve(b, [{"kind": "captures"}])
    assert res.status == "UNSAT" and res.special_moves == ["exd6"]
    text = explain.answer_text(res, [{"kind": "captures"}])
    assert "exd6" in text and "en passant" in text.lower()
    b = chess.Board(ITALIAN)
    cons = [{"kind": "piece_is", "piece": "king"}, {"kind": "to_square", "square": "g1"}]
    res = solve_mod.solve(b, cons)
    assert res.status == "UNSAT" and res.special_moves == ["O-O"]
    assert "O-O" in explain.answer_text(res, cons)


# ---- 9. "safe" sees en passant, in Z3 and in the oracle
def test_safe_accounts_for_en_passant():
    b = chess.Board(EP_TRAP)
    assert truth.holds(b, chess.Move.from_uci("e2e4"), {"kind": "safe"}) is False
    assert truth.holds(chess.Board(NO_EP), chess.Move.from_uci("e2e4"), {"kind": "safe"}) is True
    assert truth.holds(b, chess.Move.from_uci("e2e4"), {"kind": "piece_safe", "square": "e2"}) is False
    cons = [{"kind": "piece_is", "piece": "pawn"}, {"kind": "to_square", "square": "e4"}, {"kind": "safe"}]
    assert solve_mod.solve(b, cons).status == "UNSAT"
    assert solve_mod.solve(chess.Board(NO_EP), cons).status == "SAT"
    cons = [{"kind": "piece_safe", "square": "e2"}, {"kind": "to_square", "square": "e4"}]
    assert solve_mod.solve(b, cons).status == "UNSAT"
    assert solve_mod.solve(chess.Board(NO_EP), cons).status == "SAT"


# ---- low: text and arrows
def test_piece_safe_text_names_where_the_piece_ends_up():
    b = chess.Board("4k3/8/8/7q/8/5N2/8/4K3 w - - 0 1")   # Qh5 attacks the knight on f3 along h5-g4-f3
    res = solve_mod.solve(b, [{"kind": "piece_safe", "square": "f3"}])
    assert res.status == "SAT"
    text = explain.answer_text(res, [{"kind": "piece_safe", "square": "f3"}])
    assert "keeps f3 safe" not in text and res.san in text
    hl = explain.highlights(b, res, [{"kind": "piece_safe", "square": "f3"}])
    assert chess.square_name(res.move.to_square) in hl["squares"]


def test_attacks_pawn_draws_an_arrow():
    b = chess.Board("4k3/8/8/3p4/8/8/8/R3K3 w - - 0 1")    # Ra5 attacks the d5 pawn along the rank
    cons = [{"kind": "attacks_piece", "piece": "pawn"}]
    res = solve_mod.solve(b, cons)
    assert res.status == "SAT"
    hl = explain.highlights(b, res, cons)
    assert any(a[1] == "d5" for a in hl["arrows"])


# ---- low: engine survives one transient failure
def test_engine_keeps_stockfish_after_a_transient_failure(monkeypatch):
    class Fake:
        calls = 0
        def configure(self, *_): pass
        def play(self, board, limit):
            Fake.calls += 1
            if Fake.calls == 1:
                raise RuntimeError("engine hiccup")
            class R: move = next(iter(board.legal_moves))
            return R()
        def close(self): pass
    e = Engine("/nonexistent/stockfish")
    e.available = True
    monkeypatch.setattr(e, "_start", lambda: setattr(e, "_engine", Fake()))
    mv, kind = e.best_move(chess.Board(START))
    assert kind == "random" and e.available is True
    mv, kind = e.best_move(chess.Board(START))
    assert kind == "stockfish"
    # three failures in a row do give up
    e2 = Engine("/nonexistent/stockfish"); e2.available = True
    monkeypatch.setattr(e2, "_start", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    for _ in range(3):
        e2.best_move(chess.Board(START))
    assert e2.available is False


# ---- low: refinement exhaustion is an error, not a confident UNSAT
def test_refinement_exhaustion_is_reported_not_unsat(monkeypatch):
    monkeypatch.setattr(solve_mod, "MAX_REFINEMENTS", 1)
    b = chess.Board("4k3/8/8/8/8/8/8/R3K2R w - - 0 1")   # several checks, none mate
    res = solve_mod.solve(b, [{"kind": "mate_in_1"}])
    assert res.status == "ERROR" and "candidate" in res.message
    assert solve_mod.MAX_REFINEMENTS == 1   # the test's patch; the real cap is larger
    monkeypatch.undo()
    assert solve_mod.MAX_REFINEMENTS >= 256


# ---- low: "why" is only an action when the sentence is about the last answer
def test_why_with_a_piece_is_a_question_not_an_action():
    b = chess.Board(START)
    assert intent.parse("why?", b) == {"action": "why"}
    assert intent.parse("why is that impossible", b) == {"action": "why"}
    assert kinds(intent.parse("Why can't my bishop move?", b)) == ["piece_is"]


# ---- client guards (2, 10, low)
def test_client_guards_new_game_asks_and_clicks_during_the_reply():
    js = (ROOT / "app/static/app.js").read_text()
    new_game = js.split("function newGame()", 1)[1].split("\n  }", 1)[0]
    assert "state.busy" in new_game
    cfg = js.split("htmx:configRequest", 1)[1].split("});", 1)[0]
    assert "state.busy" in cfg and "preventDefault" in cfg
    assert "dataset.fen === state.fen" in cfg          # stale context is not reused after a move
    click = js.split("$('#board').addEventListener('click'", 1)[1].split("});", 1)[0]
    assert "state.human" in click
    assert re.search(r"function setAsking\(|function lockChat\(", js)   # bubbles and input are frozen while the engine replies
