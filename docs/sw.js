// sw.js - Minimal service worker. Only caches the static app shell (not
// Supabase data) so the page installs cleanly as a PWA and reloads a
// touch faster; it does not provide real offline data access.
const CACHE = "pawmodoro-shell-v20";
const SHELL = ["./", "index.html", "style.css?v=20", "app.js?v=20", "manifest.json"];

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

// Checklist reminders pushed by the cloud (supabase/functions/send-reminders),
// shown even when the app is closed.
self.addEventListener("push", event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = { body: event.data?.text() }; }
  event.waitUntil(self.registration.showNotification(data.title || "Pawmodoro", {
    body: data.body || "",
    tag: data.tag,
    // Without this, a reminder replacing an earlier one with the same tag
    // (yesterday's, still in the shade) arrives without sound or vibration.
    renotify: Boolean(data.tag),
    silent: false,
    // Used where the browser honours it; on Android 8+ the sound and
    // vibration come from the app's notification settings instead.
    vibrate: [300, 150, 300, 150, 600],
    icon: "icons/icon_256.png",
    badge: "icons/icon_64.png",
    // Snooze / Done buttons (Android shows up to two). They act through
    // push_action() with this push's one-time token, no app or login needed.
    actions: Array.isArray(data.actions) ? data.actions : [],
    data: { token: data.token, api: data.api },
  }));
});

// Tapping one opens the app (or brings it forward); its buttons act directly.
self.addEventListener("notificationclick", event => {
  event.notification.close();
  event.waitUntil((async () => {
    const { token, api } = event.notification.data || {};
    if (event.action && token && api?.url && api?.key) {
      try {
        const response = await fetch(`${api.url}/rest/v1/rpc/push_action`, {
          method: "POST",
          headers: { "Content-Type": "application/json", apikey: api.key, Authorization: `Bearer ${api.key}` },
          body: JSON.stringify({ p_token: token, p_action: event.action }),
        });
        if (response.ok && (await response.json()) === "ok") {
          if (event.action === "snooze") {
            await self.registration.showNotification("Snoozed for 15 minutes", {
              body: event.notification.body, tag: event.notification.tag, silent: true,
            });
            setTimeout(() => self.registration.getNotifications({ tag: event.notification.tag })
              .then(list => list.forEach(n => n.close())), 4000);
          }
          return;
        }
      } catch { /* fall through and open the app */ }
    }
    const windows = await clients.matchAll({ type: "window", includeUncontrolled: true });
    if (windows.length) return windows[0].focus();
    return clients.openWindow("./");
  })());
});
