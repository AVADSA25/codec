/* CODEC service worker (UI phase 2, P2.14; docs/P2.14-DESIGN.md).

   Caches only the static shell: the stylesheet and scripts under /static,
   the Plex fonts, the logo and the icons. It never stores a page or an API
   response, and never anything tied to the session cookie:
   - GET /static/*  cache first, refreshed in the background (the ?v= stamps
     make a changed file a new URL);
   - page navigations  always the network; when the network fails, or the
     tunnel answers with a gateway error because the Mac is off, a calm
     "Mac not reachable" page;
   - everything else (/api included)  not handled: no respondWith, so the
     request reaches the network exactly as without a worker. */
'use strict';

var VERSION = 'codec-shell-v1';
var PRECACHE = [
  '/static/codec.css',
  '/static/codec-shell.js',
  '/static/codec-md.js',
  '/static/logo.svg',
  '/static/icons/icon-192.png',
  '/static/fonts/IBMPlexSans-Regular.woff2',
  '/static/fonts/IBMPlexSans-Medium.woff2',
  '/static/fonts/IBMPlexSans-SemiBold.woff2',
  '/static/fonts/IBMPlexSans-Bold.woff2',
  '/static/fonts/IBMPlexMono-Regular.woff2',
  '/static/fonts/IBMPlexMono-Medium.woff2'
];
// Cloudflare's answers when the origin (the Mac) is down or unreachable.
var GATEWAY = [502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527, 530];

var OFFLINE_HTML = '<!doctype html><html lang="en"><head><meta charset="utf-8">' +
  '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">' +
  '<title>CODEC: Mac not reachable</title><link rel="stylesheet" href="/static/codec.css">' +
  '<script>try{var t=localStorage.getItem("codec-theme");if(t!=="light"&&t!=="dark")' +
  't=matchMedia("(prefers-color-scheme: light)").matches?"light":"dark";' +
  'document.documentElement.setAttribute("data-theme",t)}catch(e){}</script>' +
  '<style>body{margin:0;min-height:100vh;min-height:100dvh;display:flex;align-items:center;justify-content:center;' +
  'box-sizing:border-box;padding:24px;background:var(--bg);color:var(--text);font-family:var(--font-sans)}' +
  '.box{max-width:360px;text-align:center}.box img{width:48px;height:48px}' +
  'h1{margin:16px 0 8px;font-size:var(--fs-20);font-weight:600;line-height:var(--lh-20)}' +
  'p{margin:0 0 20px;font-size:var(--fs-14);line-height:1.5;color:var(--text-muted)}' +
  'button{height:40px;padding:0 20px;border:0;border-radius:999px;background:var(--accent);color:var(--on-accent);' +
  'font:500 var(--fs-14)/1 var(--font-sans);cursor:pointer}</style></head>' +
  '<body><main class="box" role="status"><img src="/static/logo.svg" alt="">' +
  '<h1>Your Mac is not reachable</h1>' +
  '<p>CODEC runs on your Mac. It may be asleep, switched off or offline. This page tries again by itself.</p>' +
  '<button type="button" onclick="location.reload()">Try again</button></main>' +
  '<script>addEventListener("online",function(){location.reload()});' +
  'setInterval(function(){fetch("/manifest.json",{cache:"no-store"}).then(function(r){if(r.ok)location.reload()})' +
  '.catch(function(){})},30000)</script></body></html>';

function offlinePage() {
  return new Response(OFFLINE_HTML, {
    status: 503,
    headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' }
  });
}

self.addEventListener('install', function (event) {
  event.waitUntil(caches.open(VERSION)
    .then(function (cache) { return cache.addAll(PRECACHE); })
    .then(function () { return self.skipWaiting(); }));
});

self.addEventListener('activate', function (event) {
  event.waitUntil(caches.keys()
    .then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k.indexOf('codec-shell-') === 0 && k !== VERSION; })
        .map(function (k) { return caches.delete(k); }));
    })
    .then(function () { return self.clients.claim(); }));
});

self.addEventListener('fetch', function (event) {
  var req = event.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (req.mode === 'navigate') {
    event.respondWith(fetch(req).then(function (res) {
      return GATEWAY.indexOf(res.status) >= 0 ? offlinePage() : res;
    }, offlinePage));
    return;
  }

  if (url.pathname.indexOf('/static/') === 0) {
    event.respondWith(caches.open(VERSION).then(function (cache) {
      return cache.match(req).then(function (hit) {
        var fresh = fetch(req).then(function (res) {
          if (res.ok) cache.put(req, res.clone());
          return res;
        });
        if (hit) {
          event.waitUntil(fresh.catch(function () { /* offline: the cached copy stands */ }));
          return hit;
        }
        return fresh;
      });
    }));
  }
  // Anything else, /api included: not handled here.
});
