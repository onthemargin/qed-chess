import json
import os
import re

import chess
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.engine import Engine
from app.main import create_app

_ENGINES = []


@pytest.fixture(autouse=True)
def _close_engines():
    yield
    while _ENGINES:
        _ENGINES.pop().close()   # python-chess runs a non-daemon thread; never leave one behind


class FakeGemini:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def classify(self, text, fen, history):
        self.calls.append(text)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make_client(**overrides):
    s = Settings(llm_disabled=True, stockfish_path="/nonexistent/stockfish", rate_limit_per_hour=3, daily_llm_cap=5)
    for k, v in overrides.items():
        setattr(s, k, v)
    gem = overrides.get("gemini")
    eng = Engine(s.stockfish_path)
    _ENGINES.append(eng)
    app = create_app(s, gemini_client=gem, engine=eng)
    return TestClient(app)


START = chess.STARTING_FEN
ITALIAN = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"


def test_healthz():
    r = make_client().get("/qed-chess/healthz")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and j["stockfish"] is False and j["llm"] is False and re.match(r"\d+\.\d+", j["z3_version"])


def test_index_renders_without_inline_code():
    r = make_client().get("/qed-chess/")
    assert r.status_code == 200
    html = r.text
    assert "QED Chess" in html
    assert 'src="/qed-chess/static/vendor/htmx.min.js"' in html
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", html), "inline script found"
    assert " style=" not in html and "<style" not in html
    assert "hx-on" not in html
    assert 'rel="manifest"' in html


def test_ask_structured_query_sat():
    r = make_client().post("/qed-chess/api/ask", json={"fen": ITALIAN, "moves": [], "query": {"constraints": [{"kind": "gives_check"}, {"kind": "piece_not", "piece": "queen"}]}})
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "SAT" and j["model"] == {"move": "c4f7", "san": "Bxf7+"}
    assert j["constraints"] == ["the move gives check", "the moved piece is not the queen"]
    assert "base model" in j["z3_code"]
    assert "c4" in j["highlights"]["squares"] and "f7" in j["highlights"]["squares"]
    assert j["highlights"]["arrows"][0] == ["c4", "f7"]
    assert j["bubbles"][0]["label"] == "Show another solution"
    assert j["answer"].startswith("Yes. Bxf7+")


def test_ask_typed_text_parsed_without_llm():
    r = make_client().post("/qed-chess/api/ask", json={"fen": ITALIAN, "moves": [], "text": "can I give check without moving my queen?"})
    assert r.json()["status"] == "SAT"


def test_ask_unsat_has_core_and_scope():
    r = make_client().post("/qed-chess/api/ask", json={"fen": "4k3/8/8/8/8/8/8/3QK3 w - - 0 1", "moves": [],
                                                      "query": {"constraints": [{"kind": "gives_check"}, {"kind": "piece_not", "piece": "queen"}]}})
    j = r.json()
    assert j["status"] == "UNSAT" and j["model"] is None
    assert "single move" in j["scope"]
    assert set(j["unsat_core"]) == {"gives_check", "piece_not"}
    assert any(b["label"] == "Which constraint breaks it?" for b in j["bubbles"])


def test_ask_unmatched_text_llm_disabled_is_unsupported():
    j = make_client().post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "tell me a joke"}).json()
    assert j["status"] == "UNSUPPORTED" and j["bubbles"]
    # the reply must stand on its own: it names real suggestions instead of trailing off into the bubble strip
    assert not j["answer"].rstrip().endswith(":")
    labels = [b["label"] for b in j["bubbles"] if "query" in b]
    assert any(f"“{l}”" in j["answer"] for l in labels[:2]), j["answer"]


def test_ask_llm_chat_and_query_paths():
    gem = FakeGemini({"kind": "chat", "constraints": None, "reply": "I can answer questions about single moves."})
    c = make_client(llm_disabled=False, gemini=gem)
    j = c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "tell me a joke"}).json()
    assert j["status"] == "CHAT" and "single moves" in j["answer"] and gem.calls == ["tell me a joke"]
    gem.result = {"kind": "query", "constraints": [{"kind": "develops"}], "reply": None}
    j = c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "something the parser misses"}).json()
    assert j["status"] == "SAT" and j["model"]["san"] in ("Nf3", "Nc3", "Na3", "Nh3")


def test_llm_rate_limit_per_ip():
    gem = FakeGemini({"kind": "chat", "constraints": None, "reply": "ok"})
    c = make_client(llm_disabled=False, gemini=gem, rate_limit_per_hour=2)
    for _ in range(2):
        assert c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "zzz"}, headers={"X-Real-IP": "9.9.9.9"}).status_code == 200
    r = c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "zzz"}, headers={"X-Real-IP": "9.9.9.9"})
    assert r.status_code == 429
    assert c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "zzz"}, headers={"X-Real-IP": "8.8.8.8"}).status_code == 200


