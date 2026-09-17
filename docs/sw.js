// sw.js - Minimal service worker. Only caches the static app shell (not
// Supabase data) so the page installs cleanly as a PWA and reloads a
// touch faster; it does not provide real offline data access.
const CACHE = "pawmodoro-shell-v9";
const SHELL = ["./", "index.html", "style.css?v=9", "app.js?v=9", "manifest.json"];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== location.origin) return; // never intercept Supabase/API calls
  // Network-first: always prefer a live copy of the shell when there's a
  // connection, only falling back to the cached copy if the network fails
  // outright (genuinely offline). A cache-first strategy here previously
  // meant a deployed fix could sit unused on a phone indefinitely — this
  // app needs network for Supabase anyway, so there's no offline-first
  // case worth trading staleness for.
  event.respondWith(
    fetch(event.request)
      .then(response => {
        const copy = response.clone();
        caches.open(CACHE).then(cache => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
