"""Structured event logs: one JSON line per ask / play / engine / replay so a game and its
chat can be followed in Cloud Logging by session and game id."""
import json
import logging
import re

import pytest

from app.gemini import GeminiError
from tests.test_api import ITALIAN, START, FakeGemini, make_client

TRACE = {"X-QED-Session": "s1a2b3c4d5", "X-QED-Game": "g9f8e7"}


@pytest.fixture
def events(caplog):
    caplog.set_level(logging.INFO, logger="qed.events")

    def _events(ev=None):
        out = [json.loads(r.getMessage()) for r in caplog.records if r.name == "qed.events"]
        return [e for e in out if ev is None or e["ev"] == ev]
    return _events


def test_bubble_ask_is_logged_with_ids_and_outcome(events):
    c = make_client()
    r = c.post("/qed-chess/api/ask", json={"fen": ITALIAN, "moves": ["e2e4"], "query": {"constraints": [{"kind": "gives_check"}]}}, headers=TRACE)
    assert r.status_code == 200
    (e,) = events("ask")
    assert e["sid"] == "s1a2b3c4d5" and e["gid"] == "g9f8e7"
    assert e["source"] == "bubble" and e["status"] == "SAT" and e["move"] == "c4f7"
    assert e["fen"] == ITALIAN and e["ply"] == 1
    assert e["constraints"] == [{"kind": "gives_check"}]
    assert isinstance(e["ms"], int)
    assert re.fullmatch(r"[0-9a-f]{10}", e["ip"]) and "testclient" not in json.dumps(e)   # hashed, never raw
    assert "answer" in e and len(e["answer"]) <= 160


def test_typed_llm_and_failure_paths_record_their_source(events):
    c = make_client()
    c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "can I give check"})
    e = events("ask")[-1]
    assert e["source"] == "typed" and e["text"] == "can I give check" and e["status"] == "UNSAT"

    gem = FakeGemini({"kind": "chat", "constraints": None, "reply": "Only single moves here."})
    c = make_client(llm_disabled=False, gemini=gem)
    c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "tell me a joke"})
    e = events("ask")[-1]
    assert e["source"] == "llm" and e["llm_kind"] == "chat" and isinstance(e["llm_ms"], int) and e["status"] == "CHAT"

    c = make_client(llm_disabled=False, gemini=FakeGemini(GeminiError("Vertex AI returned status 503")))
    c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "tell me a joke"})
    e = events("ask")[-1]
    assert e["source"] == "llm" and "503" in e["llm_error"] and e["status"] == "UNSUPPORTED"


def test_errors_and_rate_limits_are_logged_too(events):
    c = make_client()
    assert c.post("/qed-chess/api/ask", json={"fen": "not a fen", "moves": [], "text": "hi"}).status_code == 400
    e = events("ask")[-1]
    assert e["error"].startswith("400") and "status" not in e
    c = make_client(llm_disabled=False, gemini=FakeGemini({"kind": "chat", "constraints": None, "reply": "x"}))
    for _ in range(3):
        c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "zzz"}, headers={"X-Real-IP": "9.9.9.9"})
    assert c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "zzz"}, headers={"X-Real-IP": "9.9.9.9"}).status_code == 429
    assert events("ask")[-1]["error"].startswith("429")


def test_htmx_ask_logs_the_question_text(events):
    c = make_client()
    r = c.post("/qed-chess/ask", data={"fen": START, "moves": "[]", "text": "can I give check"}, headers=TRACE)
    assert r.status_code == 200
    e = events("ask")[-1]
    assert e["text"] == "can I give check" and e["source"] == "typed" and e["gid"] == "g9f8e7"


