/* QED Chess client. Plain JS + htmx, no eval, no inline code (strict CSP). */
(function () {
  'use strict';
  htmx.config.allowEval = false;
  htmx.config.includeIndicatorStyles = false;

  const P = document.body.dataset.prefix || '/qed-chess';
  const START = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
  const FILES = 'abcdefgh';
  const REPLY_DELAY_MS = 650;     // minimum gap between your move landing and the engine's piece starting to move
  const REPLY_ANIM_MS = 280;
  const reducedMotion = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // Trace ids: one per browser (session) and one per game, sent as headers so server logs can follow a game.
  function rid() { const a = new Uint8Array(8); if (window.crypto && crypto.getRandomValues) crypto.getRandomValues(a); else for (let i = 0; i < a.length; i++) a[i] = Math.floor(Math.random() * 256); return Array.from(a, (b) => b.toString(16).padStart(2, '0')).join(''); }
  const state = { fen: START, moves: [], sans: [], preset: 'club', human: 'w', legal: [], gid: rid(),
                  selected: null, last: null, gameOver: false, result: null, inCheck: false, history: [], busy: false };
  const flipped = () => state.human === 'b';   // the board always faces the human

  // ---- storage (always guarded: private windows / blocked storage must not break the page)
  function load(key, fallback) { try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch (e) { return fallback; } }
  function save(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* ignore */ } }
  function saveState() { save('qed.state', { fen: state.fen, moves: state.moves, sans: state.sans, preset: state.preset, human: state.human, gid: state.gid, last: state.last, gameOver: state.gameOver, result: state.result, inCheck: state.inCheck, history: state.history }); }
  function saveChat() { save('qed.chat', $('#transcript').innerHTML); }

  const SID = load('qed.sid', null) || rid(); save('qed.sid', SID);
  const traceHeaders = () => ({ 'X-QED-Session': SID, 'X-QED-Game': state.gid || '' });

  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));

  // ---- FEN helpers
  function parseFen(fen) {
    const board = {};
    const rows = fen.split(' ')[0].split('/');
    rows.forEach((row, i) => {
      let f = 0;
      for (const ch of row) {
        if (/\d/.test(ch)) f += parseInt(ch, 10);
        else { board[FILES[f] + (8 - i)] = ch; f++; }
      }
    });
    return board;
  }
  function turn() { return state.fen.split(' ')[1] === 'w' ? 'w' : 'b'; }
  function moveNo() { return parseInt(state.fen.split(' ')[5] || '1', 10); }

  // ---- board rendering
  function squareOrder() {
    const sqs = [];
    for (let r = 7; r >= 0; r--) for (let f = 0; f < 8; f++) sqs.push(FILES[f] + (r + 1));
    return flipped() ? sqs.reverse() : sqs;
  }
  function renderBoard() {
    const board = parseFen(state.fen);
    const el = $('#board');
    el.textContent = '';
    const targets = state.selected ? state.legal.filter((m) => m.slice(0, 2) === state.selected).map((m) => m.slice(2, 4)) : [];
    squareOrder().forEach((sq, idx) => {
      const d = document.createElement('div');
      const file = FILES.indexOf(sq[0]), rank = parseInt(sq[1], 10) - 1;
      d.className = 'sq ' + (((file + rank) % 2 === 1) ? 'light' : 'dark');
      d.dataset.sq = sq;
      if (state.last && (state.last.slice(0, 2) === sq || state.last.slice(2, 4) === sq)) d.classList.add('last');
      if (state.selected === sq) d.classList.add('sel');
      if (targets.includes(sq)) { d.classList.add('target'); if (board[sq]) d.classList.add('occupied'); }
      const p = board[sq];
      if (p) { const s = document.createElement('span'); s.className = 'piece ' + (p === p.toUpperCase() ? 'w' : 'b') + p.toUpperCase(); d.appendChild(s); }
      const col = idx % 8, row = Math.floor(idx / 8);
      if (row === 7) { const c = document.createElement('span'); c.className = 'coord file'; c.textContent = sq[0]; d.appendChild(c); }
      if (col === 0) { const c = document.createElement('span'); c.className = 'coord rank'; c.textContent = sq[1]; d.appendChild(c); }
      el.appendChild(d);
    });
    applyHighlights();
    renderStatus();
  }
  function renderStatus() {
    const who = turn() === 'w' ? 'White' : 'Black';
    let s = 'Move ' + moveNo() + ' · ' + who + ' to move' + (turn() === state.human ? ' (you)' : ' (Stockfish)');
    if (state.gameOver) s = 'Game over · ' + (state.result || '') + (state.result === '1-0' ? ' White wins' : state.result === '0-1' ? ' Black wins' : ' draw');
    else if (state.inCheck) s += ' · check';
    $('#status').textContent = s;
    $('#btn-side').textContent = 'Play as ' + (state.human === 'w' ? 'Black' : 'White');
    const ml = $('#movelist');
    ml.textContent = '';
    state.sans.forEach((san, i) => {
      if (i % 2 === 0) ml.appendChild(document.createTextNode((i / 2 + 1) + '. '));
      const b = document.createElement(i === state.sans.length - 1 ? 'b' : 'span');
      b.textContent = san + ' ';
      ml.appendChild(b);
    });
    ml.scrollTop = ml.scrollHeight;
  }

  // ---- highlights from the latest answer
  let highlight = { squares: [], arrows: [] };
  function coords(sq) {
    let x = FILES.indexOf(sq[0]), y = 8 - parseInt(sq[1], 10);
    if (flipped()) { x = 7 - x; y = 7 - y; }
    return [x + 0.5, y + 0.5];
  }
  function applyHighlights() {
    $$('#board .sq').forEach((d) => d.classList.toggle('hl', highlight.squares.includes(d.dataset.sq)));
    const svg = $('#arrows');
    $$('line', svg).forEach((l) => l.remove());
    highlight.arrows.forEach(([a, b]) => {
      const [x1, y1] = coords(a), [x2, y2] = coords(b);
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', x1); line.setAttribute('y1', y1); line.setAttribute('x2', x2); line.setAttribute('y2', y2);
      line.setAttribute('marker-end', 'url(#arrowhead)');
      svg.appendChild(line);
    });
    const legend = $('#legend');
    legend.textContent = highlight.arrows.length ? '● ' + highlight.arrows.map((a) => a[0] + '→' + a[1]).join(', ') + ' highlighted' : '';
  }
  function setHighlightsFrom(msg) {
    if (!msg) { highlight = { squares: [], arrows: [] }; applyHighlights(); return; }
    highlight = {
      squares: (msg.dataset.squares || '').split(',').filter(Boolean),
      arrows: (msg.dataset.arrows || '').split(',').filter(Boolean).map((s) => s.split('-')),
    };
    applyHighlights();
  }

  // ---- server calls
  async function api(path, body, method) {
    const res = await fetch(P + path, { method: method || 'POST', headers: Object.assign({ 'Content-Type': 'application/json' }, traceHeaders()), body: body ? JSON.stringify(body) : undefined });
    if (!res.ok) { let msg = res.statusText; try { msg = (await res.json()).error || msg; } catch (e) { /* ignore */ } throw new Error(msg); }
    return res.json();
  }
  async function refreshLegal() {
    const j = await api('/api/legal?fen=' + encodeURIComponent(state.fen), null, 'GET');
    state.legal = j.moves; state.inCheck = j.in_check; state.gameOver = j.game_over; state.result = j.result;
  }
  function refreshBubbles() {
    htmx.ajax('POST', P + '/bubbles', { target: '#bubbles', swap: 'innerHTML', values: { fen: state.fen, moves: JSON.stringify(state.moves) } });
  }
  function clearHighlightsAfterMove() { highlight = { squares: [], arrows: [] }; }

  // Slide the piece on `uci` from its square to the target; resolves when the slide is done.
  function animateMove(uci) {
    const from = $('#board .sq[data-sq="' + uci.slice(0, 2) + '"]'), to = $('#board .sq[data-sq="' + uci.slice(2, 4) + '"]');
    const piece = from && $('.piece', from);
    if (!piece || !to || reducedMotion() || typeof piece.animate !== 'function') return Promise.resolve();
    const a = from.getBoundingClientRect(), b = to.getBoundingClientRect();
    from.classList.add('lift');
    return new Promise((resolve) => {
      const anim = piece.animate([{ transform: 'translate(0,0)' }, { transform: 'translate(' + (b.left - a.left) + 'px,' + (b.top - a.top) + 'px)' }],
                                 { duration: REPLY_ANIM_MS, easing: 'ease-in-out', fill: 'forwards' });
      let done = false; const finish = () => { if (!done) { done = true; resolve(); } };
      anim.onfinish = finish; anim.oncancel = finish; setTimeout(finish, REPLY_ANIM_MS + 200);
    });
  }
  function commit(m) { state.moves.push(m.uci); state.sans.push(m.san); state.fen = m.fen; state.last = m.uci; }
  // Your piece lands the moment you click; the server round trip then confirms it (renderBoard() undoes a rejected preview).
  function previewMove(uci) {
    const from = $('#board .sq[data-sq="' + uci.slice(0, 2) + '"]'), to = $('#board .sq[data-sq="' + uci.slice(2, 4) + '"]');
    const piece = from && $('.piece', from); if (!piece || !to) return;
    const taken = $('.piece', to); if (taken) taken.remove();
    to.appendChild(piece);
    $$('#board .sq').forEach((d) => { d.classList.remove('last', 'sel', 'target', 'occupied'); });
    from.classList.add('last'); to.classList.add('last');
  }

  async function play(uci) {
    if (state.busy) return;
    state.busy = true;
    if (isPhone()) setSheet(false);   // you want to see the reply land
    const t0 = Date.now();
    try {
      // 1. your move lands at once
      previewMove(uci); clearHighlightsAfterMove(); applyHighlights();
      const j = await api('/api/play', { fen: state.fen, moves: state.moves, move: uci, preset: state.preset });
      commit(j.human); state.selected = null;
      state.inCheck = j.reply ? false : j.in_check; state.gameOver = j.reply ? false : j.game_over; state.result = j.reply ? null : j.result;
      renderBoard();
      // 2. the engine answers after a beat (never sooner than REPLY_DELAY_MS after your click), and its piece slides
      if (j.reply) {
        await sleep(Math.max(150, REPLY_DELAY_MS - (Date.now() - t0)));
        await animateMove(j.reply.uci);
        commit(j.reply);
      }
      state.gameOver = j.game_over; state.result = j.result; state.inCheck = j.in_check;
      await refreshLegal();
      renderBoard(); saveState(); refreshBubbles();
    } catch (e) { renderBoard(); $('#legend').textContent = 'Move rejected: ' + e.message; }
    finally { state.busy = false; }
  }
  async function replay() {
    const j = await api('/api/replay', { moves: state.moves });
    state.fen = j.fen; state.sans = j.sans; state.legal = j.legal; state.inCheck = j.in_check; state.gameOver = j.game_over; state.result = j.result;
    state.last = state.moves.length ? state.moves[state.moves.length - 1] : null; state.selected = null;
    clearHighlightsAfterMove(); renderBoard(); saveState(); refreshBubbles();
  }
  // Stockfish moves for whichever side is to move: after you switch colours, on a reload mid-reply, or to open as White.
  async function engineMove() {
    if (state.busy || state.gameOver || turn() === state.human) return;
    state.busy = true;
    const t0 = Date.now();
    try {
      const j = await api('/api/engine', { fen: state.fen, moves: state.moves, preset: state.preset });
      if (j.reply) {
        await sleep(Math.max(150, REPLY_DELAY_MS - (Date.now() - t0)));
        await animateMove(j.reply.uci);
        commit(j.reply);
      }
      state.gameOver = j.game_over; state.result = j.result; state.inCheck = j.in_check; state.selected = null;
      await refreshLegal();
      renderBoard(); saveState(); refreshBubbles();
    } catch (e) { renderBoard(); $('#legend').textContent = 'Engine move failed: ' + e.message; }
    finally { state.busy = false; }
  }
  function engineTurn() { return !state.gameOver && turn() !== state.human; }

  // ---- board interaction
  $('#board').addEventListener('click', (e) => {
    const d = e.target.closest('.sq'); if (!d || state.gameOver || state.busy) return;
    const sq = d.dataset.sq;
    if (state.selected && state.legal.some((m) => m.slice(0, 4) === state.selected + sq)) { play(state.selected + sq); return; }
    const board = parseFen(state.fen);
    const p = board[sq];
    const mine = p && ((turn() === 'w') === (p === p.toUpperCase()));
    state.selected = (mine && state.selected !== sq) ? sq : null;
    renderBoard();
  });

  // ---- controls
  $('#btn-undo').addEventListener('click', async () => {
    if (!state.moves.length || state.busy) return;
    const humanHasMoved = state.moves.some((_, i) => (i % 2 === 0 ? 'w' : 'b') === state.human);
    if (!humanHasMoved) return;   // only the engine has moved: taking that back would just make it move again
    state.moves.pop();
    if (state.moves.length && turn() === state.human) state.moves.pop();   // it was your turn, so the engine's reply goes back too
    await replay();
    if (engineTurn()) engineMove();
  });
  $('#btn-side').addEventListener('click', async () => {
    if (state.busy) return;
    state.human = state.human === 'w' ? 'b' : 'w';   // same position, other colour; the board turns to face you
    state.selected = null; renderBoard(); saveState();
    if (engineTurn()) engineMove();
  });
  $('#btn-new').addEventListener('click', newGame);
  $('#btn-pgn').addEventListener('click', async () => {
    const sf = 'Stockfish (' + state.preset + ')';
    let pgn = '[Event "QED Chess"]\n[Site "app.gyatso.me/qed-chess"]\n[White "' + (state.human === 'w' ? 'You' : sf) + '"]\n[Black "' + (state.human === 'b' ? 'You' : sf) + '"]\n[Result "' + (state.result || '*') + '"]\n\n';
    state.sans.forEach((san, i) => { if (i % 2 === 0) pgn += (i / 2 + 1) + '. '; pgn += san + ' '; });
    pgn += state.result || '*';
    try { await navigator.clipboard.writeText(pgn); $('#legend').textContent = 'PGN copied.'; }
    catch (e) { window.prompt('Copy the PGN:', pgn); }
  });
  $('#preset').addEventListener('change', (e) => { state.preset = e.target.value; saveState(); });
  function newGame() {
    state.fen = START; state.moves = []; state.sans = []; state.last = null; state.selected = null; state.gid = rid();
    state.gameOver = false; state.result = null; state.inCheck = false; state.history = [];
    $('#transcript').textContent = ''; saveChat(); save('qed.banner', false); setBanner(false);
    clearHighlightsAfterMove();
    refreshLegal().then(() => { renderBoard(); saveState(); refreshBubbles(); if (engineTurn()) engineMove(); });
  }

  // ---- phones: the chat is a bottom sheet over the board
  const isPhone = () => window.matchMedia('(max-width: 820px)').matches;
  const pointerDevice = () => window.matchMedia('(hover: hover)').matches;
  const sheet = $('.pane-chat'), handle = $('#sheet-handle');
  function setSheet(open) {
    sheet.classList.toggle('open', open);
    handle.setAttribute('aria-expanded', String(open));
    if (open) scrollToLatest();
  }
  function updateSheetCount() { const n = $$('#transcript .msg.z3').length; $('#sheet-count').textContent = n ? n + (n === 1 ? ' answer' : ' answers') : ''; }
  handle.addEventListener('click', () => setSheet(!sheet.classList.contains('open')));
  if ('ResizeObserver' in window) {   // keep the board column clear of the docked sheet (CSSOM, so it passes the strict CSP)
    new ResizeObserver(() => { if (!sheet.classList.contains('open')) document.documentElement.style.setProperty('--dock', sheet.offsetHeight + 'px'); }).observe(sheet);
  }

  // ---- banner + dialogs
  function setBanner(hidden) { $('#banner').classList.toggle('hidden', hidden); document.body.classList.toggle('banner-off', hidden); }
  setBanner(load('qed.banner', false));
  $('#banner-dismiss').addEventListener('click', () => { setBanner(true); save('qed.banner', true); });
  $$('[data-open]').forEach((b) => b.addEventListener('click', () => $('#' + b.dataset.open).showModal()));   // the model button's hx-get fills the dialog body
  $$('[data-close]').forEach((b) => b.addEventListener('click', () => $('#' + b.dataset.close).close()));

  // ---- htmx wiring: every ask carries the position and the last answer's context
  function lastAnswer() { const all = $$('#transcript .msg.z3[data-query]'); return all[all.length - 1] || null; }
  document.body.addEventListener('htmx:configRequest', (e) => {
    Object.assign(e.detail.headers, traceHeaders());
    const p = e.detail.parameters;
    p.fen = state.fen; p.moves = JSON.stringify(state.moves);
    const last = lastAnswer();
    if (last) { const q = JSON.parse(last.dataset.query); p.context = JSON.stringify({ constraints: q.constraints, exclude: q.exclude, move: last.dataset.move || null }); }
    p.history = JSON.stringify(state.history.slice(-4));
    if (p.text) { state.history.push(String(p.text).slice(0, 120)); state.history = state.history.slice(-8); saveState(); }
  });
  document.body.addEventListener('htmx:afterSwap', (e) => {
    if (e.detail.target.id === 'transcript') {
      const msgs = $$('#transcript .msg.z3'); const last = msgs[msgs.length - 1];
      setHighlightsFrom(last && last.dataset.status === 'SAT' ? last : null);
      $('#ask-form').reset(); saveChat(); updateSheetCount();
      if (isPhone()) setSheet(true); else scrollToLatest();
      if (pointerDevice()) setTimeout(() => $('#ask-text').focus(), 0);   // htmx re-enables the box after the swap; a phone must not pop its keyboard
    }
  });
  document.body.addEventListener('htmx:responseError', (e) => {
    const d = document.createElement('div'); d.className = 'msg z3 status-error';
    d.textContent = e.detail.xhr.status === 429 ? 'The assistant is rate-limited for your address; the suggested questions still work.' : 'Something went wrong (' + e.detail.xhr.status + ').';
    $('#transcript').appendChild(d); saveChat();
  });
  // Delegated from the body: every answer swaps #bubbles out-of-band, so a listener on the element itself would die with it.
  document.body.addEventListener('click', (e) => {
    const b = e.target.closest('.bubble.action'); if (!b || b.disabled) return;
    const msgs = $$('#transcript .msg.z3'); const last = msgs[msgs.length - 1];
    if (b.dataset.action === 'new_game') newGame();
    else if (b.dataset.action === 'why' && last) { const d = $('.step-2', last); if (d) { d.open = true; d.scrollIntoView({ block: 'nearest' }); } }
    else if (b.dataset.action === 'code' && last) { const d = $('.step-3', last); if (d) { d.open = true; d.scrollIntoView({ block: 'nearest' }); } }
  });
  $('#transcript').addEventListener('toggle', (e) => {
    saveChat();
    if (e.target.open) e.target.scrollIntoView({ block: 'nearest' });   // an opened step must not stay hidden below the fold
  }, true);
  $('#transcript').addEventListener('click', (e) => {
    if (e.target.closest('[data-open-model]')) $('#model').showModal();   // the hx-get on the button fills the dialog body
  });
  // Show the newest exchange from its question downwards; short answers still end flush with the bottom.
  function scrollToLatest() {
    const t = $('#transcript'); const msgs = $$('.msg', t); const last = msgs[msgs.length - 1]; if (!last) return;
    const start = last.previousElementSibling && last.previousElementSibling.classList.contains('you') ? last.previousElementSibling : last;
    t.scrollTop = Math.min(start.offsetTop - 4, t.scrollHeight - t.clientHeight);
  }

  // ---- boot
  (async function boot() {
    const saved = load('qed.state', null);
    if (saved && saved.fen) { Object.assign(state, saved); if (saved.human !== 'w' && saved.human !== 'b') state.human = 'w'; delete state.flipped; }   // a saved state without gid keeps the fresh one from the initial state
    $('#preset').value = state.preset;
    const chat = load('qed.chat', '');
    if (chat) { $('#transcript').innerHTML = chat; htmx.process($('#transcript')); }
    try { await refreshLegal(); } catch (e) { state.legal = []; }
    renderBoard();
    const msgs = $$('#transcript .msg.z3'); const lastMsg = msgs[msgs.length - 1];
    setHighlightsFrom(lastMsg && lastMsg.dataset.status === 'SAT' && lastMsg.dataset.fen === state.fen ? lastMsg : null);   // no stale arrows on a changed position
    scrollToLatest(); updateSheetCount();
    if (engineTurn() && state.legal.length) engineMove();   // e.g. the page was closed while Stockfish was replying
    if ('serviceWorker' in navigator) navigator.serviceWorker.register(P + '/sw.js').catch(() => {});
  })();
  window.addEventListener('resize', applyHighlights);
  // bubbles load via hx-trigger="load"; configRequest adds the position to that request too
})();