def test_llm_failure_degrades_to_unsupported():
    from app.gemini import GeminiError
    c = make_client(llm_disabled=False, gemini=FakeGemini(GeminiError("down")))
    j = c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "zzz"}).json()
    assert j["status"] == "UNSUPPORTED"


def test_ask_rejects_bad_input():
    c = make_client()
    assert c.post("/qed-chess/api/ask", json={"fen": "not a fen", "moves": [], "text": "x"}).status_code == 400
    assert c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "query": {"constraints": [{"kind": "nope"}]}}).status_code == 400
    assert c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "query": {"constraints": [{"kind": "to_square", "square": "z9"}]}}).status_code == 400
    assert c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "x" * 5000}).status_code == 400


def test_play_and_legal_with_fallback_engine():
    c = make_client()
    j = c.get("/qed-chess/api/legal", params={"fen": START}).json()
    assert "e2e4" in j["moves"] and len(j["moves"]) == 20
    r = c.post("/qed-chess/api/play", json={"fen": START, "moves": [], "move": "e2e4", "preset": "club"})
    assert r.status_code == 200
    j = r.json()
    assert j["human"]["san"] == "e4"
    assert j["reply"]["uci"] in [m.uci() for m in chess.Board(j["human"]["fen"]).legal_moves]
    assert j["game_over"] is False and j["engine"] == "random"
    assert c.post("/qed-chess/api/play", json={"fen": START, "moves": [], "move": "e2e5", "preset": "club"}).status_code == 400


def test_play_auto_promotes_and_detects_game_over():
    c = make_client()
    j = c.post("/qed-chess/api/play", json={"fen": "7k/4P3/8/8/8/8/8/K7 w - - 0 1", "moves": [], "move": "e7e8", "preset": "casual"}).json()
    assert j["human"]["san"] == "e8=Q+"
    j = c.post("/qed-chess/api/play", json={"fen": "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq - 0 2", "moves": [], "move": "d8h4", "preset": "casual"}).json()
    assert j["game_over"] is True and j["result"] == "0-1" and j["reply"] is None


def test_htmx_fragments():
    c = make_client()
    r = c.post("/qed-chess/ask", data={"fen": ITALIAN, "moves": "[]", "query": json.dumps({"constraints": [{"kind": "gives_check"}]})})
    assert r.status_code == 200 and "Bxf7+" in r.text
    assert 'data-squares="c4,f7,e8"' in r.text and 'data-arrows="c4-f7,f7-e8"' in r.text and 'hx-swap-oob' in r.text
    assert " style=" not in r.text and "<script" not in r.text
    r = c.post("/qed-chess/bubbles", data={"fen": START, "moves": "[]"})
    assert r.status_code == 200 and "hx-post" in r.text
    r = c.get("/qed-chess/model.html")
    assert r.status_code == 200 and r.text.count("<details") == 6 and "def king_safety" in r.text


def test_pwa_assets():
    c = make_client()
    assert c.get("/qed-chess/manifest.webmanifest").status_code == 200
    sw = c.get("/qed-chess/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
    assert c.get("/qed-chess/static/app.js").status_code == 200
    assert c.get("/qed-chess/static/app.css").status_code == 200


@pytest.mark.skipif(not os.environ.get("STOCKFISH_PATH") or not os.path.exists(os.environ["STOCKFISH_PATH"]), reason="no local stockfish")
def test_real_stockfish_if_available():
    c = make_client(stockfish_path=os.environ["STOCKFISH_PATH"])
    assert c.get("/qed-chess/healthz").json()["stockfish"] is True
    j = c.post("/qed-chess/api/play", json={"fen": START, "moves": [], "move": "e2e4", "preset": "strong"}).json()
    assert j["engine"] == "stockfish"


def test_engine_moves_for_the_side_to_move():
    """Switching colours mid-game hands the current position to the engine."""
    c = make_client()
    r = c.post("/qed-chess/api/engine", json={"fen": START, "moves": [], "preset": "club"})
    assert r.status_code == 200
    j = r.json()
    assert j["reply"]["uci"] in [m.uci() for m in chess.Board(START).legal_moves]
    assert chess.Board(j["reply"]["fen"]).turn is chess.BLACK
    assert j["game_over"] is False and j["engine"] == "random" and j["in_check"] is False
    mated = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"
    j = c.post("/qed-chess/api/engine", json={"fen": mated, "moves": [], "preset": "club"}).json()
    assert j["reply"] is None and j["game_over"] is True and j["result"] == "0-1"
    assert c.post("/qed-chess/api/engine", json={"fen": "not a fen", "moves": [], "preset": "club"}).status_code == 400


def test_replay_rebuilds_the_game_for_undo():
    c = make_client()
    r = c.post("/qed-chess/api/replay", json={"moves": ["e2e4", "e7e5", "g1f3"]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["sans"] == ["e4", "e5", "Nf3"] and j["fen"].split()[1] == "b" and "b8c6" in j["legal"]
    assert c.post("/qed-chess/api/replay", json={"moves": ["e2e5"]}).status_code == 400
