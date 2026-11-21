"""Robustness contracts for the public endpoints: serialized Z3 work, solve timeouts,
per-client limits, an in-flight cap, and strict request validation."""
import json
import threading

import chess
import pytest

from app.z3model import solve as solve_mod
from tests.test_api import ITALIAN, START, make_client

HEAVY = "qqqqqqqk/qqqqqqqq/8/8/8/8/QQQQQQQQ/QQQQQQQK w - - 0 1"
HEAVY_CONS = [{"kind": "mate_in_1"}, {"kind": "fork"}, {"kind": "pin"}, {"kind": "safe"}, {"kind": "gives_check"}]


def test_all_z3_work_runs_under_one_process_lock(monkeypatch):
    class Spy:
        def __init__(self):
            self.n = 0
            self.lock = threading.Lock()
        def __enter__(self):
            self.n += 1
            return self.lock.__enter__()
        def __exit__(self, *a):
            return self.lock.__exit__(*a)
    spy = Spy()
    monkeypatch.setattr(solve_mod, "_Z3_LOCK", spy)
    solve_mod.solve(chess.Board(ITALIAN), [{"kind": "gives_check"}])
    assert spy.n == 1


def test_concurrent_solves_do_not_crash_the_process():
    results, errors = [], []
    def run():
        try:
            results.append(solve_mod.solve(chess.Board(ITALIAN), [{"kind": "gives_check"}, {"kind": "safe"}]).status)
        except Exception as e:  # pragma: no cover
            errors.append(repr(e))
    ts = [threading.Thread(target=run) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors and len(results) == 8 and set(results) <= {"SAT", "UNSAT"}


def test_solver_timeout_is_reported_as_an_error(monkeypatch):
    monkeypatch.setattr(solve_mod, "Z3_TIMEOUT_MS", 1)
    res = solve_mod.solve(chess.Board(HEAVY), HEAVY_CONS)
    assert res.status == "ERROR" and "timed out" in res.message
    monkeypatch.undo()
    assert solve_mod.Z3_TIMEOUT_MS >= 1000


def test_solver_and_move_endpoints_are_rate_limited_per_client():
    c = make_client(solve_limit_per_hour=2)
    body = {"fen": ITALIAN, "moves": [], "query": {"constraints": [{"kind": "gives_check"}]}}
    assert c.post("/qed-chess/api/ask", json=body, headers={"X-Real-IP": "7.7.7.7"}).status_code == 200
    assert c.post("/qed-chess/api/ask", json=body, headers={"X-Real-IP": "7.7.7.7"}).status_code == 200
    r = c.post("/qed-chess/api/ask", json=body, headers={"X-Real-IP": "7.7.7.7"})
    assert r.status_code == 429 and "error" in r.json()
    assert c.post("/qed-chess/api/ask", json=body, headers={"X-Real-IP": "7.7.7.8"}).status_code == 200   # another client is fine
    c = make_client(move_limit_per_hour=1)
    play = {"fen": START, "moves": [], "move": "e2e4", "preset": "club"}
    assert c.post("/qed-chess/api/play", json=play, headers={"X-Real-IP": "7.7.7.7"}).status_code == 200
    assert c.post("/qed-chess/api/play", json=play, headers={"X-Real-IP": "7.7.7.7"}).status_code == 429
    assert c.post("/qed-chess/api/engine", json={"fen": START, "moves": [], "preset": "club"}, headers={"X-Real-IP": "7.7.7.7"}).status_code == 429


def test_solver_busy_returns_503_instead_of_queueing_forever():
    c = make_client(solve_queue=1)
    slots = c.app.state.solve_slots
    assert slots.acquire(blocking=False)
    try:
        r = c.post("/qed-chess/api/ask", json={"fen": ITALIAN, "moves": [], "query": {"constraints": [{"kind": "gives_check"}]}})
        assert r.status_code == 503 and "busy" in r.json()["error"].lower()
    finally:
        slots.release()
    assert c.post("/qed-chess/api/ask", json={"fen": ITALIAN, "moves": [], "query": {"constraints": [{"kind": "gives_check"}]}}).status_code == 200


def test_malformed_exclude_and_context_are_400_not_500():
    c = make_client()
    for bad in ("e2e4", 5, {"a": 1}):
        r = c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "query": {"constraints": [], "exclude": bad}})
        assert r.status_code == 400 and "exclude" in r.json()["error"], bad
    r = c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "another one", "context": {"constraints": [], "exclude": 5}})
    assert r.status_code == 400
    r = c.post("/qed-chess/api/ask", json={"fen": START, "moves": [], "text": "another one", "context": {"constraints": "zz", "move": 7}})
    assert r.status_code == 400
    r = c.post("/qed-chess/ask", data={"fen": START, "moves": "[]", "query": json.dumps({"constraints": [], "exclude": "zz"})})
    assert r.status_code == 200 and "exclude" in r.text and "Internal Server Error" not in r.text
