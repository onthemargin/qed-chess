from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    stockfish_path: str = "stockfish"
    vertex_location: str = "us-central1"
    vertex_project: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    llm_disabled: bool = False
    rate_limit_per_hour: int = 30
    daily_llm_cap: int = 2000
    prefix: str = "/qed-chess"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            stockfish_path=os.environ.get("STOCKFISH_PATH", "stockfish"),
            vertex_location=os.environ.get("VERTEX_LOCATION", "us-central1"),
            vertex_project=os.environ.get("VERTEX_PROJECT") or None,
            gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
            llm_disabled=os.environ.get("QED_LLM_DISABLED", "") == "1",
            rate_limit_per_hour=int(os.environ.get("QED_RATE_LIMIT_PER_HOUR", "30")),
            daily_llm_cap=int(os.environ.get("QED_DAILY_LLM_CAP", "2000")),
        )
