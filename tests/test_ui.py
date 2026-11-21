"""UI contract tests: things the browser needs from the server for the board,
theme and chat to work. Behaviour inside the browser is checked by hand with
puppeteer at desktop and phone widths; these pin what the server must ship for it."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_api import ITALIAN, START, make_client

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
PIECES = ["wK", "wQ", "wR", "wB", "wN", "wP", "bK", "bQ", "bR", "bB", "bN", "bP"]


def _css_var(css, name):
    m = re.search(r"--%s:\s*(#[0-9a-fA-F]{6})" % re.escape(name), css)
    assert m, f"--{name} not defined as a 6-digit hex colour"
    return m.group(1).lower()


def _luminance(hex6):
    r, g, b = (int(hex6[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_piece_images_are_served_for_every_piece():
    c = make_client()
    for p in PIECES:
        r = c.get(f"/qed-chess/static/pieces/{p}.svg")
        assert r.status_code == 200, p
        assert "image/svg+xml" in r.headers["content-type"], p
        assert "<svg" in r.text and "<script" not in r.text, p


def test_css_draws_pieces_from_the_svg_set():
    css = make_client().get("/qed-chess/static/app.css").text
    for p in PIECES:
        assert f"pieces/{p}.svg" in css, p
    # pieces fill the square instead of being a text glyph
    assert re.search(r"\.piece\s*\{[^}]*background-size", css)


def test_default_theme_is_light_and_consistent():
    c = make_client()
    css = c.get("/qed-chess/static/app.css").text
    bg = _css_var(css, "bg")
    assert _luminance(bg) > 0.85, f"page background {bg} is not light"
    assert _luminance(_css_var(css, "text")) < 0.25
    assert _luminance(_css_var(css, "board-dark")) < _luminance(_css_var(css, "board-light"))
    html = c.get("/qed-chess/").text
    assert f'name="theme-color" content="{bg}"' in html
    manifest = json.loads(c.get("/qed-chess/manifest.webmanifest").text)
    assert manifest["theme_color"] == bg and manifest["background_color"] == bg
    assert "prefers-color-scheme: dark" not in css   # one theme, no auto dark mode


def test_answer_links_to_the_base_model_from_the_code_step():
    r = make_client().post("/qed-chess/ask", data={"fen": ITALIAN, "moves": "[]", "query": json.dumps({"constraints": [{"kind": "gives_check"}]})})
    assert r.status_code == 200
    step = r.text.split('class="step step-3"', 1)[1]
    assert "data-open-model" in step and 'hx-get="/qed-chess/model.html"' in step
    assert "s.add(" in step
    assert "header: View the model" not in r.text


def test_service_worker_precaches_pieces_and_is_versioned_past_v1():
    sw = make_client().get("/qed-chess/sw.js").text
    assert "qed-chess-v1'" not in sw, "bump the cache name so clients drop the old theme"
    assert "/qed-chess/static/pieces/" in sw
    for p in PIECES:
        assert f"'{p}'" in sw, p


def test_transcript_can_scroll_independently_of_the_page():
    css = make_client().get("/qed-chess/static/app.css").text
    transcript = re.search(r"\.transcript\s*\{([^}]*)\}", css).group(1)
    assert "min-height: 0" in transcript and "overscroll-behavior: contain" in transcript
    msg = re.search(r"\.msg\s*\{([^}]*)\}", css).group(1)
    assert "flex-shrink: 0" in msg
    assert "100dvh" in css


def test_engine_reply_is_animated_after_a_pause():
    js = (STATIC / "app.js").read_text()
    assert "REPLY_DELAY_MS" in js and "animateMove" in js
    assert "prefers-reduced-motion" in js
    assert "KeyboardEvent" not in js   # sanity: no accidental dead code


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_client_js_parses():
    for f in ("app.js", "sw.js"):
        subprocess.run(["node", "--check", str(STATIC / f)], check=True)


def test_static_assets_are_versioned_by_content_hash():
    """The service worker is cache-first, so a changed stylesheet must get a new URL."""
    import hashlib
    html = make_client().get("/qed-chess/").text
    digest = hashlib.sha256((STATIC / "app.css").read_bytes() + (STATIC / "app.js").read_bytes()).hexdigest()[:10]
    assert f'href="/qed-chess/static/app.css?v={digest}"' in html
    assert f'src="/qed-chess/static/app.js?v={digest}"' in html


def test_answer_records_the_position_it_was_asked_about():
    """Restored chats only re-highlight the board when the position still matches."""
    r = make_client().post("/qed-chess/ask", data={"fen": ITALIAN, "moves": "[]", "query": json.dumps({"constraints": [{"kind": "gives_check"}]})})
    assert f'data-fen="{ITALIAN}"' in r.text


# ---- 2026-11-21 pass: action bubbles after a swap, banner links, move indicator, solving state, play as either colour

def test_action_bubbles_keep_working_after_an_answer_swaps_them_in():
    """The answer replaces #bubbles out-of-band, so a listener bound to the old element dies.
    The handler for data-action bubbles must be delegated from a stable ancestor."""
    js = (STATIC / "app.js").read_text()
    assert "$('#bubbles').addEventListener" not in js
    assert re.search(r"document\.body\.addEventListener\('click'", js) or re.search(r"\$\('\.pane-chat'\)\.addEventListener\('click'", js)
    assert "data-action" in js or "dataset.action" in js


def test_banner_holds_the_model_and_how_links_once():
    html = make_client().get("/qed-chess/").text
    banner = re.search(r'<aside class="banner"[^>]*>(.*?)</aside>', html, re.S).group(1)
    assert 'id="btn-model"' in banner and 'hx-get="/qed-chess/model.html"' in banner
    assert banner.count("How it works") == 1
    header = re.search(r"<header(.*?)</header>", html, re.S).group(1)
    assert "How it works" not in header and "View the model" not in header
    # a dismissed banner must not strand the links: the header gets a compact copy only then
    css = make_client().get("/qed-chess/static/app.css").text
    assert re.search(r"\.top-actions\s*\{[^}]*display:\s*none", css)
    assert re.search(r"body\.banner-off\s+\.top-actions\s*\{[^}]*display:\s*flex", css)
    assert 'class="top-actions-after"' in html or 'id="top-actions"' in html


def test_answer_names_the_move_it_belongs_to():
    c = make_client()
    fen = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
    r = c.post("/qed-chess/ask", data={"fen": fen, "moves": json.dumps(["e2e4", "e7e5"]), "text": "can I give check"})
    assert r.status_code == 200
    assert '<span class="at">' in r.text
    assert "move 2 · after 1…e5" in r.text
    r = c.post("/qed-chess/ask", data={"fen": START, "moves": "[]", "query": json.dumps({"constraints": [{"kind": "gives_check"}]})})
    assert "move 1 · start" in r.text
    # a bubble asked after White's move but before the reply says so
    fen_b = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
    r = c.post("/qed-chess/ask", data={"fen": fen_b, "moves": json.dumps(["e2e4"]), "query": json.dumps({"constraints": [{"kind": "gives_check"}]})})
    assert "move 1 · after 1. e4" in r.text
    # moves that do not replay to the FEN fall back to the FEN alone
    r = c.post("/qed-chess/ask", data={"fen": fen, "moves": json.dumps(["d2d4"]), "query": json.dumps({"constraints": [{"kind": "gives_check"}]})})
    assert r.status_code == 200 and "move 2 · White to move" in r.text


def test_solving_state_disables_input_and_animates():
    c = make_client()
    html = c.get("/qed-chess/").text
    m = re.search(r'hx-disabled-elt="([^"]*)"', html)
    assert m, "the chat pane must disable its controls while a question is in flight"
    assert "#ask-text" in m.group(1) and ".bubble" in m.group(1)
    assert 'id="thinking"' in html and "Solving…" not in html
    css = c.get("/qed-chess/static/app.css").text
    assert re.search(r"\.ask input:disabled", css) and re.search(r"\.bubble:disabled", css)
    assert "@keyframes" in css and re.search(r"\.thinking\.htmx-request", css)
    reduced = css.split("prefers-reduced-motion", 1)[1]
    assert "animation" in reduced


def test_play_as_either_colour_replaces_flip():
    html = make_client().get("/qed-chess/").text
    assert 'id="btn-flip"' not in html and 'id="btn-side"' in html
    js = (STATIC / "app.js").read_text()
    assert "state.human" in js and "/api/engine" in js
    assert "turn() === 'w') state.moves.pop()" not in js   # undo must not assume the human is White


def test_banner_explains_z3_for_newcomers():
    html = make_client().get("/qed-chess/").text
    banner = re.search(r'<aside class="banner"[^>]*>(.*?)</aside>', html, re.S).group(1)
    assert "Z3" in banner and "Microsoft" in banner
    assert "prove" in banner.lower()
    assert len(re.sub(r"<[^>]+>", "", banner).split()) < 70   # a line or two, not a manual


def test_chat_pane_matches_the_board_column_height():
    """The chat column must end where the controls under the board end, not at the viewport edge."""
    css = make_client().get("/qed-chess/static/app.css").text
    layout = re.search(r"\.layout\s*\{([^}]*)\}", css).group(1)
    assert "grid-template-rows: auto" in layout
    assert "overflow: auto" in layout or "overflow-y: auto" in layout   # short viewports scroll instead of clipping the board
    assert re.search(r"\.pane-chat\s*\{[^}]*contain: size", css), "the chat must not push the row taller than the board column"


# ---- 2026-11-21, second pass: focus, phone banner, undo guard, bottom-sheet chat on phones

def test_input_regains_focus_on_pointer_devices_only():
    js = (STATIC / "app.js").read_text()
    assert "hover: hover" in js and "#ask-text').focus()" in js


def test_phone_banner_hides_the_explainer_line():
    css = make_client().get("/qed-chess/static/app.css").text
    phone = re.search(r"@media \(max-width: 480px\)\s*\{(.*?)\n\}", css, re.S)
    assert phone and re.search(r"\.banner-sub\s*\{[^}]*display:\s*none", phone.group(1))


def test_undo_is_a_no_op_until_the_human_has_moved():
    js = (STATIC / "app.js").read_text()
    undo = js.split("$('#btn-undo')", 1)[1].split("});", 1)[0]
    assert "state.human" in undo and "return" in undo
    assert "humanHasMoved" in undo


def test_phone_chat_is_a_bottom_sheet_not_a_tab():
    c = make_client()
    html = c.get("/qed-chess/").text
    assert 'class="tabs"' not in html and 'data-tab=' not in html
    assert 'id="sheet-handle"' in html and 'aria-expanded="false"' in html and 'aria-controls="transcript"' in html
    css = c.get("/qed-chess/static/app.css").text
    mobile = re.search(r"@media \(max-width: 820px\)\s*\{(.*?)\n\}", css, re.S).group(1)
    assert re.search(r"\.pane-chat\s*\{[^}]*position:\s*fixed", mobile)
    assert re.search(r"\.pane-chat\s*\{[^}]*bottom:\s*0", mobile)
    assert ".pane-chat.open" in mobile and "--dock" in mobile        # the board column is padded by the docked sheet's height
    assert re.search(r"body\s*\{[^}]*height:\s*auto", mobile), "a short phone must scroll the page to reach the controls above the sheet"
    assert re.search(r"\.sheet-handle\s*\{[^}]*display:\s*none", css)   # desktop never shows the handle
    js = (STATIC / "app.js").read_text()
    assert "ResizeObserver" in js and "'--dock'" in js and "setSheet(" in js
    assert "data-tab" not in js and ".tab'" not in js