def test_play_engine_and_replay_are_logged(events):
    c = make_client()
    c.post("/qed-chess/api/play", json={"fen": START, "moves": [], "move": "e2e4", "preset": "club"}, headers=TRACE)
    (p,) = events("play")
    assert p["gid"] == "g9f8e7" and p["move"] == "e2e4" and p["san"] == "e4" and p["preset"] == "club"
    assert p["reply"] and p["engine"] == "random" and p["game_over"] is False and isinstance(p["ms"], int)
    c.post("/qed-chess/api/engine", json={"fen": START, "moves": [], "preset": "club"}, headers=TRACE)
    (g,) = events("engine")
    assert g["reply"] and g["engine"] == "random" and g["ply"] == 0
    c.post("/qed-chess/api/replay", json={"moves": ["e2e4", "e7e5"]}, headers=TRACE)
    (rp,) = events("replay")
    assert rp["ply"] == 2 and rp["sid"] == "s1a2b3c4d5"
    assert c.post("/qed-chess/api/play", json={"fen": START, "moves": [], "move": "e2e5", "preset": "club"}).status_code == 400
    assert events("play")[-1]["error"].startswith("400")


def test_events_are_one_json_line_each_and_ids_are_validated(events, caplog):
    c = make_client()
    c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "x" * 50}, headers={"X-QED-Session": "bad header!", "X-QED-Game": "g" * 80})
    for r in caplog.records:
        if r.name == "qed.events":
            assert "\n" not in r.getMessage()
    e = events("ask")[-1]
    assert "sid" not in e and "gid" not in e


def test_client_sends_session_and_game_ids():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "app" / "static" / "app.js").read_text()
    assert "'X-QED-Session'" in js and "'X-QED-Game'" in js and "qed.sid" in js
    assert "htmx:configRequest" in js and "e.detail.headers" in js


# ---- review findings 2026-11-21

def test_ip_hash_is_keyed_not_a_bare_sha256():
    import hashlib
    from app.eventlog import ip_hash
    assert ip_hash("1.2.3.4") != hashlib.sha256(b"1.2.3.4").hexdigest()[:10]
    assert ip_hash("1.2.3.4") == ip_hash("1.2.3.4") and ip_hash("1.2.3.4") != ip_hash("1.2.3.5")


def test_unexpected_server_errors_are_tagged_500(events, monkeypatch):
    from app import main as main_mod
    monkeypatch.setattr(main_mod.solve_mod, "solve", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    c = make_client()
    with pytest.raises(RuntimeError):
        c.post("/qed-chess/api/ask", json={"fen": ITALIAN, "moves": [], "query": {"constraints": [{"kind": "gives_check"}]}})
    e = events("ask")[-1]
    assert e["error"] == "500 RuntimeError" and "status" not in e


def test_source_says_llm_only_when_gemini_was_called(events):
    c = make_client()   # llm disabled
    c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "tell me a joke"})
    e = events("ask")[-1]
    assert e["source"] == "unparsed" and "llm_ms" not in e and e["status"] == "UNSUPPORTED"
    gem = FakeGemini({"kind": "chat", "constraints": None, "reply": "x"})
    c = make_client(llm_disabled=False, gemini=gem, daily_llm_cap=0)
    c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "tell me a joke"})
    e = events("ask")[-1]
    assert e["source"] == "capped" and gem.calls == []
    c = make_client(llm_disabled=False, gemini=gem)
    c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "tell me a joke"})
    assert events("ask")[-1]["source"] == "llm"


def test_replay_event_records_result_timing_and_errors(events):
    c = make_client()
    c.post("/qed-chess/api/replay", json={"moves": ["e2e4", "e7e5"]}, headers=TRACE)
    e = events("replay")[-1]
    assert e["ply"] == 2 and isinstance(e["ms"], int) and e["fen"].split()[1] == "w"
    assert c.post("/qed-chess/api/replay", json={"moves": ["e2e4", "e2e4"]}).status_code == 400
    assert events("replay")[-1]["error"].startswith("400")


def test_client_has_no_dead_gid_backfill():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "app" / "static" / "app.js").read_text()
    assert "if (!state.gid) state.gid = rid();" not in js


def test_readme_privacy_section_describes_the_logging():
    from pathlib import Path
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
    assert "Nothing a player types is stored server-side" not in readme
    sec = readme.split("## Security notes", 1)[1].split("\n## ", 1)[0]
    assert "log" in sec.lower() and "retention" in sec.lower()
