# Vendored third-party assets

Pinned, minified, committed as static files. No build step, no npm.

| File | Package | Version | Source | SHA-256 |
|------|---------|---------|--------|---------|
| htmx.min.js | htmx.org | 2.0.4 | https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js | e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447 |

Alpine.js is intentionally not vendored: the UI state (board, tabs, overlay) is
a small amount of plain JS in `app/static/app.js`, and the strict CSP
(`script-src 'self'`, no eval) favours that over Alpine's expression evaluator.
htmx runs with `htmx.config.allowEval = false` (set in `app.js`).

## Chess piece set (`app/static/pieces/*.svg`)

"cburnett" pieces by Colin M.L. Burnett, as shipped by lichess
(`public/piece/cburnett` in https://github.com/lichess-org/lila, fetched
2026-11-02 from the `master` branch). Licence per lila's COPYING.md: GPLv2+;
the Wikimedia originals are additionally offered under CC BY-SA 3.0 and the
BSD licence. Plain SVG, no scripts; served as-is under `img-src 'self'`.

| File | SHA-256 |
|------|---------|
| bB.svg | 48ff9dba84dae861b1a81ac58f4647e05cee8a827acad1618bf11beaedcce60d |
| bK.svg | 2dc3caba39a67a707ebc27f4e41b727dd5252f4f49da695fc15cc2589dd92a47 |
| bN.svg | 72e1565fbc9024015dcb6ee28d918c876523119f6f552c0a40d3118c13a67e13 |
| bP.svg | 93e11e46deceff0768211a28df3e39c6d3427aa172bdb5e3928b025bd2858587 |
| bQ.svg | 2d0162d1fe776ac5a4d307d4f6e5960804273edc0d3743f1c97a158f5a64c4b1 |
| bR.svg | 5928b86919ae815f5d35dea8a25f5a84c3f8839468a137e429d42f618185cbcd |
| wB.svg | 8bf4f38cc170686775f81158934301942b07e3f1162e76c6305e76d66aaa0910 |
| wK.svg | 09837f1f99dae2d05c2a0bfb5ce0fb9f37a95b124de44f428a08ae18d7b7c557 |
| wN.svg | 43d8bed9cd40836335072a20f52d30237e6e2cbe304ffb13e258fa1d576c156a |
| wP.svg | a8bd79f344ea69a577cdd7b11cbab7bbebfc157cce01c6d29b8f2edf1c44068e |
| wQ.svg | 9c0a960b360e08cea48eedab54e852107145de289b81a82fe27942045e2ab665 |
| wR.svg | 05746153e11de3ff4a43d6725f704e226e5d5b49c6ff170ed598d3e9883f2cd5 |
