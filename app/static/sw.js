/* QED Chess service worker: cache-first for static assets, network for everything else. */
const CACHE = 'qed-chess-v2';   // bump whenever app.css/app.js change shape
const PRE = ['/qed-chess/static/app.css', '/qed-chess/static/app.js', '/qed-chess/static/vendor/htmx.min.js',
  '/qed-chess/static/icons/icon.svg', '/qed-chess/manifest.webmanifest',
  ...['wK', 'wQ', 'wR', 'wB', 'wN', 'wP', 'bK', 'bQ', 'bR', 'bB', 'bN', 'bP'].map((p) => '/qed-chess/static/pieces/' + p + '.svg')];
self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRE)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/qed-chess/static/')) {
    e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy));
      return res;
    })));
  }
});
