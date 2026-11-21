# QED Chess

Play chess. Ask the board a question. A theorem prover answers, and shows its proof.

Live at https://app.gyatso.me/qed-chess/ (served by the app.gyatso.me monorepo container).

## What it is

A normal game against Stockfish, with an assistant beside the board that answers
questions about **one move in the current position** by building a constraint
model and handing it to [Z3](https://github.com/Z3Prover/z3):

- "Can I give check without moving my queen?" → constraints → **SAT**, and the model *is* the move.
- Add a restriction and get **UNSAT**, with the unsat core saying which conditions clash.
- Every answer unfolds in three steps: plain-English constraints, solver verdict + model, the actual Z3 code.
- "View the model" shows the base model's own source, extracted from `app/z3model/base.py`.

Suggested-question bubbles are chosen from the game state and map straight to
structured queries. Typed text goes through a deterministic parser first; only
if that fails does one Gemini call (Vertex AI) translate it into a query or a
short reply. Explanations are templates over solver results, never LLM prose.

## Honesty rules (enforced in code and tests)

- Scope is a single move. Castling, en passant, repetition and the fifty-move
  rule are delegated to python-chess, and UNSAT answers say so when such a move
  would have satisfied the question.
- Every Z3 model is re-validated with python-chess (`app/z3model/truth.py`). A
  disagreement is reported as an error, never shown as a move.
- Mate-in-1: Z3 proves "gives check"; python-chess confirms there is no reply.
  The answer's scope line states this.
- `tests/test_z3_queries.py` checks soundness *and* completeness of the Z3
  model against python-chess over a 22-position corpus for every query kind.

## Layout

```
app/main.py          FastAPI app; all routes under /qed-chess
app/z3model/base.py  the base model (board, move variables, movement, attacks, king safety, scope)
app/z3model/queries.py  per-question constraints
app/z3model/solve.py    solve, cross-check, unsat core / drop-one analysis
app/z3model/truth.py    python-chess oracle
app/bubbles.py       suggested questions from game state
app/intent.py        deterministic text → query parser
app/gemini.py        Vertex AI Gemini client (structured JSON output, schema-validated)
app/engine.py        Stockfish via python-chess, random-move fallback
app/templates/       Jinja2 (index, htmx fragments)
app/static/          app.css, app.js, sw.js, manifest, icons, vendor/htmx.min.js
tests/               pytest
```

## Run locally

```
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
./run_tests.sh
.venv/bin/uvicorn app.main:app --port 3006        # http://127.0.0.1:3006/qed-chess/
```

Environment (all optional):

| Variable | Default | Meaning |
|---|---|---|
| `STOCKFISH_PATH` | `stockfish` | UCI binary; if missing, a random legal move is played and `/healthz` reports `stockfish:false` |
| `VERTEX_PROJECT` | from ADC | GCP project for Gemini |
| `VERTEX_LOCATION` | `us-central1` | Vertex region |
| `GEMINI_MODEL` | `gemini-2.5-flash` | model id |
| `QED_LLM_DISABLED` | unset | `1` disables the LLM path entirely (bubbles and the parser still work) |
| `QED_RATE_LIMIT_PER_HOUR` | `30` | LLM calls per client IP (`X-Real-IP`, else peer) per hour |
| `QED_DAILY_LLM_CAP` | `2000` | process-wide LLM calls per UTC day |

Production: Alpine image, Z3 from the `py3-z3` apk package (PyPI has no musl
wheels), venv created with `--system-site-packages`, Stockfish compiled in a
builder stage. Start: `uvicorn app.main:app --host 127.0.0.1 --port 3006`.

## API

- `POST /qed-chess/api/ask` `{fen, moves, text?, query?, exclude?, context?, history?}` →
  `{status: SAT|UNSAT|CHAT|UNSUPPORTED|ERROR, answer, scope, constraints, model, z3_code, unsat_core, drop_one, highlights, query, bubbles}`
- `POST /qed-chess/api/play` `{fen, moves, move, preset}` → human move + engine reply (auto-queens a bare promotion)
- `GET /qed-chess/api/legal?fen=` · `POST /qed-chess/api/replay {moves}` · `GET /qed-chess/api/model` · `GET /qed-chess/healthz`
- htmx fragments: `POST /ask`, `POST /bubbles`, `GET /model.html`

## Deviations from the house stack

- No Tailwind: the strict CSP (`script-src 'self'`, `style-src 'self'`, no
  eval) and the no-build-step rule rule out the Tailwind Play CDN, so the
  stylesheet is hand-written (`app/static/app.css`).
- No Alpine.js: the UI state is a small plain-JS file; htmx (vendored, pinned,
  `allowEval=false`) handles the chat fragments. See `app/static/vendor/VENDOR.md`.
- "Play as Black/White" switches sides in the current position and lets the
  engine move for the colour you left. Promotions auto-queen.

## Security notes

- Vertex auth is ADC; no API keys anywhere. Config values that reach the Vertex
  URL are regex-checked. Typed text is capped at 300 chars, whitespace-collapsed
  and fenced as `<UNTRUSTED_INPUT>` with tag neutralisation; the output schema
  has no free-form URL field and is validated after parsing.
- No inline scripts or styles are served (asserted by tests), so the page runs
  under the nginx CSP without exceptions.
- The per-IP rate limit keys on `X-Real-IP` when present. The app is meant to
  sit behind a reverse proxy that sets that header from the real peer address
  (nginx in production); if you expose uvicorn directly, clients can spoof it,
  so strip or ignore the header at your edge.
- The server is stateless: games and chat live in the browser's localStorage.
  Nothing a player types is stored server-side; typed questions are sent to
  Vertex AI only when the deterministic parser cannot handle them.

## Licence

MIT, see `LICENSE`. Third-party assets keep their own licences, recorded with
checksums in `app/static/vendor/VENDOR.md`: htmx (BSD 2-Clause) and the
cburnett chess piece set (GPLv2+, also CC BY-SA 3.0 / BSD for the originals).
