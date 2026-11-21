"""QED Chess: FastAPI app. Every route lives under /qed-chess."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path

import chess
import z3
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Form, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import bubbles as bubbles_mod
from . import explain, intent
from .config import Settings
from .engine import PRESETS, Engine
from .eventlog import Timer, ip_hash, logged
from .eventlog import configure as configure_logging
from .gemini import MAX_INPUT, GeminiClient, GeminiError, default_project
from .ratelimit import DailyCap, RateLimiter
from .z3model import solve as solve_mod
from .z3model import source, truth

log = logging.getLogger("qed")
HERE = Path(__file__).parent
MAX_CONSTRAINTS = 6
MAX_MOVES = 600


class AskIn(BaseModel):
    fen: str = Field(max_length=120)
    moves: list[str] = Field(default_factory=list, max_length=MAX_MOVES)
    text: str | None = Field(default=None, max_length=2000)
    query: dict | None = None
    exclude: list[str] | None = Field(default=None, max_length=64)
    context: dict | None = None
    history: list[str] = Field(default_factory=list, max_length=8)


class PlayIn(BaseModel):
    fen: str = Field(max_length=120)
    moves: list[str] = Field(default_factory=list, max_length=MAX_MOVES)
    move: str = Field(max_length=5)
    preset: str = "club"


class EngineIn(BaseModel):
    fen: str = Field(max_length=120)
    moves: list[str] = Field(default_factory=list, max_length=MAX_MOVES)
    preset: str = "club"


class ReplayIn(BaseModel):
    # Module level on purpose: a model defined inside create_app() cannot be resolved from the
    # route's string annotations (from __future__ import annotations), and FastAPI then treats
    # the body as a missing query parameter -> every /api/replay was a 400 and Undo did nothing.
    moves: list[str] = Field(default_factory=list, max_length=MAX_MOVES)


def _at_label(fen: str, moves: list[str]) -> str:
    """Where in the game a question was asked, for the chat transcript: 'move 12 · after 11…Nf6'."""
    board = _board(fen)
    n = board.fullmove_number
    b, sans = chess.Board(), []
    for u in moves:
        try:
            mv = chess.Move.from_uci(u)
        except ValueError:
            break
        if mv not in b.legal_moves:
            break
        sans.append(b.san(mv))
        b.push(mv)
    if b.fen() == board.fen():
        if not sans:
            return f"move {n} · start"
        i = len(sans) - 1
        last = f"{i // 2 + 1}. {sans[-1]}" if i % 2 == 0 else f"{i // 2 + 1}…{sans[-1]}"
        return f"move {n} · after {last}"
    return f"move {n} · {'White' if board.turn else 'Black'} to move"


def _castling_answer(board: chess.Board, side: str | None) -> str:
    """Castling is outside the Z3 model, so the rules engine answers it directly."""
    moves = [mv for mv in board.legal_moves if board.is_castling(mv)
             and (side is None or (side == "kingside") == board.is_kingside_castling(mv))]
    where = f" {side}" if side else ""
    if moves:
        return (f"Castling is outside the Z3 model, but python-chess says {' or '.join(board.san(mv) for mv in moves)} "
                f"is legal here.")
    return f"You cannot castle{where} right now, according to python-chess (castling is outside the Z3 model)."


def _board(fen: str) -> chess.Board:
    try:
        b = chess.Board(fen)
    except ValueError:
        raise HTTPException(400, "invalid FEN")
    if not b.is_valid():
        raise HTTPException(400, "invalid position")
    return b


def _validate_constraints(cons) -> list[dict]:
    if not isinstance(cons, list) or len(cons) > MAX_CONSTRAINTS:
        raise HTTPException(400, "constraints must be a list of at most 6 items")
    out = []
    for c in cons:
        if not isinstance(c, dict) or c.get("kind") not in truth.KINDS:
            raise HTTPException(400, f"unknown constraint {c!r}")
        clean = {"kind": c["kind"]}
        try:
            if "piece" in c and c["piece"] is not None:
                truth.piece_type(str(c["piece"]))
                clean["piece"] = str(c["piece"]).lower()
            if "square" in c and c["square"] is not None:
                truth.square(str(c["square"]))
                clean["square"] = str(c["square"]).lower()
            if "color" in c and c["color"] is not None:
                if c["color"] not in ("dark", "light"):
                    raise ValueError
                clean["color"] = c["color"]
            if "side" in c and c["side"] is not None:
                if c["side"] not in ("kingside", "queenside"):
                    raise ValueError
                clean["side"] = c["side"]
        except ValueError:
            raise HTTPException(400, f"bad parameter in {c!r}")
        needs = {"piece_is": "piece", "piece_not": "piece", "captures_piece": "piece", "attacks_piece": "piece",
                 "from_square": "square", "to_square": "square", "attacks_square": "square",
                 "piece_safe": "square", "defends": "square", "to_color": "color", "to_side": "side"}
        if c["kind"] in needs and needs[c["kind"]] not in clean:
            raise HTTPException(400, f"{c['kind']} needs {needs[c['kind']]}")
        out.append(clean)
    return out


def _validate_exclude(ex, board) -> list[str]:
    if ex is None:
        return []
    if not isinstance(ex, list):
        raise HTTPException(400, "exclude must be a list of moves")
    out = []
    for u in ex:
        try:
            mv = chess.Move.from_uci(str(u))
        except ValueError:
            raise HTTPException(400, f"bad move {u!r}")
        out.append(mv.uci())
    return out[:64]


def create_app(settings: Settings | None = None, gemini_client=None, engine: Engine | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    P = settings.prefix
    engine = engine or Engine(settings.stockfish_path)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        engine.close()

    app = FastAPI(title="QED Chess", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals["P"] = P
    # Content hash of the client bundle: the service worker is cache-first per URL, so a new
    # hash is what makes returning visitors fetch a changed stylesheet or script.
    static = HERE / "static"
    templates.env.globals["V"] = hashlib.sha256((static / "app.css").read_bytes() + (static / "app.js").read_bytes()).hexdigest()[:10]
    limiter = RateLimiter(settings.rate_limit_per_hour)
    daily = DailyCap(settings.daily_llm_cap)
    solve_limiter = RateLimiter(settings.solve_limit_per_hour)
    move_limiter = RateLimiter(settings.move_limit_per_hour)
    app.state.solve_slots = threading.BoundedSemaphore(settings.solve_queue)

    def throttle(limiter_, request: Request, what: str) -> None:
        if not limiter_.allow(client_ip(request)):
            raise HTTPException(429, f"Too many {what} from your address this hour; please slow down.")

    if gemini_client is None and not settings.llm_disabled:
        try:
            project = settings.vertex_project or default_project()
            gemini_client = GeminiClient(project, settings.vertex_location, settings.gemini_model)
        except Exception as e:
            log.warning("LLM disabled: %s", e)
            gemini_client = None
    if settings.llm_disabled:
        gemini_client = None

    app.state.engine = engine
    app.state.gemini = gemini_client
    r = APIRouter(prefix=P)
    configure_logging()

    def client_ip(request: Request) -> str:
        return request.headers.get("x-real-ip") or (request.client.host if request.client else "?")

    # ---------------------------------------------------------------- core ask
    def run_query(board: chess.Board, constraints: list[dict], exclude: list[str]) -> dict:
        res = solve_mod.solve(board, constraints, exclude=exclude)
        last = {"status": res.status, "constraints": constraints, "exclude": exclude,
                "move": res.move.uci() if res.move else None}
        return {
            "status": res.status if res.status in ("SAT", "UNSAT") else "ERROR",
            "answer": explain.answer_text(res, constraints, board),
            "scope": res.scope,
            "constraints": res.constraints_text,
            "model": {"move": res.move.uci(), "san": res.san} if res.move else None,
            "z3_code": res.z3_code,
            "unsat_core": res.unsat_core,
            "drop_one": res.drop_one,
            "highlights": explain.highlights(board, res, constraints),
            "query": {"constraints": constraints, "exclude": exclude},
            "bubbles": bubbles_mod.suggest(board, last),
            "message": res.message,
        }

    def unsupported(board, text):
        bubbles = bubbles_mod.suggest(board)
        examples = [f"“{b['label']}”" for b in bubbles if "query" in b][:2]
        hint = (" Try the suggestions below, such as " + " or ".join(examples) + ".") if examples else " Try one of the suggestions below."
        return {"status": "UNSUPPORTED",
                "answer": "I can only answer questions about a single move in this position: checks, captures, "
                          "forks, pins, safety, squares and pieces." + hint,
                "scope": solve_mod.SCOPE_BASE, "constraints": [], "model": None, "z3_code": "",
                "unsat_core": [], "drop_one": [], "highlights": {"squares": [], "arrows": []},
                "query": None, "bubbles": bubbles, "message": ""}

    def chat(board, reply):
        out = unsupported(board, None)
        out.update(status="CHAT", answer=reply)
        return out

    def answer(payload: AskIn, request: Request, meta: dict | None = None) -> dict:
        meta = meta if meta is not None else {}
        board = _board(payload.fen)
        query = payload.query
        text = (payload.text or "").strip()
        if query is not None:
            meta["source"] = "bubble"
        if query is None and text:
            if len(text) > MAX_INPUT * 4:
                raise HTTPException(400, "message too long")
            parsed = intent.parse(text, board)
            meta["source"] = "typed" if parsed is not None else "unparsed"
            if parsed is None and app.state.gemini is not None:
                ip = client_ip(request)
                if not limiter.allow(ip):
                    raise HTTPException(429, "Too many questions for the assistant from this address; try the suggested questions.")
                if not daily.allow():
                    meta["source"] = "capped"
                    return unsupported(board, text)
                meta["source"] = "llm"
                llm_t = Timer()
                try:
                    out = app.state.gemini.classify(text, payload.fen, payload.history)
                except GeminiError as e:
                    meta["llm_ms"] = llm_t.ms(); meta["llm_error"] = str(e)[:200]
                    log.warning("gemini failed: %s", e)
                    return unsupported(board, text)
                meta["llm_ms"] = llm_t.ms(); meta["llm_kind"] = out.get("kind")
                if out["kind"] == "query" and out["constraints"]:
                    parsed = {"constraints": out["constraints"]}
                else:
                    return chat(board, out.get("reply") or "I did not understand that.")
            if parsed is None:
                return unsupported(board, text)
            if "action" in parsed:
                meta["source"] = "action"; meta["action"] = parsed["action"]
                ctx = payload.context or {}
                if parsed["action"] == "another" and ctx.get("constraints") is not None:
                    prior = ctx.get("exclude") or []
                    if not isinstance(prior, list) or not isinstance(ctx.get("move"), (str, type(None))):
                        raise HTTPException(400, "bad context")
                    excl = list(prior) + ([ctx["move"]] if ctx.get("move") else [])
                    query = {"constraints": ctx["constraints"], "exclude": excl}
                elif parsed["action"] == "another":
                    return chat(board, "Ask a question first, then I can show another solution.")
                elif parsed["action"] == "castle":
                    return chat(board, _castling_answer(board, parsed.get("side")))
                else:
                    out = chat(board, "Open the last answer with the ▸ toggles: constraints, then the solver verdict, then the Z3 code.")
                    out["action"] = parsed["action"]
                    return out
            else:
                query = parsed
        if query is None:
            return unsupported(board, text)
        constraints = _validate_constraints(query.get("constraints", []))
        exclude = (_validate_exclude(query.get("exclude"), board) + _validate_exclude(payload.exclude, board))[:64]
        return run_query(board, constraints, exclude)

    def answer_logged(payload: AskIn, request: Request) -> dict:
        """answer() plus one `ask` event, whatever the outcome."""
        text = (payload.text or "").strip()
        with logged("ask", request, ip=ip_hash(client_ip(request)), fen=payload.fen, ply=len(payload.moves),
                    text=text[:300] or None) as meta:
            throttle(solve_limiter, request, "questions")
            if not app.state.solve_slots.acquire(blocking=False):
                raise HTTPException(503, "The solver is busy right now; try again in a moment.")
            try:
                data = answer(payload, request, meta)
            finally:
                app.state.solve_slots.release()
            meta.setdefault("status", data.get("status"))
            if data.get("model"):
                meta["move"] = data["model"]["move"]
            if data.get("query"):
                meta["constraints"] = data["query"].get("constraints")
                if data["query"].get("exclude"):
                    meta["exclude"] = data["query"]["exclude"]
            meta["answer"] = (data.get("answer") or "")[:160]
            return data

    # ---------------------------------------------------------------- routes
    @r.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {"presets": list(PRESETS)})

    @r.get("/healthz")
    def healthz():
        return {"ok": True, "z3_version": z3.get_version_string(), "stockfish": engine.available,
                "llm": app.state.gemini is not None}

    @r.post("/api/ask")
    def api_ask(payload: AskIn, request: Request):
        return answer_logged(payload, request)

    @r.post("/ask", response_class=HTMLResponse)
    def htmx_ask(request: Request, fen: str = Form(...), moves: str = Form("[]"), text: str = Form(None),
                 query: str = Form(None), context: str = Form(None), history: str = Form("[]"),
                 label: str = Form(None)):
        try:
            payload = AskIn(fen=fen, moves=json.loads(moves or "[]"), text=text,
                            query=json.loads(query) if query else None,
                            context=json.loads(context) if context else None,
                            history=json.loads(history or "[]"))
        except (ValueError, TypeError):
            raise HTTPException(400, "bad form payload")
        try:
            data = answer_logged(payload, request)
        except HTTPException as e:
            data = {"status": "ERROR", "answer": str(e.detail), "constraints": [], "model": None, "z3_code": "",
                    "scope": "", "unsat_core": [], "drop_one": [], "highlights": {"squares": [], "arrows": []},
                    "query": None, "bubbles": bubbles_mod.suggest(_board(fen)) if e.status_code != 400 or "FEN" not in str(e.detail) else [],
                    "message": ""}
        return templates.TemplateResponse(request, "answer.html",
                                          {"a": data, "fen": fen, "question": (text or label or _label_for(payload.query))[:300],
                                           "at": _at_label(fen, payload.moves)})

    @r.post("/bubbles", response_class=HTMLResponse)
    def htmx_bubbles(request: Request, fen: str = Form(...), moves: str = Form("[]")):
        board = _board(fen)
        return templates.TemplateResponse(request, "bubbles.html", {"bubbles": bubbles_mod.suggest(board), "oob": False})

    @r.get("/model.html", response_class=HTMLResponse)
    def model_html(request: Request):
        return templates.TemplateResponse(request, "model.html", {"sections": source.sections()})

    @r.get("/api/model")
    def model_json():
        return {"sections": source.sections()}

    @r.get("/api/legal")
    def legal(fen: str = Query(max_length=120)):
        board = _board(fen)
        return {"moves": [m.uci() for m in board.legal_moves], "in_check": board.is_check(),
                "game_over": board.is_game_over(), "result": board.result() if board.is_game_over() else None}

    def _engine_reply(board: chess.Board, preset: str, out: dict, meta: dict) -> None:
        """Let the engine answer on `board` (mutated) and record the reply in `out` and the event `meta`."""
        if not board.is_game_over():
            reply, kind = engine.best_move(board, preset)
            san = board.san(reply)
            board.push(reply)
            out["reply"] = {"uci": reply.uci(), "san": san, "fen": board.fen()}
            out["engine"] = kind
            out.update(game_over=board.is_game_over(), result=board.result() if board.is_game_over() else None,
                       in_check=board.is_check())
        meta.update(reply=out["reply"]["san"] if out["reply"] else None, engine=out["engine"],
                    game_over=out["game_over"], result=out["result"])

    @r.post("/api/play")
    def play(payload: PlayIn, request: Request):
        with logged("play", request, ip=ip_hash(client_ip(request)), fen=payload.fen, ply=len(payload.moves),
                    preset=payload.preset, move=payload.move) as meta:
            throttle(move_limiter, request, "moves")
            return _play(payload, meta)

    def _play(payload: PlayIn, meta: dict) -> dict:
        board = _board(payload.fen)
        try:
            mv = chess.Move.from_uci(payload.move)
        except ValueError:
            raise HTTPException(400, "bad move")
        if mv not in board.legal_moves:
            # auto-queen when the client omits the promotion piece
            if mv.promotion is None:
                mv = chess.Move(mv.from_square, mv.to_square, promotion=chess.QUEEN)
            if mv not in board.legal_moves:
                raise HTTPException(400, "illegal move")
        human_san = board.san(mv)
        meta["san"] = human_san
        board.push(mv)
        out = {"human": {"uci": mv.uci(), "san": human_san, "fen": board.fen()}, "reply": None,
               "game_over": board.is_game_over(), "result": board.result() if board.is_game_over() else None,
               "in_check": board.is_check(), "engine": None}
        _engine_reply(board, payload.preset, out, meta)
        return out

    @r.post("/api/engine")
    def engine_move(payload: EngineIn, request: Request):
        """The engine moves for whichever side is to move: used when the human switches colours."""
        with logged("engine", request, ip=ip_hash(client_ip(request)), fen=payload.fen, ply=len(payload.moves),
                    preset=payload.preset) as meta:
            throttle(move_limiter, request, "moves")
            board = _board(payload.fen)
            out = {"reply": None, "engine": None, "game_over": board.is_game_over(),
                   "result": board.result() if board.is_game_over() else None, "in_check": board.is_check()}
            _engine_reply(board, payload.preset, out, meta)
            return out

    @r.post("/api/replay")
    def replay(payload: ReplayIn, request: Request):
        with logged("replay", request, ply=len(payload.moves)) as meta:
            throttle(move_limiter, request, "moves")
            board = chess.Board()
            sans = []
            for u in payload.moves:
                try:
                    mv = chess.Move.from_uci(u)
                except ValueError:
                    raise HTTPException(400, f"bad move {u!r}")
                if mv not in board.legal_moves:
                    raise HTTPException(400, f"illegal move {u!r}")
                sans.append(board.san(mv))
                board.push(mv)
            meta["fen"] = board.fen()
            return {"fen": board.fen(), "sans": sans, "in_check": board.is_check(), "game_over": board.is_game_over(),
                    "result": board.result() if board.is_game_over() else None,
                    "legal": [m.uci() for m in board.legal_moves]}

    @r.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(HERE / "static" / "manifest.webmanifest", media_type="application/manifest+json")

    @r.get("/sw.js")
    def sw():
        return FileResponse(HERE / "static" / "sw.js", media_type="application/javascript",
                            headers={"Cache-Control": "no-cache"})

    app.include_router(r)
    app.mount(f"{P}/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.exception_handler(HTTPException)
    async def http_exc(request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def bad_request(request, exc: RequestValidationError):
        return JSONResponse({"error": "invalid request"}, status_code=400)

    return app


def _label_for(query) -> str:
    if not query:
        return ""
    return "; ".join(explain._clause(c) for c in query.get("constraints", [])) or "any legal move"


app = create_app()
