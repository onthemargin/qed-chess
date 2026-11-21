"""The opponent: Stockfish over UCI via python-chess, one process, one lock.
Falls back to a random legal move when the binary is missing so the app and
its tests run anywhere."""
from __future__ import annotations

import atexit
import logging
import random
import shutil
import threading

import chess
import chess.engine

log = logging.getLogger("qed.engine")

PRESETS = {
    "casual": {"skill": 1, "time": 0.1},
    "club": {"skill": 8, "time": 0.3},
    "strong": {"skill": 20, "time": 0.8},
}


class Engine:
    def __init__(self, path: str = "stockfish"):
        self.path = path
        self._lock = threading.Lock()
        self._engine = None
        self._failures = 0          # consecutive failures; one hiccup must not disable Stockfish for good
        # Lazy start: the UCI process is spawned on the first move request, so
        # importing the app (or probing /healthz) never leaves a process behind.
        self.available = bool(shutil.which(path)) if path else False

    def _start(self):
        self._engine = chess.engine.SimpleEngine.popen_uci(self.path, timeout=10)
        atexit.register(self.close)   # never leave a UCI process (and its reader thread) behind

    def best_move(self, board: chess.Board, preset: str = "club") -> tuple[chess.Move, str]:
        p = PRESETS.get(preset, PRESETS["club"])
        if self.available:
            with self._lock:
                try:
                    if self._engine is None:
                        self._start()
                    self._engine.configure({"Skill Level": p["skill"]})
                    res = self._engine.play(board, chess.engine.Limit(time=p["time"]))
                    if res.move is not None:
                        self._failures = 0
                        return res.move, "stockfish"
                except Exception as e:
                    self._failures += 1
                    log.warning("stockfish failed (%d in a row), falling back: %s", self._failures, e)
                    if self._engine is not None:
                        try:
                            self._engine.close()
                        except Exception:
                            pass
                    self._engine = None            # restarted on the next request
                    if self._failures >= 3:
                        self.available = False
        return random.choice(list(board.legal_moves)), "random"

    def close(self):
        with self._lock:
            if self._engine is not None:
                try:
                    self._engine.quit()
                except Exception:
                    pass
                self._engine = None
