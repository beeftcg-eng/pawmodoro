// sw.js - Minimal service worker. Only caches the static app shell (not
// Supabase data) so the page installs cleanly as a PWA and reloads a
// touch faster; it does not provide real offline data access.
const CACHE = "pawmodoro-shell-v3";
const SHELL = ["./", "index.html", "style.css?v=3", "app.js?v=3", "manifest.json"];

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
  event.respondWith(
    caches.match(event.request).then(cached => cached || fetch(event.request))
  );
});
