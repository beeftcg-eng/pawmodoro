// app.js - Pawmodoro mobile web app. No build step: plain JS + the
// Supabase JS client from a CDN. Talks to the same Postgres functions
// (supabase/schema.sql) the desktop app uses, so quests/XP/streaks/notes/
// checklist stay in sync between this and the desktop app.

const CONFIG_KEY = "pawmodoro_config";       // { url, anonKey }
const SESSION_KEY = "pawmodoro_session";     // supabase session, persisted by the client itself
const SETTINGS_KEY = "pawmodoro_settings";   // { workMin, shortBreakMin, longBreakMin, sessionsBeforeLong }
const THEME_KEY = "pawmodoro_theme";         // theme name, independent per device (matches desktop)
// Public half of the Web Push key pair; the private half is a secret of the
// send-reminders Edge Function (supabase/functions/send-reminders).
const VAPID_PUBLIC_KEY = "BAfduob9rBFf2TCWPKa_6cx_WSq4TTGsEqPKUsfSrVvJ7F1gqtNumo_R5dLgSzVE_thLAltRyRJKR6PDyYWDcso";

// Same palettes as the desktop app's theme.py, minus the desktop-only
// serif/mono font-fallback machinery — "mono" themes just use a plain
// system monospace stack here.
const THEMES = {
  "Paper": { paper: "#f4ecd8", paperLight: "#fdf6e8", paperEdge: "#c9b896", ink: "#3a2f22", inkSoft: "#6b5c46", accent: "#96433a", accentSoft: "#b98a83", mono: false },
  "Lavender Dusk": { paper: "#ece5f3", paperLight: "#f7f3fa", paperEdge: "#c1aad9", ink: "#392f4d", inkSoft: "#6d6086", accent: "#7d5ba6", accentSoft: "#b39cd0", mono: false },
  "Deep Plum": { paper: "#e5dde6", paperLight: "#f3edf4", paperEdge: "#a98bb0", ink: "#2e2032", inkSoft: "#63506a", accent: "#5c3566", accentSoft: "#8f6b98", mono: false },
  "Royal Purple": { paper: "#ece0f7", paperLight: "#f8f1fc", paperEdge: "#b98fd9", ink: "#2b1240", inkSoft: "#6b4a8a", accent: "#8e24aa", accentSoft: "#ba68c8", mono: false },
  "Charcoal Grey": { paper: "#e6e7ea", paperLight: "#f4f5f6", paperEdge: "#a9b0b8", ink: "#282b30", inkSoft: "#585e66", accent: "#4d5a6b", accentSoft: "#8994a1", mono: false },
  "Slate & Mauve": { paper: "#e8e4e8", paperLight: "#f5f2f5", paperEdge: "#ad9fac", ink: "#2f2a30", inkSoft: "#655c66", accent: "#6b4c63", accentSoft: "#a3899e", mono: false },
  "Hacker Terminal": { paper: "#080c08", paperLight: "#0f150f", paperEdge: "#1f3d1f", ink: "#39ff14", inkSoft: "#2ea82e", accent: "#00ff9c", accentSoft: "#0d8a2e", mono: true },
  "Arcade Neon": { paper: "#0d0221", paperLight: "#1a0933", paperEdge: "#4b1c73", ink: "#00f0ff", inkSoft: "#8a5cf6", accent: "#ff2fd6", accentSoft: "#b34fd1", mono: true },
};

function getTheme() {
  return localStorage.getItem(THEME_KEY) || "Paper";
}

function applyTheme(name) {
  const t = THEMES[name] || THEMES.Paper;
  const root = document.documentElement.style;
  root.setProperty("--paper", t.paper);
  root.setProperty("--paper-light", t.paperLight);
  root.setProperty("--paper-edge", t.paperEdge);
  root.setProperty("--ink", t.ink);
  root.setProperty("--ink-soft", t.inkSoft);
  root.setProperty("--accent", t.accent);
  root.setProperty("--accent-soft", t.accentSoft);
  root.setProperty("--body-font", t.mono
    ? "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace"
    : "Georgia, 'Noto Serif', serif");
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", t.paper);
}

function setTheme(name) {
  localStorage.setItem(THEME_KEY, name);
  applyTheme(name);
}

let supabaseClient = null;
let state = null;         // last sync_pull() result
// Fingerprints of the state / household last applied (sync_pull_if_changed,
// household_pull_if_changed): a periodic poll offers them and gets just
// "unchanged" back when nothing changed. Only polls offer them -- the pull
// after anything done here is a full one, so a refused action can never
// leave a stale screen behind.
let stateFingerprint = null;
let householdFingerprint = null;
let fingerprintsSupported = true;
let pomodoro = {
  phase: "work",          // "work" | "short_break" | "long_break"
  secondsLeft: 25 * 60,
  running: false,
  sessionCount: 0,
  intervalId: null,
  deadline: 0,            // Date.now() value the running phase ends at (the timer counts against this, not ticks)
  completing: false,
  paused: false,          // stopped part-way (so a re-render keeps the time left)
  // One timer across devices (set_timer_state / sync_pull's "timer"):
  owner: true,            // this phone started / changed the timer last
  revSeen: 0,             // the shared timer revision already followed (or written)
  takeover: null,         // {session, phase, minutes} a followed phase that ended here, waiting for the owner
};
const TIMER_TAKEOVER_MS = 90000;

let lastTzSent = null;         // UTC offset (minutes) last reported to the server
let tzRetryAt = 0;
let householdState;            // undefined = not fetched yet, null = not in a household, object = the household
let householdSupported = true; // false if the cloud schema predates households
let lastHouseholdFetch = 0;
let wakeLock = null;
const renderKeys = {};         // per tab: what the last render was built from, so polls only re-render on change

const QUEST_ICONS = { pomodoros: "\u{1F43E}", tasks: "✅", breaks: "☕", clear_checklist: "\u{1F9F9}" };

// ---------- Setup (which Supabase project to talk to) ----------

// The shared Pawmodoro project, used unless someone opted into their own via
// "Use a different Supabase project". The anon key is meant to ship in client
// code (it's the `anon` role; row-level security in supabase/schema.sql is
// what protects data) — never put a service_role key here. Keep in sync with
// cloud_defaults.py and Deckbuilder's src/shared/pawmodoroDefaults.ts.
const DEFAULT_CONFIG = {
  url: "https://cinxclbsgamdprftcbek.supabase.co",
  anonKey: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNpbnhjbGJzZ2FtZHByZnRjYmVrIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODk1NTc4OTIsImV4cCI6MjEwNTEzMzg5Mn0.1hpyBV2ZO3MeQUSnx3K5-XG6BbzY-gXb-4uG6Ls7Fr0",
};

function getConfig() {
  try {
    const saved = JSON.parse(localStorage.getItem(CONFIG_KEY) || "null");
    if (saved && saved.url && saved.anonKey) return saved;
  } catch {
    // fall through to the shared project
  }
  return DEFAULT_CONFIG;
}

function saveConfig(url, anonKey) {
  localStorage.setItem(CONFIG_KEY, JSON.stringify({ url, anonKey }));
}

function getSettings() {
  try {
    return { workMin: 25, shortBreakMin: 5, longBreakMin: 15, sessionsBeforeLong: 4,
      ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") };
  } catch {
    return { workMin: 25, shortBreakMin: 5, longBreakMin: 15, sessionsBeforeLong: 4 };
  }
}

function saveSettings(s) {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
}

// This phone's id for the shared timer (which device changed it last).
function deviceId() {
  try {
    let id = localStorage.getItem("pawmodoro_device");
    if (!id) {
      id = "phone-" + Math.random().toString(16).slice(2, 14);
      localStorage.setItem("pawmodoro_device", id);
    }
    return id;
  } catch {
    return "phone";
  }
}

function timerShared() {
  return getSettings().shareTimer !== false;
}

// ---------- Boot ----------

window.addEventListener("DOMContentLoaded", boot);

async function boot() {
  applyTheme(getTheme());
  const cfg = getConfig();
  if (!cfg || !cfg.url || !cfg.anonKey) {
    showSetupScreen();
    return;
  }
  initSupabase(cfg);
  showConnectingScreen();

  // Everything below talks to Supabase. Unguarded, a dead/unreachable
  // project (or just a slow mobile connection) left the page stuck on a
  // permanently blank <div id="app"> with nothing telling the user why —
  // wrapped in try/catch + a timeout so a failure always ends in a visible
  // retry screen instead of silence.
  try {
    const { data: { session }, error } = await withTimeout(
      supabaseClient.auth.getSession(), 10000, "Timed out waiting for a response."
    );
    if (error) throw error;
    if (session) {
      await enterApp();
    } else {
      showLoginScreen();
    }
  } catch (err) {
    showConnectionErrorScreen(err);
  }
}

function withTimeout(promise, ms, message) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(message)), ms)),
  ]);
}

function showConnectingScreen() {
  document.getElementById("app").innerHTML = `
    <div class="centered-card">
      <h1>\u{1F43E} Pawmodoro</h1>
      <p class="hint">Connecting…</p>
    </div>`;
}

function showConnectionErrorScreen(err) {
  document.getElementById("app").innerHTML = `
    <div class="centered-card">
      <h1>\u{1F43E} Pawmodoro</h1>
      <p class="error">Couldn't connect: ${escapeHtml(err?.message || String(err))}</p>
      <p class="hint">Check your connection and that the Supabase project is reachable, then try again.</p>
      <button id="retry-btn">Retry</button>
    </div>`;
  document.getElementById("retry-btn").addEventListener("click", boot);
}

function initSupabase(cfg) {
  supabaseClient = window.supabase.createClient(cfg.url, cfg.anonKey, {
    auth: { persistSession: true, storageKey: SESSION_KEY },
  });
}

// ---------- Setup screen ----------

function showSetupScreen() {
  document.getElementById("app").innerHTML = `
    <div class="centered-card">
      <h1>\u{1F43E} Use a different project</h1>
      <p class="hint">Only needed if you run your own Supabase project: paste its Project URL and
      anon public key (Project Settings &rarr; API).</p>
      <input id="setup-url" type="text" placeholder="https://xxxx.supabase.co" autocapitalize="off" autocorrect="off">
      <input id="setup-key" type="text" placeholder="anon public key" autocapitalize="off" autocorrect="off">
      <button id="setup-save">Save &amp; continue</button>
      <button id="setup-default" class="secondary">Use the shared Pawmodoro project</button>
      <p id="setup-error" class="error"></p>
    </div>`;
  document.getElementById("setup-default").addEventListener("click", () => {
    localStorage.removeItem(CONFIG_KEY);
    initSupabase(DEFAULT_CONFIG);
    showLoginScreen();
  });
  document.getElementById("setup-save").addEventListener("click", () => {
    const url = document.getElementById("setup-url").value.trim();
    const key = document.getElementById("setup-key").value.trim();
    if (!url || !key) {
      document.getElementById("setup-error").textContent = "Both fields are required.";
      return;
    }
    saveConfig(url, key);
    initSupabase({ url, anonKey: key });
    showLoginScreen();
  });
}

// ---------- Login screen ----------

function showLoginScreen() {
  document.getElementById("app").innerHTML = `
    <div class="centered-card">
      <h1>\u{1F43E} Pawmodoro</h1>
      <input id="login-email" type="email" placeholder="Email" autocapitalize="off" autocorrect="off">
      <input id="login-password" type="password" placeholder="Password">
      <button id="login-btn">Log in</button>
      <button id="signup-btn" class="secondary">First time — create account</button>
      <p id="login-error" class="error"></p>
      <p class="hint"><a href="#" id="custom-project-link">Use a different Supabase project</a></p>
    </div>`;

  document.getElementById("custom-project-link").addEventListener("click", (e) => {
    e.preventDefault();
    showSetupScreen();
  });
  document.getElementById("login-btn").addEventListener("click", () => doAuth("login"));
  document.getElementById("signup-btn").addEventListener("click", () => doAuth("signup"));
}

async function doAuth(mode) {
  const email = document.getElementById("login-email").value.trim();
  const password = document.getElementById("login-password").value;
  const errEl = document.getElementById("login-error");
  errEl.textContent = "";
  if (!email || !password) {
    errEl.textContent = "Email and password are required.";
    return;
  }
  // Unguarded, a flaky connection (e.g. switching wifi/cell) could make the
  // auth call reject instead of resolving with {error}, leaving the button
  // looking like it did nothing — same class of bug boot() had before it
  // got try/catch + a timeout.
  const btn = document.getElementById(mode === "signup" ? "signup-btn" : "login-btn");
  btn.disabled = true;
  try {
    const { error } = mode === "signup"
      ? await withTimeout(supabaseClient.auth.signUp({ email, password }), 10000, "Timed out. Check your connection and try again.")
      : await withTimeout(supabaseClient.auth.signInWithPassword({ email, password }), 10000, "Timed out. Check your connection and try again.");
    if (error) {
      errEl.textContent = error.message;
      return;
    }
    const { data: { session } } = await supabaseClient.auth.getSession();
    if (!session) {
      errEl.textContent = "Check your email to confirm the account, then log in.";
      return;
    }
    await enterApp();
  } catch (err) {
    errEl.textContent = err?.message || String(err);
  } finally {
    btn.disabled = false;
  }
}

// ---------- Main app ----------

let pollIntervalId = null;

// Tell the server our UTC offset so "today" (daily task reset, quest day,
// streak day) rolls over at OUR midnight rather than the server's UTC one.
async function sendTzOffset() {
  const minutes = -new Date().getTimezoneOffset();
  if (minutes === lastTzSent || Date.now() < tzRetryAt) return;
  const { error } = await supabaseClient.rpc("set_tz_offset", { p_minutes: minutes });
  if (error) tzRetryAt = Date.now() + 120000; // e.g. cloud schema not updated yet
  else lastTzSent = minutes;
}

async function enterApp() {
  // Fetch state before building the shell — the shell's initial
  // switchTab() renders a tab immediately, and pullAndRender() skips
  // refreshing the Notes editor whenever it's focused (so a poll can't
  // clobber text being typed right now), so if Notes is the tab shown on
  // load, it needs real data on this very first render regardless.
  await sendTzOffset();
  const { data, error } = await supabaseClient.rpc("sync_pull");
  if (!error) {
    state = data;
    followRemoteTimer(state?.timer);
  }
  await refreshHousehold(true);
  renderShell();
  updateLevelBadge();
  startPolling();
  refreshPushSubscription();
}

function startPolling() {
  // Logging out and back in within the same page session (no reload)
  // would otherwise stack up a new poller on top of any still-running
  // one from a previous login.
  stopPolling();
  // pick up changes made on the desktop app; skipped while the page is hidden
  // (screen off / another app in front), which saves battery and data
  pollIntervalId = setInterval(() => {
    if (!document.hidden) pullAndRender({ poll: true });
    checkTaskReminders();
  }, 5000);
}

function stopPolling() {
  clearInterval(pollIntervalId);
  pollIntervalId = null;
}

// Coming back to the app: the timer catches up on time that passed while the
// screen was locked, and everything refreshes right away.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  if (pomodoro.running) {
    requestWakeLock();
    tickPomodoro();
  }
  if (supabaseClient && state) pullAndRender();
});

async function refreshHousehold(force) {
  const due = force || activeTab === "shared" || activeTab === "progress" || Date.now() - lastHouseholdFetch > 30000;
  if (!due || !householdSupported) return;
  lastHouseholdFetch = Date.now();
  const { data, error } = await fetchHousehold(force);
  if (error) {
    // A cloud schema from before households existed: carry on without them.
    if (error.code === "PGRST202" || error.status === 404) householdSupported = false;
    if (householdState === undefined) householdState = null;
    return;
  }
  if (data?.unchanged) return;
  householdFingerprint = data?.fingerprint ?? null;
  householdState = data?.household ?? null; // null when this account isn't in a household
}

// household_pull_if_changed (like fetchState): {household, fingerprint}, or
// {unchanged} when the fingerprint offered still matches. Shaped the same
// on an older schema, from a plain household_pull.
async function fetchHousehold(force) {
  if (fingerprintsSupported) {
    const { data, error } = await supabaseClient.rpc("household_pull_if_changed",
      { p_fingerprint: force ? null : householdFingerprint });
    if (!error) return { data };
    if (error.code !== "PGRST202" && error.status !== 404) return { error };
    fingerprintsSupported = false;
  }
  const { data, error } = await supabaseClient.rpc("household_pull");
  return error ? { error } : { data: { household: data } };
}

function renderShell() {
  const themeOptions = Object.keys(THEMES)
    .map(name => `<option value="${name}"${name === getTheme() ? " selected" : ""}>${name}</option>`)
    .join("");
  document.getElementById("app").innerHTML = `
    <header id="header">
      <span id="level-badge"></span>
      <select id="theme-select" title="Theme">${themeOptions}</select>
      <button id="logout-btn" title="Log out">⏻</button>
    </header>
    <main id="view"></main>
    <nav id="tabbar">
      <button data-tab="notes">\u{1F4DD} Notes</button>
      <button data-tab="checklist">✅ Checklist</button>
      <button data-tab="pomodoro">⏱️ Pomodoro</button>
      <button data-tab="progress">\u{1F3C6} Progress</button>
      <button data-tab="shared">\u{1F3E0} Shared</button>
      <button data-tab="calendar">\u{1F4C5} Calendar</button>
    </nav>`;
  document.getElementById("logout-btn").addEventListener("click", async () => {
    stopPolling();
    stateFingerprint = householdFingerprint = null;
    await supabaseClient.auth.signOut();
    showLoginScreen();
  });
  document.getElementById("theme-select").addEventListener("change", e => setTheme(e.target.value));
  document.querySelectorAll("#tabbar button").forEach(btn => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
  switchTab("notes");
}

let activeTab = "notes";

function switchTab(tab) {
  activeTab = tab;
  document.querySelectorAll("#tabbar button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  if (tab === "notes") renderNotes();
  else if (tab === "checklist") renderChecklist();
  else if (tab === "pomodoro") renderPomodoro();
  else if (tab === "progress") renderProgress();
  else if (tab === "shared") renderShared();
  else if (tab === "calendar") renderCalendar();
}

let pullRequestId = 0;

async function fetchState(poll) {
  if (fingerprintsSupported) {
    const { data, error } = await supabaseClient.rpc("sync_pull_if_changed", { p_fingerprint: poll ? stateFingerprint : null });
    if (!error) return { data };
    // A cloud schema from before v2.20: plain pulls from now on.
    if (error.code !== "PGRST202" && error.status !== 404) return { error };
    fingerprintsSupported = false;
  }
  return supabaseClient.rpc("sync_pull");
}

async function pullAndRender({ poll = false } = {}) {
  // Every call actually fires the request — an action like checking off a
  // task needs its own follow-up pull to always go through, never get
  // silently skipped because a periodic poll happened to be in flight.
  // What's guarded against instead is a *stale* response landing after a
  // newer one already applied: each call gets an id, and a response is
  // only applied if its id is still the most recent one issued, so a slow
  // older request can never overwrite state a faster newer one already set.
  const requestId = ++pullRequestId;
  await sendTzOffset();
  const { data, error } = await fetchState(poll);
  if (requestId !== pullRequestId) return;
  if (error) {
    console.error("sync_pull failed", error);
    return;
  }
  if (!data?.unchanged) {
    stateFingerprint = data?.fingerprint ?? null;
    state = data;
    followRemoteTimer(state?.timer);
    purgeTrashedAttachments();
  }
  await refreshHousehold(false);
  if (requestId !== pullRequestId) return;
  updateLevelBadge();
  rerenderActiveTab();
}

// Re-renders the visible tab after a pull, but only if what it shows actually
// changed -- a poll every few seconds would otherwise wipe whatever is being
// typed into a tab's input box.
function rerenderActiveTab() {
  if (activeTab === "notes") {
    // Only refresh if the editor doesn't currently have focus, so a
    // poll landing mid-keystroke can never clobber text being typed
    // right now — it picks up on the next poll after you tap away.
    const editor = document.getElementById("notes-editor");
    if (editor && document.activeElement !== editor && !notesSaveTimer) {
      if (!notePages().some(p => p.id === notesPage)) setNotesPage("main");
      if (renderKeys.notesPages !== notePagesKey()) renderNotes();
      else editor.innerHTML = pageHtml(notesPage);
    }
  } else if (activeTab === "checklist") {
    if (renderKeys.checklist !== checklistKey()) renderChecklist();
  } else if (activeTab === "progress") {
    if (renderKeys.progress !== progressKey()) renderProgress();
  } else if (activeTab === "shared") {
    if (renderKeys.shared !== sharedKey()) renderShared();
  } else if (activeTab === "calendar") {
    // not while the add / edit form is open: it would lose what's typed
    if (!calendarForm && renderKeys.calendar !== calendarKey()) renderCalendar();
  }
}

// Keeps what was typed (and the focus) in an input across a re-render.
function keepDraft(id) {
  const el = document.getElementById(id);
  const value = el ? el.value : "";
  const focused = el && document.activeElement === el;
  return () => {
    const fresh = document.getElementById(id);
    if (!fresh) return;
    fresh.value = value;
    if (focused) fresh.focus();
  };
}

function levelFromXp(totalXp) {
  let level = 1, remaining = totalXp;
  let needed = 80 + (level - 1) * 20;
  while (remaining >= needed) {
    remaining -= needed;
    level += 1;
    needed = 80 + (level - 1) * 20;
  }
  return { level, xpInto: remaining, xpNeeded: needed };
}

const TITLES = [
  [1, "Curious Pup"], [3, "Eager Fox"], [6, "Focused Hound"], [10, "Diligent Doe"],
  [15, "Steady Stag"], [20, "Tireless Tabby"], [30, "Productivity Panther"],
  [40, "Zen Owl"], [50, "Legendary Loremaster"],
];
function titleForLevel(level) {
  let result = TITLES[0][1];
  for (const [threshold, name] of TITLES) if (level >= threshold) result = name;
  return result;
}

function updateLevelBadge() {
  if (!state) return;
  const { level } = levelFromXp(state.xp);
  const badge = document.getElementById("level-badge");
  if (badge) badge.textContent = `\u{1F3C6} Lv.${level}`;
}

// ---------- Notes tab ----------

let notesSaveTimer = null;
let notesSaveNow = null;  // runs the pending save immediately (before switching page)

// Notes pages: "main" is the original notes text (app_state.notes), the
// rest come from the notes_pages table (desktop v2.16+). An older cloud
// schema sends no notes_pages at all; then there's just the one page.
const NOTES_PAGE_KEY = "pawmodoro_notes_page";
let notesPage = (() => { try { return localStorage.getItem(NOTES_PAGE_KEY) || "main"; } catch { return "main"; } })();

function notePages() {
  return [{ id: "main", title: "Notes" }, ...(state?.notes_pages ?? [])];
}

function notePagesKey() {
  return JSON.stringify([notesPage, notePages().map(p => [p.id, p.title])]);
}

function pageHtml(id) {
  if (id === "main") return state?.notes ?? "";
  return (state?.notes_pages ?? []).find(p => p.id === id)?.html ?? "";
}

function setNotesPage(id) {
  notesPage = id;
  try { localStorage.setItem(NOTES_PAGE_KEY, id); } catch { /* private mode */ }
}

function newPageId() {
  const bytes = crypto.getRandomValues(new Uint8Array(4));
  return Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
}

// Saves a page only if nobody else saved it since this phone last saw it
// (e.g. the desktop catching up after being offline). If they did, their
// version is added to the bottom of the same page, then ours is saved --
// nothing lost, and no extra pages. Saves run one at a time: typing while a
// slow save is still on its way would otherwise send the next one with the
// old revision and look like a clash with this phone's own text.
let notesSaveChain = Promise.resolve();
const notesLastSaved = new Map();  // page id -> the html this phone last saved there

function saveNotesChecked(pageId, title, html) {
  const run = notesSaveChain.then(() => saveNotesCheckedNow(pageId, title, html));
  notesSaveChain = run.catch(() => {});
  return run;
}

// The other device's version, as a block for the bottom of this page.
// (The desktop's text is a whole HTML document; only its body is wanted.)
function otherVersionHtml(theirs) {
  const body = /<body[^>]*>([\s\S]*)<\/body>/i.exec(theirs ?? "");
  const text = body ? body[1] : (theirs ?? "");
  return `<hr><p><i>From your other device (${new Date().toTimeString().slice(0, 5)}):</i></p>${text}`;
}

async function saveNotesCheckedNow(pageId, title, html) {
  const main = pageId === "main";
  const page = main ? null : (state?.notes_pages ?? []).find(p => p.id === pageId);
  let base = main ? (state?.notes_rev ?? null) : (page?.rev ?? null);
  for (let attempt = 0; attempt < 3; attempt++) {
    const { data, error } = main
      ? await supabaseClient.rpc("set_notes_checked", { p_notes: html, p_base_rev: base })
      : await supabaseClient.rpc("set_note_page_checked", { p_id: pageId, p_title: title, p_html: html, p_base_rev: base });
    if (error && isMissingFunction(error)) {  // older cloud: plain save
      return main ? supabaseClient.rpc("set_notes", { p_notes: html })
                  : supabaseClient.rpc("set_note_page", { p_id: pageId, p_title: title, p_html: html });
    }
    if (error) { toast("⚠️ Couldn't save notes", error.message || "Check your connection."); return; }
    if (data.ok) {
      if (main && state) state.notes_rev = data.rev;
      else if (page) page.rev = data.rev;
      notesLastSaved.set(pageId, html);
      return html;
    }
    base = data.rev;
    const theirs = main ? data.notes : data.html;
    if (theirs === notesLastSaved.get(pageId)) continue;  // our own last save (a pull told us an older revision)
    // Really changed elsewhere: keep theirs at the bottom of this page.
    const extra = otherVersionHtml(theirs);
    html += extra;
    // and into the open editor, so the next save from typing keeps it too
    const editor = document.getElementById("notes-editor");
    if (editor && notesPage === pageId) editor.insertAdjacentHTML("beforeend", extra);
    toast("Notes changed in two places", "Both kept: the other version is at the bottom of the page.");
  }
}

async function notesPageCall(name, params, failText) {
  const { error } = await supabaseClient.rpc(name, params);
  if (error) toast(`⚠️ ${failText}`, error.message || "Check your connection and try again.");
  await pullAndRender();
  return !error;
}

async function switchNotesPage(id) {
  if (notesSaveNow) await notesSaveNow();
  setNotesPage(id);
  renderNotes();
}

async function addNotesPage() {
  const title = (prompt("New page name") ?? "").trim();
  if (!title) return;
  if (notesSaveNow) await notesSaveNow();
  const id = newPageId();
  setNotesPage(id);
  await notesPageCall("set_note_page_checked", { p_id: id, p_title: title, p_html: "", p_base_rev: null }, "Couldn't add the page");
  renderNotes();
}

async function renameNotesPage(page) {
  const title = (prompt("Rename page", page.title) ?? "").trim();
  if (!title || title === page.title) return;
  if (notesSaveNow) await notesSaveNow();
  await notesPageCall("set_note_page_checked", { p_id: page.id, p_title: title, p_html: pageHtml(page.id), p_base_rev: null }, "Couldn't rename the page");
  renderNotes();
}

async function removeNotesPage(page) {
  if (!confirm(`Delete the page “${page.title}” and everything on it?`)) return;
  clearTimeout(notesSaveTimer);
  notesSaveTimer = null;
  notesSaveNow = null;
  setNotesPage("main");
  await notesPageCall("remove_note_page", { p_id: page.id }, "Couldn't delete the page");
  renderNotes();
}

// Simple contenteditable formatting toolbar. document.execCommand is
// long-deprecated but still the only broadly-supported way to do basic
// rich text (bold/underline/lists) in a plain contenteditable without
// pulling in a whole editor library — good enough for parity with a
// subset of the desktop notes toolbar. Saves as HTML, same as desktop's
// QTextEdit.toHtml(), so formatting round-trips between the two.
const NOTES_TOOLBAR = [
  { cmd: "bold", label: "<b>B</b>", title: "Bold" },
  { cmd: "underline", label: "<u>U</u>", title: "Underline" },
  { cmd: "insertUnorderedList", label: "• List", title: "Bullet list" },
  { cmd: "insertOrderedList", label: "1. List", title: "Numbered list" },
  { cmd: "removeFormat", label: "Clear", title: "Clear formatting" },
];

function renderNotes() {
  const view = document.getElementById("view");
  const toolbarHtml = NOTES_TOOLBAR
    .map(b => `<button type="button" data-cmd="${b.cmd}" title="${b.title}">${b.label}</button>`)
    .join("");
  if (!notePages().some(p => p.id === notesPage)) setNotesPage("main");
  renderKeys.notesPages = notePagesKey();
  const pagesSupported = Array.isArray(state?.notes_pages);
  const current = notePages().find(p => p.id === notesPage);
  const pageTabs = pagesSupported ? `
      <div class="notes-pages">
        ${notePages().map(p => `<button type="button" class="notes-page-tab${p.id === notesPage ? " active" : ""}" data-page="${escapeHtml(p.id)}">${escapeHtml(p.title)}</button>`).join("")}
        <button type="button" class="notes-page-add" title="Add a page">+ Page</button>
        ${notesPage !== "main" ? `
          <button type="button" class="rename-btn notes-page-rename" title="Rename this page">✎</button>
          <button type="button" class="remove-btn notes-page-remove" title="Delete this page">✕</button>` : ""}
      </div>` : "";
  view.innerHTML = `
    <div class="pane">
      ${pageTabs}
      <div class="notes-toolbar">${toolbarHtml}</div>
      <div id="notes-editor" class="notes-editor" contenteditable="true" data-placeholder="Jot anything here. It saves itself."></div>
      <p id="notes-status" class="hint">Autosaved</p>
    </div>`;
  const editor = document.getElementById("notes-editor");
  editor.innerHTML = pageHtml(notesPage);
  view.querySelectorAll(".notes-page-tab").forEach(btn => {
    btn.addEventListener("click", () => switchNotesPage(btn.dataset.page));
  });
  view.querySelector(".notes-page-add")?.addEventListener("click", addNotesPage);
  view.querySelector(".notes-page-rename")?.addEventListener("click", () => renameNotesPage(current));
  view.querySelector(".notes-page-remove")?.addEventListener("click", () => removeNotesPage(current));

  document.querySelectorAll(".notes-toolbar button").forEach(btn => {
    btn.addEventListener("click", () => {
      editor.focus();
      document.execCommand(btn.dataset.cmd, false, null);
      scheduleNotesSave(editor);
    });
  });
  editor.addEventListener("input", () => scheduleNotesSave(editor));
}

function scheduleNotesSave(editor) {
  // #notes-status (and the editor itself) may no longer exist by the
  // time this fires, or by the time the debounced save below completes,
  // if the user has since switched to a different tab — switchTab()
  // replaces #view's innerHTML entirely. Guard every DOM touch so the
  // save (and updating in-memory `state`, which must happen regardless
  // of whether the tab is still visible) can never be aborted by a null
  // reference partway through.
  const savingStatus = document.getElementById("notes-status");
  if (savingStatus) savingStatus.textContent = "Saving…";
  clearTimeout(notesSaveTimer);
  const pageId = notesPage;  // the page this edit belongs to, even if you switch before it saves
  const save = async () => {
    clearTimeout(notesSaveTimer);
    notesSaveTimer = null;
    notesSaveNow = null;
    const html = editor.innerHTML;
    if (pageId === "main") {
      const saved = await saveNotesChecked("main", "Notes", html);
      if (state) state.notes = saved ?? html;
    } else {
      const page = (state?.notes_pages ?? []).find(p => p.id === pageId);
      if (!page) return;  // deleted meanwhile
      const saved = await saveNotesChecked(pageId, page.title, html);
      page.html = saved ?? html;
    }
    const status = document.getElementById("notes-status");
    if (status) status.textContent = "Autosaved";
  };
  notesSaveNow = save;
  notesSaveTimer = setTimeout(save, 800);
}

// ---------- Checklist tab ----------

// Monthly tasks and due dates: the same rules as task_dates.py and
// month_occurrence() / task_reminds_on() in schema.sql.
const openTaskDetails = new Set();  // task ids whose steps & note panel is open

function monthOccurrence(monthDay, now) {
  const inMonth = (y, m) => new Date(y, m, Math.min(monthDay || 1, new Date(y, m + 1, 0).getDate()));
  const candidate = inMonth(now.getFullYear(), now.getMonth());
  return localISODate(candidate <= now ? candidate : inMonth(now.getFullYear(), now.getMonth() - 1));
}

function taskRemindsToday(task, now) {
  const today = localISODate(now);
  if (task.recurrence === "monthly") return monthOccurrence(task.month_day, now) === today;
  if (task.recurrence === "once" && task.due_date) return task.due_date <= today;
  return true;
}

// Days in a row a daily task has been done (from done_days, which the cloud
// keeps); today not done yet doesn't break it. The same rule as
// task_dates.streak.
function taskStreak(task, now = new Date()) {
  if (task.recurrence !== "daily" || !Array.isArray(task.done_days)) return 0;
  const done = new Set(task.done_days);
  const day = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (!done.has(localISODate(day))) day.setDate(day.getDate() - 1);
  let count = 0;
  while (done.has(localISODate(day))) {
    count++;
    day.setDate(day.getDate() - 1);
  }
  return count;
}

// "overdue", "today" or null.
function taskDueStatus(task, now = new Date()) {
  if (task.recurrence !== "once" || !task.due_date || task.completed_today) return null;
  const today = localISODate(now);
  return task.due_date < today ? "overdue" : task.due_date === today ? "today" : null;
}

function taskScheduleLabel(task, now = new Date()) {
  if (task.recurrence === "monthly") return `monthly, ${ordinal(task.month_day || 1)}`;
  if (task.recurrence === "once" && task.due_date) {
    if (task.due_date === localISODate(now)) return "due today";
    const [y, m, d] = task.due_date.split("-").map(Number);
    return "due " + new Date(y, m - 1, d).toLocaleDateString(undefined,
      { weekday: "short", day: "numeric", month: "short", year: y === now.getFullYear() ? undefined : "numeric" });
  }
  return task.recurrence;
}

async function taskCall(name, params, failTitle) {
  const { error } = await supabaseClient.rpc(name, params);
  if (error) {
    toast(failTitle, isMissingFunction(error)
      ? "The cloud database needs updating: re-run supabase/schema.sql (see MOBILE_SYNC.md)."
      : error.message || "Check your connection and try again.");
  }
  await pullAndRender();
}

function saveTaskDetails(task, subtasks, note = task.note ?? "") {
  return taskCall("set_task_details",
    { p_task_id: task.id, p_note: note, p_subtasks: subtasks.map(s => ({ id: s.id, text: s.text })) },
    "⚠️ Couldn't save the steps");
}

// The steps & note panel under a task.
function taskDetailsHtml(task) {
  const subtasks = Array.isArray(task.subtasks) ? task.subtasks : [];
  return `
    <div class="task-details" ${openTaskDetails.has(task.id) ? "" : "hidden"}>
      ${task.recurrence === "once" ? `
        <label class="due-row">Due date <input class="task-due" type="date" value="${escapeHtml(task.due_date ?? "")}"></label>` : ""}
      <ul class="subtask-list">
        ${subtasks.map((sub, i) => `
          <li class="subtask${sub.done ? " done" : ""}" data-index="${i}">
            <label><input type="checkbox" ${sub.done ? "checked" : ""}><span>${escapeHtml(sub.text)}</span></label>
            <button class="remove-btn subtask-remove" title="Remove step">✕</button>
          </li>`).join("")}
      </ul>
      <div class="subtask-add">
        <input class="subtask-text" type="text" placeholder="Add a step">
        <button class="subtask-add-btn">Add</button>
      </div>
      <textarea class="task-note" rows="2" placeholder="Note">${escapeHtml(task.note ?? "")}</textarea>
    </div>`;
}

function wireTaskDetails(li, task) {
  const panel = li.querySelector(".task-details");
  const subtasks = Array.isArray(task.subtasks) ? task.subtasks : [];
  li.querySelector(".details-btn").addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    if (panel.hidden) openTaskDetails.delete(task.id); else openTaskDetails.add(task.id);
  });
  panel.querySelector(".task-due")?.addEventListener("change", e => taskCall("set_task_due_date",
    { p_task_id: task.id, p_due_date: e.target.value || null }, "⚠️ Couldn't save the due date"));
  panel.querySelectorAll(".subtask").forEach(row => {
    const sub = subtasks[Number(row.dataset.index)];
    row.querySelector("input").addEventListener("change", e => taskCall("set_subtask_done",
      { p_task_id: task.id, p_sub_id: sub.id, p_done: e.target.checked }, "⚠️ Couldn't save"));
    row.querySelector(".subtask-remove").addEventListener("click", () =>
      saveTaskDetails(task, subtasks.filter(s => s.id !== sub.id)));
  });
  const input = panel.querySelector(".subtask-text");
  const add = () => {
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    saveTaskDetails(task, [...subtasks, { id: Math.random().toString(16).slice(2, 10), text }]);
  };
  panel.querySelector(".subtask-add-btn").addEventListener("click", add);
  input.addEventListener("keydown", e => { if (e.key === "Enter") add(); });
  panel.querySelector(".task-note").addEventListener("change", e => {
    if (e.target.value !== (task.note ?? "")) saveTaskDetails(task, subtasks, e.target.value);
  });
}

// What's typed into the steps & note panels but not saved yet, so a
// re-render (a poll found a change) doesn't throw it away.
function keepTaskDetailDrafts() {
  const drafts = [];
  document.querySelectorAll(".task-item[data-task-id]").forEach(li => {
    for (const sel of [".subtask-text", ".task-note"]) {
      const el = li.querySelector(sel);
      if (el && (document.activeElement === el || (sel === ".subtask-text" && el.value))) {
        drafts.push({ id: li.dataset.taskId, sel, value: el.value, focused: document.activeElement === el });
      }
    }
  });
  return () => drafts.forEach(d => {
    const el = document.querySelector(`.task-item[data-task-id="${CSS.escape(d.id)}"] ${d.sel}`);
    if (!el) return;
    el.value = d.value;
    if (d.focused) el.focus();
  });
}

// Shared row renderer for both the regular checklist and the wishlist
// section below it — identical markup/behavior except the regular list
// also shows each task's recurrence tag.
// The regular list also gets a daily reminder time (the same one the
// desktop's "daily at" reminder uses). The desktop's "every N hours"
// reminders are desktop-only, so they never show up here.
function renderTaskList(container, tasks, { showRecurrence = false, reminders = false } = {}) {
  tasks.forEach((task, index) => {
    const li = document.createElement("li");
    const due = showRecurrence ? taskDueStatus(task) : null;
    li.className = "task-item" + (task.completed_today ? " done" : "") + (due ? ` due-${due}` : "");
    li.dataset.taskId = task.id;
    const subtasks = Array.isArray(task.subtasks) ? task.subtasks : [];
    const extras = !showRecurrence ? ""
      : (subtasks.length ? ` <em>☑ ${subtasks.filter(s => s.done).length}/${subtasks.length}</em>` : "")
        + ((task.note ?? "").trim() ? " <em>\u{1F4DD}</em>" : "")
        + (taskStreak(task) >= 2 ? ` <em title="days in a row">\u{1F525} ${taskStreak(task)}</em>` : "");
    const bell = !reminders ? ""
      : task.reminder_every_h ? ` <em>\u{1F514} every ${Number(task.reminder_every_h)}h</em>`
      : task.reminder_time ? ` <em>\u{1F514} ${escapeHtml(task.reminder_time)}</em>` : "";
    const snoozed = reminders && task.snoozed_until && new Date(task.snoozed_until) > new Date()
      ? ` <em>\u{1F4A4} ${new Date(task.snoozed_until).toTimeString().slice(0, 5)}</em>` : "";
    const everyMode = Boolean(task.reminder_every_h);
    li.innerHTML = `
      <div class="reorder-col">
        <button class="reorder-btn" data-dir="up" title="Move up" ${index === 0 ? "disabled" : ""}>▲</button>
        <button class="reorder-btn" data-dir="down" title="Move down" ${index === tasks.length - 1 ? "disabled" : ""}>▼</button>
      </div>
      <label>
        <input type="checkbox" ${task.completed_today ? "checked" : ""}>
        <span>${due === "overdue" ? "⚠ " : ""}${escapeHtml(task.text)}${showRecurrence ? ` <em class="schedule">[${escapeHtml(taskScheduleLabel(task))}]</em>` : ""}${bell}${snoozed}${extras}</span>
      </label>
      ${showRecurrence ? `<button class="rename-btn details-btn" title="Steps & note">☰</button>` : ""}
      ${reminders ? `<button class="rename-btn reminder-btn" title="Reminder">\u{1F514}</button>` : ""}
      <button class="rename-btn" title="Rename">✎</button>
      <button class="remove-btn" title="Remove">✕</button>
      ${reminders ? `
        <div class="reminder-edit" hidden>
          <select class="reminder-mode">
            <option value="time" ${everyMode ? "" : "selected"}>Remind me daily at</option>
            <option value="every" ${everyMode ? "selected" : ""}>Remind me every</option>
          </select>
          <input class="reminder-time" type="time" value="${escapeHtml(task.reminder_time ?? "")}" ${everyMode ? "hidden" : ""}>
          <span class="reminder-every" ${everyMode ? "" : "hidden"}>
            <input type="number" min="1" max="24" value="${Number(task.reminder_every_h) || 8}"> hours
          </span>
          <button class="reminder-save">Set</button>
          <button class="reminder-clear">Clear</button>
        </div>` : ""}
      ${showRecurrence ? taskDetailsHtml(task) : ""}`;
    li.querySelector("input").addEventListener("change", async e => {
      await toggleTask(task.id, e.target.checked);
    });
    if (showRecurrence) wireTaskDetails(li, task);
    if (reminders) {
      const editor = li.querySelector(".reminder-edit");
      const mode = editor.querySelector(".reminder-mode");
      li.querySelector(".reminder-btn").addEventListener("click", () => { editor.hidden = !editor.hidden; });
      mode.addEventListener("change", () => {
        editor.querySelector(".reminder-time").hidden = mode.value !== "time";
        editor.querySelector(".reminder-every").hidden = mode.value !== "every";
      });
      li.querySelector(".reminder-save").addEventListener("click", () => {
        if (mode.value === "every") {
          const hours = Math.round(Number(editor.querySelector(".reminder-every input").value));
          if (hours >= 1 && hours <= 24) setTaskReminder(task.id, null, hours);
          else toast("Every 1 to 24 hours", "Pick a number of hours between 1 and 24.");
        } else {
          const time = editor.querySelector(".reminder-time").value;
          if (time) setTaskReminder(task.id, time, null);
        }
      });
      li.querySelector(".reminder-clear").addEventListener("click", () => setTaskReminder(task.id, null, null));
    }
    li.querySelector('.rename-btn[title="Rename"]').addEventListener("click", async () => {
      const text = (prompt("Rename task", task.text) ?? "").trim();
      if (!text || text === task.text) return;
      const { error } = await supabaseClient.rpc("rename_task", { p_task_id: task.id, p_text: text });
      if (error) toast("⚠️ Couldn't rename", error.message || "Check your connection and try again.");
      await pullAndRender();
    });
    li.querySelector(".remove-btn").addEventListener("click", async () => {
      await supabaseClient.rpc("remove_task", { p_task_id: task.id });
      await pullAndRender();
    });
    li.querySelectorAll(".reorder-btn").forEach(btn => {
      btn.addEventListener("click", () => moveTask(tasks, index, btn.dataset.dir === "up" ? -1 : 1));
    });
    container.appendChild(li);
  });
}

function checklistKey() {
  // (with the date: "due today" turns into "overdue" at midnight)
  return JSON.stringify([localISODate(new Date()), state?.checklist ?? []]);
}

function renderChecklist() {
  const restoreDraft = keepDraft("task-text");
  const restoreDetailDrafts = keepTaskDetailDrafts();
  const draftRepeat = document.getElementById("task-recurrence")?.value;
  renderKeys.checklist = checklistKey();
  const view = document.getElementById("view");
  const allTasks = state?.checklist ?? [];
  // Cards pushed from the Deckbuilder wishlist get their own section below
  // (same tab, not a new one) so they don't mix into, or get counted
  // toward, the regular checklist.
  const tasks = allTasks.filter(t => t.source !== "wishlist");
  const wishlistTasks = allTasks.filter(t => t.source === "wishlist");
  view.innerHTML = `
    <div class="pane">
      <div id="push-row" class="push-row"></div>
      <ul id="task-list" class="task-list"></ul>
      <div class="add-row">
        <input id="task-text" type="text" placeholder="e.g. Walk the dogs">
        <select id="task-recurrence">
          <option value="daily">daily</option>
          <option value="weekly">weekly</option>
          <option value="monthly">monthly</option>
          <option value="once">once</option>
        </select>
        <input id="task-month-day" type="number" min="1" max="31" value="${new Date().getDate()}" title="Day of the month" hidden>
        <input id="task-due-date" type="date" title="Due date (optional)" hidden>
        <button id="task-add">Add</button>
      </div>
      ${wishlistTasks.length ? `
        <h3 class="section-heading">\u{1F0CF} Card Wishlist</h3>
        <ul id="wishlist-task-list" class="task-list"></ul>
      ` : ""}
    </div>`;

  renderTaskList(document.getElementById("task-list"), tasks, { showRecurrence: true, reminders: true });
  updatePushRow();
  const wishlistList = document.getElementById("wishlist-task-list");
  if (wishlistList) renderTaskList(wishlistList, wishlistTasks);

  const repeat = document.getElementById("task-recurrence");
  const monthDay = document.getElementById("task-month-day");
  const dueDate = document.getElementById("task-due-date");
  const showPickers = () => {
    monthDay.hidden = repeat.value !== "monthly";
    dueDate.hidden = repeat.value !== "once";
  };
  if (draftRepeat) repeat.value = draftRepeat;
  showPickers();
  repeat.addEventListener("change", showPickers);
  document.getElementById("task-add").addEventListener("click", async () => {
    const text = document.getElementById("task-text").value.trim();
    if (!text) return;
    const recurrence = repeat.value;
    const params = { p_text: text, p_recurrence: recurrence, p_reminder_time: null };
    // Only sent when used, so plain tasks still work on a cloud schema older than v2.18.
    if (recurrence === "monthly") {
      const day = Math.round(Number(monthDay.value));
      if (!(day >= 1 && day <= 31)) return toast("Day of the month", "Pick a day between 1 and 31.");
      params.p_month_day = day;
    }
    if (recurrence === "once" && dueDate.value) params.p_due_date = dueDate.value;
    const { error } = await supabaseClient.rpc("add_task", params);
    if (error) {
      toast("⚠️ Couldn't add the task", isMissingFunction(error)
        ? "The cloud database needs updating: re-run supabase/schema.sql (see MOBILE_SYNC.md)."
        : error.message || "Check your connection and try again.");
      return;
    }
    document.getElementById("task-text").value = "";
    dueDate.value = "";
    await pullAndRender();
  });
  restoreDraft();
  restoreDetailDrafts();
}

// ---------- Phone push notifications ----------
// With these on, a task's 🔔 time reaches the phone even when this app is
// closed: the cloud sends it (see supabase/functions/send-reminders).

let pushOn = false;          // this phone gets push notifications (cached; see updatePushRow)
let pushEndpoint = null;

function pushSupported() {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

function isIos() {
  return /iphone|ipad|ipod/i.test(navigator.userAgent);
}

function vapidKeyBytes() {
  const padded = VAPID_PUBLIC_KEY + "=".repeat((4 - VAPID_PUBLIC_KEY.length % 4) % 4);
  const raw = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, c => c.charCodeAt(0));
}

async function currentPushSubscription() {
  if (!pushSupported()) return null;
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

async function savePushSubscription(sub) {
  const json = sub.toJSON();
  return supabaseClient.rpc("save_push_subscription", {
    p_endpoint: json.endpoint, p_p256dh: json.keys.p256dh, p_auth: json.keys.auth,
  });
}

async function enablePush() {
  if (await Notification.requestPermission() !== "granted") {
    toast("Notifications are blocked", "Allow them for this app in your phone's settings, then try again.");
    return updatePushRow();
  }
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription()
      ?? await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: vapidKeyBytes() });
    const { error } = await savePushSubscription(sub);
    if (error) throw new Error(error.message);
    toast("\u{1F514} Phone notifications on", "Your checklist reminders will arrive even with the app closed.");
  } catch (err) {
    toast("⚠️ Couldn't turn on notifications", err.message || String(err));
  }
  updatePushRow();
}

async function disablePush() {
  const sub = await currentPushSubscription();
  if (sub) {
    await supabaseClient.rpc("delete_push_subscription", { p_endpoint: sub.endpoint });
    await sub.unsubscribe();
  }
  updatePushRow();
}

// Re-registers an existing subscription with whoever is signed in now
// (e.g. after logging into another account on the same phone).
async function refreshPushSubscription() {
  try {
    const sub = await currentPushSubscription();
    pushOn = Boolean(sub) && Notification.permission === "granted";
    pushEndpoint = pushOn ? sub.endpoint : null;
    if (pushOn) await savePushSubscription(sub);
  } catch { /* best-effort */ }
}

async function updatePushRow() {
  const row = document.getElementById("push-row");
  if (!row) return;
  if (!pushSupported()) {
    row.innerHTML = isIos()
      ? `<p class="hint">\u{1F514} For reminders with the app closed, add Pawmodoro to your Home Screen (Share → Add to Home Screen) and open it from there.</p>`
      : `<p class="hint">\u{1F514} This browser can't show reminders while the app is closed.</p>`;
    return;
  }
  const sub = await currentPushSubscription();
  const on = Boolean(sub) && Notification.permission === "granted";
  pushOn = on;
  pushEndpoint = on ? sub.endpoint : null;
  row.innerHTML = on
    ? `<span class="hint">\u{1F514} Phone notifications are on</span> <button id="push-toggle" class="secondary-btn">Turn off</button>`
    : `<span class="hint">\u{1F514} Get your reminders even with the app closed</span> <button id="push-toggle">Turn on</button>`;
  document.getElementById("push-toggle").addEventListener("click", on ? disablePush : enablePush);
}

async function setTaskReminder(taskId, time, everyHours) {
  let { error } = await supabaseClient.rpc("set_task_reminder_mode",
    { p_task_id: taskId, p_reminder_time: time, p_every_h: everyHours });
  if (error && isMissingFunction(error) && !everyHours) {
    ({ error } = await supabaseClient.rpc("set_task_reminder", { p_task_id: taskId, p_reminder_time: time }));
  }
  if (error) toast("⚠️ Couldn't save the reminder", error.message || "Check your connection and try again.");
  else if (time && window.Notification && Notification.permission === "default") Notification.requestPermission();
  await pullAndRender();
}

// The cloud's database is older than this page (schema.sql not re-run).
function isMissingFunction(error) {
  return error?.code === "PGRST202" || /could not find the function/i.test(error?.message ?? "");
}

// Fires the daily reminders while this page is open, for phones without
// push turned on (with it, the cloud sends them, open or not). Each one
// fires at most once a day per device.
const REMINDED_KEY = "pawmodoro_reminded";  // { taskId: "YYYY-MM-DD" }

function checkTaskReminders() {
  if (!state?.checklist || pushOn) return;
  const now = new Date();
  const today = localISODate(now);
  const hm = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  let reminded;
  try { reminded = JSON.parse(localStorage.getItem(REMINDED_KEY) || "{}"); } catch { reminded = {}; }
  let changed = false;
  for (const task of state.checklist) {
    if (!task.reminder_time || task.completed_today || task.source === "wishlist") continue;
    if (!taskRemindsToday(task, now)) continue;  // a monthly task on its day, a dated one from its date
    if (reminded[task.id] === today || hm < task.reminder_time) continue;
    reminded[task.id] = today;
    changed = true;
    notify("\u{1F514} Task reminder", task.text);
  }
  if (changed) {
    try { localStorage.setItem(REMINDED_KEY, JSON.stringify(reminded)); } catch { /* private mode */ }
  }
}

async function moveTask(tasks, index, delta) {
  const target = index + delta;
  if (target < 0 || target >= tasks.length) return;
  const reordered = tasks.map(t => t.id);
  [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
  await supabaseClient.rpc("reorder_tasks", { p_ordered_ids: reordered });
  await pullAndRender();
}

async function toggleTask(taskId, done) {
  const { data, error } = await supabaseClient.rpc("complete_task", { p_task_id: taskId, p_done: done });
  if (error) {
    console.error("complete_task failed", error);
    toast("⚠️ Couldn't save", error.message || "Check your connection and try again.");
  } else if (done && data) {
    let detail = `+${data.xp_gained ?? 0} XP`;
    if (data.new_level > data.old_level) detail += ` — Level up! Now level ${data.new_level}`;
    toast("✅ Nice work!", detail);
    for (const q of data.completed_quests ?? []) {
      toast("\u{1F31F} Quest complete!", `${q.desc} — +${q.bonus_xp} XP`);
    }
  }
  await pullAndRender();
}

// ---------- Pomodoro tab ----------

function renderPomodoro() {
  const s = getSettings();
  if (!pomodoro.running && !pomodoro.paused) pomodoro.secondsLeft = phaseSeconds(pomodoro.phase, s);
  const view = document.getElementById("view");
  view.innerHTML = `
    <div class="pane pomodoro-pane">
      <p class="phase-label" id="phase-label"></p>
      <div class="timer-ring"><span id="timer-text"></span></div>
      <div class="timer-controls">
        <button id="pomo-start-pause"></button>
        <button id="pomo-reset" class="secondary">Reset</button>
      </div>
      <p class="hint">The timer counts against a fixed end time, so it stays accurate even if your phone
      pauses this page when the screen locks — it catches up when you come back. The end-of-session alert can be
      late in that case, so the screen is kept awake while a session runs.</p>
      <details class="settings-details">
        <summary>Timer settings</summary>
        <label>Work (min) <input id="set-work" type="number" min="1" value="${s.workMin}"></label>
        <label>Short break (min) <input id="set-short" type="number" min="1" value="${s.shortBreakMin}"></label>
        <label>Long break (min) <input id="set-long" type="number" min="1" value="${s.longBreakMin}"></label>
        <label>Sessions before long break <input id="set-count" type="number" min="1" value="${s.sessionsBeforeLong}"></label>
        <label class="check-label"><input id="set-share" type="checkbox" ${s.shareTimer === false ? "" : "checked"}>
          One timer on all my devices</label>
        <button id="save-settings">Save settings</button>
      </details>
    </div>`;

  updateTimerDisplay();

  document.getElementById("pomo-start-pause").addEventListener("click", togglePomodoro);
  document.getElementById("pomo-reset").addEventListener("click", resetPomodoro);
  document.getElementById("save-settings").addEventListener("click", () => {
    const newSettings = {
      workMin: parseInt(document.getElementById("set-work").value, 10) || 25,
      shortBreakMin: parseInt(document.getElementById("set-short").value, 10) || 5,
      longBreakMin: parseInt(document.getElementById("set-long").value, 10) || 15,
      sessionsBeforeLong: parseInt(document.getElementById("set-count").value, 10) || 4,
      shareTimer: document.getElementById("set-share").checked,
    };
    saveSettings(newSettings);
    if (!pomodoro.running && !pomodoro.paused) {
      pomodoro.secondsLeft = phaseSeconds(pomodoro.phase, newSettings);
      updateTimerDisplay();
    }
  });

  if (window.Notification && Notification.permission === "default") {
    Notification.requestPermission();
  }
}

function phaseSeconds(phase, s) {
  if (phase === "work") return s.workMin * 60;
  if (phase === "short_break") return s.shortBreakMin * 60;
  return s.longBreakMin * 60;
}

function phaseLabel(phase) {
  if (phase === "work") return "Focus session";
  if (phase === "short_break") return "Short break";
  return "Long break";
}

function updateTimerDisplay() {
  const label = document.getElementById("phase-label");
  const text = document.getElementById("timer-text");
  const btn = document.getElementById("pomo-start-pause");
  if (!label || !text || !btn) return;
  label.textContent = phaseLabel(pomodoro.phase);
  const m = Math.floor(pomodoro.secondsLeft / 60).toString().padStart(2, "0");
  const sec = (pomodoro.secondsLeft % 60).toString().padStart(2, "0");
  text.textContent = `${m}:${sec}`;
  btn.textContent = pomodoro.running ? "Pause" : "Start";
}

function togglePomodoro() {
  if (pomodoro.running) pausePomodoro();
  else startPomodoro();
  publishTimer();
}

// `deadline`: run until then (another device's timer) instead of for
// secondsLeft from now; that device alerts for it, so no push is booked here.
function startPomodoro(deadline) {
  pomodoro.running = true;
  pomodoro.paused = false;
  pomodoro.deadline = deadline ?? Date.now() + pomodoro.secondsLeft * 1000;
  clearInterval(pomodoro.intervalId);
  pomodoro.intervalId = setInterval(tickPomodoro, 500);
  requestWakeLock();
  updateTimerDisplay();
  if (deadline === undefined) scheduleTimerPush();
}

// With push on, the cloud also sends "session over" at the end time, so it
// arrives even if the screen locks and the page is paused. Cancelled when
// paused, reset, or finished here first.
function scheduleTimerPush() {
  if (!pushOn || !pushEndpoint) return;
  const work = pomodoro.phase === "work";
  supabaseClient.rpc("schedule_timer_push", {
    p_endpoint: pushEndpoint,
    p_fire_at: new Date(pomodoro.deadline).toISOString(),
    p_title: work ? "Focus session complete!" : "Break's over",
    p_body: work ? "Open Pawmodoro to collect your XP and start your break." : "Back to it when you're ready.",
  }).then(({ error }) => { if (error && !isMissingFunction(error)) console.warn("timer push", error); });
}

function cancelTimerPush() {
  if (!pushOn || !pushEndpoint) return;
  supabaseClient.rpc("cancel_timer_push", { p_endpoint: pushEndpoint }).then(() => {});
}

function pausePomodoro() {
  settlePomodoro();
  cancelTimerPush();
  pomodoro.running = false;
  pomodoro.paused = true;
  clearInterval(pomodoro.intervalId);
  releaseWakeLock();
  updateTimerDisplay();
}

// Time left is always derived from the end time, never counted tick by tick.
function settlePomodoro() {
  pomodoro.secondsLeft = Math.max(0, Math.ceil((pomodoro.deadline - Date.now()) / 1000));
}

function resetPomodoro() {
  cancelTimerPush();
  clearInterval(pomodoro.intervalId);
  pomodoro.running = false;
  pomodoro.paused = false;
  releaseWakeLock();
  pomodoro.phase = "work";
  pomodoro.secondsLeft = phaseSeconds("work", getSettings());
  updateTimerDisplay();
  publishTimer();
}

// ---- One timer across devices ----
// Tells the other devices what this timer now is; a no-op with sharing off.
function publishTimer() {
  pomodoro.owner = true;
  pomodoro.takeover = null;
  if (!timerShared() || !supabaseClient) return;
  const timerState = { phase: pomodoro.phase, running: pomodoro.running, device: deviceId() };
  if (pomodoro.running) timerState.ends_at = pomodoro.deadline / 1000;
  else timerState.seconds_left = pomodoro.secondsLeft;
  supabaseClient.rpc("set_timer_state", { p_state: timerState }).then(({ data, error }) => {
    if (error) { if (!isMissingFunction(error)) console.warn("set_timer_state", error); return; }
    pomodoro.revSeen = Math.max(pomodoro.revSeen, Number(data) || 0);
  });
}

// Another device started, paused, reset or moved on the timer: do the same.
function followRemoteTimer(timer) {
  if (!timerShared() || !timer || !["work", "short_break", "long_break"].includes(timer.phase)) return;
  const rev = Number(timer.rev) || 0;
  if (rev <= pomodoro.revSeen) return;
  pomodoro.revSeen = rev;
  if (timer.device === deviceId()) return;
  const endsMs = (Number(timer.ends_at) || 0) * 1000;
  if (timer.running && endsMs < Date.now() - 60000) return; // left running somewhere long ago
  pomodoro.owner = false;
  pomodoro.takeover = null;
  if (pomodoro.running) cancelTimerPush(); // its device alerts for it now
  pomodoro.phase = timer.phase;
  if (timer.running && endsMs) {
    startPomodoro(endsMs);
    settlePomodoro();
  } else {
    clearInterval(pomodoro.intervalId);
    pomodoro.running = false;
    releaseWakeLock();
    pomodoro.secondsLeft = Number(timer.seconds_left) || phaseSeconds(timer.phase, getSettings());
    pomodoro.paused = pomodoro.secondsLeft !== phaseSeconds(timer.phase, getSettings());
  }
  updateTimerDisplay();
}

// A finished phase, counted once however many devices report it (the key).
async function creditPhase(phase, session, minutes) {
  const name = phase === "work" ? "record_pomodoro_completed" : "record_break_completed";
  const params = phase === "work" ? { p_minutes: minutes } : {};
  let { data, error } = await supabaseClient.rpc(name, { ...params, p_session: session });
  if (error && isMissingFunction(error)) ({ data, error } = await supabaseClient.rpc(name, params)); // older cloud
  return data;
}

async function takeOverTimer(pending) {
  if (!pending || pending !== pomodoro.takeover) return; // the owner reported back meanwhile
  pomodoro.takeover = null;
  const data = await creditPhase(pending.phase, pending.session, pending.minutes);
  if (pending.phase === "work" && data && !data.already_counted) toast("Focus session counted", `+${data.xp_gained} XP`);
  publishTimer();
  await pullAndRender();
}

async function tickPomodoro() {
  if (!pomodoro.running || pomodoro.completing) return;
  settlePomodoro();
  if (pomodoro.secondsLeft <= 0) {
    pomodoro.completing = true; // a catch-up tick can race the interval; only complete once
    clearInterval(pomodoro.intervalId);
    pomodoro.running = false;
    releaseWakeLock();
    try {
      await onPhaseComplete();
    } finally {
      pomodoro.completing = false;
    }
  }
  updateTimerDisplay();
}

// Keeps the screen on while a session runs, so the page isn't paused by a
// screen lock. Best-effort: unsupported / denied is fine.
async function requestWakeLock() {
  try {
    if ("wakeLock" in navigator && !wakeLock) {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => { wakeLock = null; });
    }
  } catch {
    wakeLock = null;
  }
}

function releaseWakeLock() {
  try { if (wakeLock) wakeLock.release(); } catch { /* already released */ }
  wakeLock = null;
}

async function onPhaseComplete() {
  const s = getSettings();
  // Finishing late means the page was asleep and the push already told you;
  // then just show it in the app instead of buzzing a second time.
  const pushed = pushOn && Date.now() - pomodoro.deadline > 15000;
  if (!pushed) cancelTimerPush();
  const announce = pushed ? toast : notify;
  const session = `${pomodoro.phase}:${Math.round(pomodoro.deadline / 1000)}`;
  // Following another device's timer: it counts the session, unless it
  // hasn't reported back after a while (then this phone does).
  const countsHere = pomodoro.owner || !timerShared();
  if (!countsHere) {
    const pending = { session, phase: pomodoro.phase, minutes: s.workMin };
    pomodoro.takeover = pending;
    setTimeout(() => takeOverTimer(pending), TIMER_TAKEOVER_MS);
  }
  if (pomodoro.phase === "work") {
    pomodoro.sessionCount += 1;
    const data = countsHere ? await creditPhase("work", session, s.workMin) : null;
    announce("Focus session complete!", data && !data.already_counted ? `+${data.xp_gained} XP` : "Nice work!");
    if (data) {
      for (const q of data.completed_quests ?? []) {
        toast("\u{1F31F} Quest complete!", `${q.desc} — +${q.bonus_xp} XP`);
      }
    }
    pomodoro.phase = (pomodoro.sessionCount % s.sessionsBeforeLong === 0) ? "long_break" : "short_break";
  } else {
    if (countsHere) await creditPhase(pomodoro.phase, session);
    announce("Break's over", "Back to it when you're ready.");
    pomodoro.phase = "work";
  }
  pomodoro.paused = false;
  pomodoro.secondsLeft = phaseSeconds(pomodoro.phase, s);
  updateTimerDisplay();
  if (countsHere) publishTimer();
  await pullAndRender();
}

function notify(title, body) {
  toast(title, body);
  if (navigator.vibrate) navigator.vibrate([300, 150, 300, 150, 600]);
  if (!window.Notification || Notification.permission !== "granted") return;
  const options = { body, silent: false, vibrate: [300, 150, 300, 150, 600], icon: "icons/icon_256.png", badge: "icons/icon_64.png" };
  // Chrome on Android refuses `new Notification()` ("Illegal constructor");
  // there, notifications have to come from the service worker.
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.ready
      .then(reg => reg.showNotification(title, options))
      .catch(() => { try { new Notification(title, options); } catch { /* toast already shown */ } });
  } else {
    try { new Notification(title, options); } catch { /* toast already shown */ }
  }
}

// ---------- Progress tab ----------

function progressKey() {
  return JSON.stringify([state, householdState]);
}

function localISODate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// The Tuesday that starts the quest week `now` is in, "YYYY-MM-DD" (the same
// rule as gamification.week_start_for and week_start_for() in schema.sql).
function weekStartFor(now) {
  const start = new Date(now);
  start.setDate(start.getDate() - ((now.getDay() + 6) % 7 - 1 + 7) % 7);
  return localISODate(start);
}

function formatMinutes(min) {
  const h = Math.floor(min / 60), m = min % 60;
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
}

// Focus minutes for the last 14 days as a small bar chart, plus this week's
// (since Tuesday, the quest-week reset) totals.
function renderHistory() {
  const rows = new Map((state.history ?? []).map(r => [r.day, r]));
  const now = new Date();
  const days = [];
  for (let i = 13; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    days.push({ key: localISODate(d), label: "MTWTFSS"[(d.getDay() + 6) % 7], min: rows.get(localISODate(d))?.focus_min ?? 0, today: i === 0 });
  }
  const peak = Math.max(1, ...days.map(d => d.min));
  const weekKey = weekStartFor(now);
  const week = { pomodoros: 0, focus_min: 0, tasks: 0 };
  for (const r of state.history ?? []) {
    if (r.day >= weekKey) { week.pomodoros += r.pomodoros; week.focus_min += r.focus_min; week.tasks += r.tasks; }
  }
  return `
    <div class="history-chart">${days.map(d => `
      <div class="history-col">
        <span class="history-val">${d.min || ""}</span>
        <div class="history-bar" style="height:${d.min ? Math.max(3, Math.round(80 * d.min / peak)) : 0}px"></div>
        <span class="history-day${d.today ? " today" : ""}">${d.label}</span>
      </div>`).join("")}</div>
    <p class="hint">This week (since Tuesday): ${week.pomodoros} sessions &middot; ${formatMinutes(week.focus_min)} focused &middot; ${week.tasks} tasks</p>`;
}

function renderHouseholdSummary() {
  if (!householdState) return "";
  let sessions = 0, minutes = 0, tasks = 0;
  const lines = householdState.members.map(m => {
    sessions += m.week_pomodoros; minutes += m.week_focus_min; tasks += m.week_tasks;
    const { level } = levelFromXp(m.xp);
    return `<p class="hint"><strong>${escapeHtml(m.name || "?")}${m.is_me ? " (you)" : ""}</strong> — Lv.${level}${m.streak >= 2 ? ` \u{1F525}${m.streak}` : ""}
      &middot; this week: ${m.week_pomodoros} sessions, ${formatMinutes(m.week_focus_min)}</p>`;
  }).join("");
  return `
    <section class="card">
      <h2>\u{1F3E0} Household</h2>
      ${lines}
      <p class="hint"><strong>Together this week:</strong> ${sessions} sessions &middot; ${formatMinutes(minutes)} focused &middot; ${tasks} tasks</p>
    </section>`;
}

function renderProgress() {
  if (!state) return;
  renderKeys.progress = progressKey();
  const { level, xpInto, xpNeeded } = levelFromXp(state.xp);
  const title = titleForLevel(level);
  const daysLeft = daysUntilWeeklyReset(new Date());
  const view = document.getElementById("view");
  view.innerHTML = `
    <div class="pane">
      <section class="card">
        <h2>\u{1F3C6} Level ${level} — ${title}</h2>
        <div class="xp-bar"><div class="xp-bar-fill" style="width:${Math.round(100 * xpInto / xpNeeded)}%"></div></div>
        <p class="hint">${xpInto} / ${xpNeeded} XP</p>
        <p class="hint">${state.current_streak >= 2 ? `\u{1F525} ${state.current_streak}-day streak (best: ${state.longest_streak})` : "Complete something today to start a streak"}</p>
        <p class="hint">${state.total_pomodoros} sessions completed &nbsp;&middot;&nbsp; ${state.total_tasks} tasks completed</p>
      </section>
      <section class="card">
        <h2>Focus minutes, last 14 days</h2>
        ${renderHistory()}
      </section>
      ${renderHouseholdSummary()}
      <section class="card">
        <h2>Today's quests</h2>
        ${renderQuestList(state.quests)}
      </section>
      <section class="card">
        <h2>This week's quests</h2>
        <p class="hint">Resets in ${daysLeft} day${daysLeft === 1 ? "" : "s"} (Tuesday)</p>
        ${renderQuestList(state.weekly_quests)}
      </section>
    </div>`;
}

function renderQuestList(quests) {
  if (!quests || quests.length === 0) return `<p class="hint">No quests.</p>`;
  return `<ul class="quest-list">${quests.map(q => `
    <li class="${q.completed ? "done" : ""}">
      ${q.completed ? "☑" : "☐"} ${QUEST_ICONS[q.kind] ?? "•"} ${escapeHtml(q.desc)} (${q.progress}/${q.target})
    </li>`).join("")}</ul>`;
}

function daysUntilWeeklyReset(d) {
  const pyWeekday = (d.getDay() + 6) % 7; // JS getDay(): Sun=0..Sat=6 -> Mon=0..Sun=6
  return 7 - ((pyWeekday - 1 + 7) % 7);
}

// ---------- Shared tab (household to-do list) ----------

function sharedKey() {
  return JSON.stringify([householdState, householdSupported]);
}

async function sharedCall(name, params) {
  const { data, error } = await supabaseClient.rpc(name, params);
  if (error) {
    toast("⚠️ Couldn't do that", error.message || "Check your connection and try again.");
    return { ok: false };
  }
  return { ok: true, data };
}

async function reloadShared() {
  await refreshHousehold(true);
  if (activeTab === "shared") renderShared();
}

// Schedule of a shared item: "once" (optionally with a date/time), or it repeats
// daily / weekly on a weekday ("every Tuesday") / monthly on a day of the month.
// Same rules as shared_schedule.py and shared_last_occurrence() in schema.sql.
const WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const SHARED_SCHEDULE_DEFAULT = { recurrence: "once", weekday: 1, month_day: 1, due_date: "", due_time: "" };
let sharedDraftSchedule = { ...SHARED_SCHEDULE_DEFAULT };

function ordinal(n) {
  const suffix = (n % 100 >= 11 && n % 100 <= 13) ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th");
  return `${n}${suffix}`;
}

function sharedScheduleLabel(task) {
  const at = task.due_time ? ` at ${task.due_time}` : "";
  switch (task.recurrence) {
    case "daily": return `\u{1F501} Every day${at}`;
    case "weekly": return task.weekday == null ? "" : `\u{1F501} Every ${WEEKDAY_NAMES[task.weekday]}${at}`;
    case "monthly": return task.month_day == null ? "" : `\u{1F501} Monthly on the ${ordinal(task.month_day)}${at}`;
    default:
      if (!task.due_date) return "";
      const [y, m, d] = task.due_date.split("-").map(Number);
      const day = new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: y === new Date().getFullYear() ? undefined : "numeric" });
      return `\u{1F4C5} ${day}${at}`;
  }
}

function sharedIsOverdue(task) {
  if (task.done || task.recurrence !== "once" || !task.due_date) return false;
  const [y, m, d] = task.due_date.split("-").map(Number);
  const [hh, mm] = (task.due_time || "23:59").split(":").map(Number);
  return new Date() > new Date(y, m - 1, d, hh, mm);
}

// The parameters shared_add_task takes for a schedule. Empty for a plain item,
// so plain items still work against a cloud schema that predates schedules.
function sharedScheduleParams(sch) {
  const plain = sch.recurrence === "once" && !sch.due_date && !sch.due_time;
  if (plain) return {};
  const once = sch.recurrence === "once";
  return {
    p_recurrence: sch.recurrence,
    p_weekday: sch.recurrence === "weekly" ? Number(sch.weekday) : null,
    p_month_day: sch.recurrence === "monthly" ? Number(sch.month_day) : null,
    // a time on its own means "today"
    p_due_date: once ? (sch.due_date || (sch.due_time ? new Date().toLocaleDateString("sv") : null)) : null,
    p_due_time: sch.due_time || null,
  };
}

async function moveSharedTask(tasks, index, delta) {
  const target = index + delta;
  if (target < 0 || target >= tasks.length) return;
  const ids = tasks.map(t => t.id);
  [ids[index], ids[target]] = [ids[target], ids[index]];
  await sharedCall("shared_reorder", { p_ordered_ids: ids });
  await reloadShared();
}

function renderShared() {
  const restoreDraft = keepDraft("shared-text");
  renderKeys.shared = sharedKey();
  const view = document.getElementById("view");

  if (!householdSupported) {
    view.innerHTML = `<div class="pane"><section class="card"><h2>\u{1F3E0} Shared list</h2>
      <p class="hint">The cloud database needs updating for shared lists. Re-run <code>supabase/schema.sql</code>
      in the Supabase SQL Editor (see MOBILE_SYNC.md), then reopen this app.</p></section></div>`;
    return;
  }

  if (!householdState) {
    view.innerHTML = `
      <div class="pane">
        <section class="card">
          <h2>\u{1F3E0} Shared list</h2>
          <p class="hint">Share a to-do list (groceries, chores…) with your partner. One of you creates the
          household and gives the other the invite code. Your notes, checklist and XP stay private.</p>
        </section>
        <section class="card">
          <h2>Create a household</h2>
          <div class="stack"><input id="hh-create-name" type="text" placeholder="Your name (shown to your partner)">
          <button id="hh-create">Create</button></div>
        </section>
        <section class="card">
          <h2>Join with an invite code</h2>
          <div class="stack"><input id="hh-join-code" type="text" placeholder="Invite code" autocapitalize="characters" autocorrect="off">
          <input id="hh-join-name" type="text" placeholder="Your name (shown to your partner)">
          <button id="hh-join">Join</button></div>
        </section>
      </div>`;
    document.getElementById("hh-create").addEventListener("click", async () => {
      const name = document.getElementById("hh-create-name").value.trim();
      if (!name) { toast("Add your name", "Your partner will see it next to what you tick off."); return; }
      const r = await sharedCall("household_create", { p_name: name });
      if (r.ok) { householdState = r.data; renderShared(); }
    });
    document.getElementById("hh-join").addEventListener("click", async () => {
      const code = document.getElementById("hh-join-code").value.trim();
      const name = document.getElementById("hh-join-name").value.trim();
      if (!code || !name) { toast("Almost there", "Enter the invite code and your name."); return; }
      const r = await sharedCall("household_join", { p_code: code, p_name: name });
      if (r.ok) { householdState = r.data; renderShared(); }
    });
    return;
  }

  const h = householdState;
  const names = h.members.map(m => escapeHtml(m.name || "?"));
  view.innerHTML = `
    <div class="pane">
      <p class="hint">${names.length < 2
        ? `\u{1F3E0} Just you so far — give your partner this invite code: <strong>${escapeHtml(h.invite_code)}</strong>`
        : `\u{1F3E0} ${names.join(" &amp; ")} — invite code <strong>${escapeHtml(h.invite_code)}</strong>`}</p>
      <ul id="shared-list" class="task-list"></ul>
      <div class="add-row">
        <input id="shared-text" type="text" placeholder="e.g. Milk">
        <button id="shared-add">Add</button>
      </div>
      <div class="add-row shared-schedule">
        <select id="shared-repeat" title="Repeat">
          <option value="once">One time</option>
          <option value="daily">Every day</option>
          <option value="weekly">Every week</option>
          <option value="monthly">Every month</option>
        </select>
        <select id="shared-weekday" title="Day of the week">
          ${WEEKDAY_NAMES.map((n, i) => `<option value="${i}">on ${n}</option>`).join("")}
        </select>
        <input id="shared-monthday" type="number" min="1" max="31" title="Day of the month" placeholder="day">
        <input id="shared-date" type="date" title="Date (optional)">
        <input id="shared-time" type="time" title="Time (optional)">
      </div>
      <div class="add-row shared-actions">
        <button id="shared-clear" class="secondary-btn">Clear completed</button>
        <button id="shared-leave" class="secondary-btn">Leave household</button>
      </div>
    </div>`;
  const list = document.getElementById("shared-list");
  h.tasks.forEach((task, index) => {
    const li = document.createElement("li");
    li.className = "task-item" + (task.done ? " done" : "");
    const schedule = sharedScheduleLabel(task);
    li.innerHTML = `
      <div class="reorder-col">
        <button class="reorder-btn" data-dir="up" title="Move up" ${index === 0 ? "disabled" : ""}>▲</button>
        <button class="reorder-btn" data-dir="down" title="Move down" ${index === h.tasks.length - 1 ? "disabled" : ""}>▼</button>
      </div>
      <label>
        <input type="checkbox" ${task.done ? "checked" : ""}>
        <span>${escapeHtml(task.text)}${schedule ? ` <em>${escapeHtml(schedule)}${sharedIsOverdue(task) ? " ⚠ overdue" : ""}</em>` : ""}${task.assigned_to ? ` <em>→ ${task.assigned_to_me ? "you" : escapeHtml(task.assigned_name ?? "?")}</em>` : ""}${task.done && task.done_by_name ? ` <em>✓ ${escapeHtml(task.done_by_name)}</em>` : ""}</span>
      </label>
      <button class="rename-btn assign-btn" title="Whose turn (tap to change)">\u{1F464}</button>
      <button class="remove-btn" title="Remove">✕</button>`;
    li.querySelector("input").addEventListener("change", async e => {
      await sharedCall("shared_set_done", { p_id: task.id, p_done: e.target.checked });
      await reloadShared();
    });
    li.querySelector(".remove-btn").addEventListener("click", async () => {
      await sharedCall("shared_remove_task", { p_id: task.id });
      await reloadShared();
    });
    // Anyone → each member in turn → anyone again. Only the assignee gets its reminders.
    li.querySelector(".assign-btn").addEventListener("click", async () => {
      const members = (h.members ?? []).filter(m => m.user_id);
      const at = members.findIndex(m => m.user_id === task.assigned_to);
      const next = at + 1 < members.length ? members[at + 1] : null;
      const { ok } = await sharedCall("shared_assign", { p_id: task.id, p_user: next ? next.user_id : null });
      if (ok) toast(next ? `\u{1F464} ${next.is_me ? "Your" : `${next.name ?? "Their"}'s`} turn` : "\u{1F464} Anyone", task.text);
      await reloadShared();
    });
    li.querySelectorAll(".reorder-btn").forEach(btn => {
      btn.addEventListener("click", () => moveSharedTask(h.tasks, index, btn.dataset.dir === "up" ? -1 : 1));
    });
    list.appendChild(li);
  });

  // The schedule pickers keep their values across re-renders (a poll can redraw
  // the tab while you're still choosing), so they live in sharedDraftSchedule.
  const pickers = {
    recurrence: document.getElementById("shared-repeat"), weekday: document.getElementById("shared-weekday"),
    month_day: document.getElementById("shared-monthday"), due_date: document.getElementById("shared-date"),
    due_time: document.getElementById("shared-time"),
  };
  const syncPickers = () => {
    pickers.weekday.hidden = sharedDraftSchedule.recurrence !== "weekly";
    pickers.month_day.hidden = sharedDraftSchedule.recurrence !== "monthly";
    pickers.due_date.hidden = sharedDraftSchedule.recurrence !== "once";
  };
  for (const [key, el] of Object.entries(pickers)) {
    el.value = sharedDraftSchedule[key] ?? "";
    el.addEventListener("input", () => { sharedDraftSchedule[key] = el.value; syncPickers(); });
  }
  syncPickers();
  const add = async () => {
    const input = document.getElementById("shared-text");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    const params = { p_text: text, ...sharedScheduleParams(sharedDraftSchedule) };
    sharedDraftSchedule = { ...SHARED_SCHEDULE_DEFAULT };  // a one-off grocery shouldn't inherit the last chore's schedule
    await sharedCall("shared_add_task", params);
    await reloadShared();
    document.getElementById("shared-text")?.focus();
  };
  document.getElementById("shared-add").addEventListener("click", add);
  document.getElementById("shared-text").addEventListener("keydown", e => { if (e.key === "Enter") add(); });
  document.getElementById("shared-clear").addEventListener("click", async () => {
    await sharedCall("shared_clear_done", {});
    await reloadShared();
  });
  document.getElementById("shared-leave").addEventListener("click", async () => {
    if (!confirm("Leave this household? You'll stop seeing the shared list. If you're the last one in it, the list is deleted.")) return;
    const r = await sharedCall("household_leave", {});
    if (r.ok) { householdState = null; renderShared(); }
  });
  restoreDraft();
}

// ---------- Calendar tab ----------
// What's on each day -- dated tasks, scheduled shared items and the
// calendar's own events (trips, appointments) -- and the files attached to
// them (tickets, reservations, receipts, photos), kept in the cloud's
// "attachments" Storage bucket. Which day a thing lands on is the desktop's
// rule (calendar_view.py); tests/parity checks taskOnDay / taskDoneOn /
// sharedOnDay / sharedDoneOn against it.

const ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024;  // attachment_max_bytes() in schema.sql
const ATTACHMENT_TRASH_MS = 3600 * 1000;        // a removed item's files wait this long (an undo brings them back)
const CAL_KIND_ICONS = { event: "\u{1F4C5}", task: "✅", shared: "\u{1F3E0}" };
const CAL_KIND_ORDER = { event: 0, shared: 1, task: 2 };
let calendarMonth = null;   // { year, month (0-11) } on screen
let calendarDay = null;     // "YYYY-MM-DD" selected
let calendarOpen = null;    // "kind:id" of the entry whose details are open
let calendarForm = null;    // { kind: "event" | "task" | "shared", event? } -- the add / edit form showing
const purgedAttachments = new Set();

function isoToDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function randomHexId() {
  return Array.from(crypto.getRandomValues(new Uint8Array(6)), b => b.toString(16).padStart(2, "0")).join("");
}

// The most recent day on or before d that a repeating shared item came due
// (shared_schedule.last_occurrence), or null for a one-off.
function sharedOccurrence(task, d) {
  if (task.recurrence === "daily") return localISODate(d);
  if (task.recurrence === "weekly") {
    const back = ((d.getDay() + 6) % 7 - (task.weekday ?? 0) + 7) % 7;
    return localISODate(new Date(d.getFullYear(), d.getMonth(), d.getDate() - back));
  }
  if (task.recurrence === "monthly") return monthOccurrence(task.month_day ?? 1, d);
  return null;
}

function taskOnDay(task, day) {
  if (task.source === "wishlist") return false;
  if (task.recurrence === "once") return (task.due_date || "").slice(0, 10) === day;
  if (task.recurrence === "monthly") return monthOccurrence(task.month_day || 1, isoToDate(day)) === day;
  if (task.recurrence === "weekday") return task.weekday === (isoToDate(day).getDay() + 6) % 7;
  return false;
}

function taskDoneOn(task, day) {
  return (task.done_days || []).includes(day) || (task.recurrence === "once" && Boolean(task.completed_today));
}

function sharedOnDay(task, day) {
  const recurrence = task.recurrence || "once";
  if (recurrence === "once") return (task.due_date || "").slice(0, 10) === day;
  if (recurrence === "weekly" || recurrence === "monthly") return sharedOccurrence(task, isoToDate(day)) === day;
  return false;
}

// A shared item only knows whether its current occurrence is ticked.
function sharedDoneOn(task, day, today) {
  if (!task.done) return false;
  if ((task.recurrence || "once") === "once") return true;
  return sharedOccurrence(task, isoToDate(today)) === day;
}

function calendarEntriesOn(day, today = localISODate(new Date())) {
  const out = [];
  const addEvent = (event, shared) => {
    const start = event.start_date, end = event.end_date || event.start_date;
    if (!(start <= day && day <= end)) return;
    out.push({ kind: "event", id: event.id, title: event.title, item: event, shared, done: false,
      time: day === start ? event.start_time || null : null,
      span: start === end ? null : day === start ? "start" : day === end ? "end" : "middle" });
  };
  (state?.events ?? []).forEach(e => addEvent(e, false));
  (householdState?.events ?? []).forEach(e => addEvent(e, true));
  for (const task of householdState?.tasks ?? []) {
    if (sharedOnDay(task, day)) out.push({ kind: "shared", id: task.id, title: task.text, item: task, shared: true,
      time: task.due_time || null, done: sharedDoneOn(task, day, today), span: null });
  }
  for (const task of state?.checklist ?? []) {
    if (taskOnDay(task, day)) out.push({ kind: "task", id: task.id, title: task.text, item: task, shared: false,
      time: task.reminder_time || null, done: taskDoneOn(task, day), span: null });
  }
  return out.sort((a, b) => (a.time !== null) - (b.time !== null) || (a.time || "").localeCompare(b.time || "")
    || CAL_KIND_ORDER[a.kind] - CAL_KIND_ORDER[b.kind] || a.title.toLowerCase().localeCompare(b.title.toLowerCase()));
}

function attachmentsFor(kind, id) {
  return [...(state?.attachments ?? []), ...(householdState?.attachments ?? [])]
    .filter(a => a.item_kind === kind && a.item_id === id);
}

function humanSize(size) {
  let n = Number(size) || 0;
  for (const unit of ["B", "KB", "MB", "GB"]) {
    if (n < 1024 || unit === "GB") return unit === "B" ? `${n} B` : `${n.toFixed(1)} ${unit}`;
    n /= 1024;
  }
}

function describeEvent(event) {
  const fmt = iso => {
    const d = isoToDate(iso);
    return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short",
      year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
  };
  const end = event.end_date || event.start_date;
  let text = fmt(event.start_date) + (event.start_time ? `, ${event.start_time}` : "");
  if (end !== event.start_date) text += ` – ${fmt(end)}` + (event.end_time ? `, ${event.end_time}` : "");
  else if (event.end_time) text += `–${event.end_time}`;
  return text;
}

function calendarKey() {
  return JSON.stringify([state?.checklist, state?.events, state?.attachments, householdState]);
}

function calendarCall(name, params, failTitle) {
  return supabaseClient.rpc(name, params).then(({ data, error }) => {
    if (error) {
      toast(failTitle, isMissingFunction(error)
        ? "The cloud database needs updating: re-run supabase/schema.sql (see MOBILE_SYNC.md)."
        : error.message || "Check your connection and try again.");
      return { ok: false };
    }
    return { ok: true, data };
  });
}

async function reloadCalendar() {
  await pullAndRender();
  await refreshHousehold(true);
  if (activeTab === "calendar") renderCalendar();
}

function renderCalendar() {
  renderKeys.calendar = calendarKey();
  const today = localISODate(new Date());
  calendarDay ??= today;
  calendarMonth ??= { year: isoToDate(calendarDay).getFullYear(), month: isoToDate(calendarDay).getMonth() };
  const { year, month } = calendarMonth;
  const supported = !state || "events" in state;
  const first = new Date(year, month, 1);
  const start = new Date(year, month, 1 - (first.getDay() + 6) % 7);
  const lastDay = new Date(year, month + 1, 0);
  const cells = [];
  for (let d = new Date(start); d <= lastDay || cells.length % 7; d.setDate(d.getDate() + 1)) {
    const iso = localISODate(d);
    const inMonth = d.getMonth() === month;
    const entries = inMonth ? calendarEntriesOn(iso, today) : [];
    const dots = entries.slice(0, 3).map(e => `<i class="cal-dot ${e.kind}${e.done ? " done" : ""}"></i>`).join("")
      + (entries.length > 3 ? `<i class="cal-more">+</i>` : "");
    cells.push(`<button class="cal-cell${inMonth ? "" : " other"}${iso === today ? " today" : ""}${iso === calendarDay ? " selected" : ""}"
      data-day="${iso}"><span class="cal-num">${d.getDate()}</span><span class="cal-dots">${dots}</span></button>`);
  }
  const dayDate = isoToDate(calendarDay);
  const dayTitle = dayDate.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long",
    year: dayDate.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
  const entries = calendarEntriesOn(calendarDay, today);
  const view = document.getElementById("view");
  view.innerHTML = `
    <div class="pane">
      ${supported ? "" : `<p class="hint">Events and files need the cloud database updated: re-run
        <code>supabase/schema.sql</code> (see MOBILE_SYNC.md). Dated tasks still show.</p>`}
      <div class="cal-nav">
        <button id="cal-prev" title="Previous month">‹</button>
        <strong>${escapeHtml(first.toLocaleDateString(undefined, { month: "long", year: "numeric" }))}</strong>
        <button id="cal-next" title="Next month">›</button>
        <button id="cal-today" class="secondary-btn">Today</button>
      </div>
      <div class="cal-grid">
        ${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(d => `<span class="cal-dow">${d}</span>`).join("")}
        ${cells.join("")}
      </div>
      <h3 class="section-heading">${escapeHtml(dayTitle)}${calendarDay === today ? " (today)" : ""}</h3>
      <ul id="cal-day-list" class="task-list"></ul>
      <div class="add-row cal-add">
        ${supported ? `<button data-add="event">+ Event</button>` : ""}
        <button data-add="task">+ Task</button>
        ${householdState ? `<button data-add="shared">+ Shared</button>` : ""}
      </div>
      <div id="cal-form"></div>
    </div>`;

  document.getElementById("cal-prev").addEventListener("click", () => stepCalendarMonth(-1));
  document.getElementById("cal-next").addEventListener("click", () => stepCalendarMonth(1));
  document.getElementById("cal-today").addEventListener("click", () => {
    calendarDay = today;
    calendarMonth = null;
    calendarOpen = null;
    renderCalendar();
  });
  view.querySelectorAll(".cal-cell").forEach(cell => cell.addEventListener("click", () => {
    calendarDay = cell.dataset.day;
    const d = isoToDate(calendarDay);
    calendarMonth = { year: d.getFullYear(), month: d.getMonth() };
    calendarOpen = null;
    renderCalendar();
  }));
  view.querySelectorAll("[data-add]").forEach(btn => btn.addEventListener("click", () => {
    calendarForm = { kind: btn.dataset.add };
    renderCalendarForm();
  }));

  const list = document.getElementById("cal-day-list");
  if (!entries.length) list.innerHTML = `<li class="hint">Nothing on this day.</li>`;
  for (const entry of entries) list.appendChild(calendarEntryItem(entry));
  renderCalendarForm();
}

function stepCalendarMonth(delta) {
  const { year, month } = calendarMonth;
  const target = new Date(year, month + delta, 1);
  const day = Math.min(isoToDate(calendarDay).getDate(), new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate());
  calendarDay = localISODate(new Date(target.getFullYear(), target.getMonth(), day));
  calendarMonth = { year: target.getFullYear(), month: target.getMonth() };
  calendarOpen = null;
  renderCalendar();
}

function calendarEntryItem(entry) {
  const key = `${entry.kind}:${entry.id}`;
  const files = attachmentsFor(entry.kind, entry.id);
  const li = document.createElement("li");
  li.className = "task-item cal-entry" + (entry.done ? " done" : "");
  li.dataset.key = key;
  const label = (entry.time ? `${entry.time} ` : "") + entry.title;
  li.innerHTML = `
    <button class="cal-entry-head">
      <span>${CAL_KIND_ICONS[entry.kind]} ${escapeHtml(entry.span === "middle" || entry.span === "end" ? `… ${entry.title}` : label)}</span>
      ${entry.kind === "event" && entry.shared ? `<em>\u{1F3E0}</em>` : ""}
      ${files.length ? `<em>\u{1F4CE}${files.length}</em>` : ""}
    </button>`;
  li.querySelector(".cal-entry-head").addEventListener("click", () => {
    calendarOpen = calendarOpen === key ? null : key;
    renderCalendar();
  });
  if (calendarOpen === key) li.appendChild(calendarDetails(entry, files));
  return li;
}

function calendarDetails(entry, files) {
  const box = document.createElement("div");
  box.className = "cal-details";
  const lines = [];
  if (entry.kind === "event") {
    lines.push(escapeHtml(describeEvent(entry.item)));
    if (entry.shared) {
      const by = entry.item.created_by_me ? "you" : entry.item.created_by_name || "someone";
      lines.push(`\u{1F3E0} On the household's calendar (added by ${escapeHtml(by)})`);
    }
  } else {
    lines.push(entry.kind === "task" ? "Task" : "Shared item");
  }
  if (entry.item.note) lines.push(escapeHtml(entry.item.note).replace(/\n/g, "<br>"));
  const filesAllowed = state && "attachments" in state;
  box.innerHTML = `
    <p>${lines.join("<br>")}</p>
    ${entry.kind === "event" ? `<div class="add-row"><button class="secondary-btn" data-act="edit">Edit</button>
      <button class="secondary-btn" data-act="delete">Delete</button></div>` : ""}
    <ul class="cal-files">${files.map(f => `
      <li data-id="${escapeHtml(f.id)}">
        <a target="_blank" rel="noopener" data-path="${escapeHtml(f.path)}">${f.mime?.startsWith("image/")
          ? `<img class="cal-thumb" alt="">` : "\u{1F4C4}"} ${escapeHtml(f.file_name)}</a>
        <em>${humanSize(f.size)}${f.added_by_me === false && f.added_by_name ? ` · ${escapeHtml(f.added_by_name)}` : ""}</em>
        <button class="remove-btn" title="Remove this file">✕</button>
      </li>`).join("")}</ul>
    ${filesAllowed ? `<label class="cal-attach">\u{1F4CE} Add a file or photo<input type="file" multiple hidden></label>` : ""}`;
  box.querySelector('[data-act="edit"]')?.addEventListener("click", () => {
    calendarForm = { kind: "event", event: entry.item, shared: entry.shared };
    renderCalendarForm();
  });
  box.querySelector('[data-act="delete"]')?.addEventListener("click", async () => {
    const extra = files.length ? ` and its ${files.length} file${files.length === 1 ? "" : "s"}` : "";
    if (!confirm(`Delete “${entry.title}”${extra}?${entry.shared ? " It's removed for everyone in your household." : ""}`)) return;
    calendarOpen = null;
    await calendarCall("remove_event", { p_id: entry.id }, "⚠️ Couldn't delete it");
    await reloadCalendar();
  });
  box.querySelectorAll(".cal-files li").forEach(row => {
    row.querySelector(".remove-btn").addEventListener("click", async () => {
      const file = files.find(f => f.id === row.dataset.id);
      if (!confirm(`Remove “${file.file_name}”?${entry.shared ? " It's removed for everyone in your household." : ""}`)) return;
      await removeAttachment(file);
      await reloadCalendar();
    });
  });
  box.querySelector(".cal-attach input")?.addEventListener("change", async e => {
    const picked = Array.from(e.target.files || []);
    for (const file of picked) {
      toast("\u{1F4CE} Uploading…", file.name || "photo");
      await uploadAttachment(entry.kind, entry.id, file);
    }
    await reloadCalendar();
  });
  signFileLinks(box, files);
  return box;
}

// The bucket is private: links (and thumbnails) are signed for an hour.
async function signFileLinks(box, files) {
  const paths = files.map(f => f.path).filter(Boolean);
  if (!paths.length) return;
  const { data, error } = await supabaseClient.storage.from("attachments").createSignedUrls(paths, 3600);
  if (error || !Array.isArray(data)) return;
  for (const signed of data) {
    if (!signed?.signedUrl) continue;
    box.querySelectorAll("a[data-path]").forEach(a => {
      if (a.dataset.path !== signed.path) return;
      a.href = signed.signedUrl;
      const img = a.querySelector("img");
      if (img) img.src = signed.signedUrl;
    });
  }
}

async function uploadAttachment(kind, itemId, file) {
  if (file.size > ATTACHMENT_MAX_BYTES) {
    toast("⚠️ Too big", `${file.name}: files can be at most ${ATTACHMENT_MAX_BYTES / 1024 / 1024} MB.`);
    return false;
  }
  const id = randomHexId();
  const mime = file.type || "application/octet-stream";
  const registered = await calendarCall("add_attachment", { p_id: id, p_item_kind: kind, p_item_id: itemId,
    p_file_name: file.name || "photo.jpg", p_mime: mime, p_size: file.size }, "⚠️ Couldn't add the file");
  if (!registered.ok) return false;
  const { error } = await supabaseClient.storage.from("attachments")
    .upload(registered.data.path, file, { upsert: true, contentType: mime });
  if (error) {
    toast("⚠️ Couldn't upload the file", error.message || "Check your connection and try again.");
    await supabaseClient.rpc("remove_attachment", { p_id: id });  // or it would count against the space
    return false;
  }
  return (await calendarCall("attachment_uploaded", { p_id: id }, "⚠️ Couldn't add the file")).ok;
}

// Storage first: its policy needs the attachments row to still be there.
async function removeAttachment(file) {
  const { error } = await supabaseClient.storage.from("attachments").remove([file.path]);
  if (error) {
    toast("⚠️ Couldn't remove the file", error.message || "Check your connection and try again.");
    return false;
  }
  return (await calendarCall("remove_attachment", { p_id: file.id }, "⚠️ Couldn't remove the file")).ok;
}

// Files whose task / item / event was removed over an hour ago (sync_pull's
// attachments_trash): deleted for good. The desktop does the same.
async function purgeTrashedAttachments() {
  const now = Date.now();
  for (const entry of state?.attachments_trash ?? []) {
    if (purgedAttachments.has(entry.id) || now - new Date(entry.trashed_at).getTime() < ATTACHMENT_TRASH_MS) continue;
    purgedAttachments.add(entry.id);
    const { error } = await supabaseClient.storage.from("attachments").remove([entry.path]);
    if (!error) await supabaseClient.rpc("remove_attachment", { p_id: entry.id });
  }
}

function renderCalendarForm() {
  const box = document.getElementById("cal-form");
  if (!box) return;
  if (!calendarForm) {
    box.innerHTML = "";
    return;
  }
  const { kind } = calendarForm;
  if (kind === "event") {
    const e = calendarForm.event || {};
    const allDay = !e.start_time;
    box.innerHTML = `
      <section class="card">
        <h2>${e.id ? "Edit event" : "New event"}</h2>
        <div class="stack">
          <input id="ev-title" type="text" maxlength="200" placeholder="e.g. Trip to Lisbon, Dentist" value="${escapeHtml(e.title || "")}">
          <label class="cal-check"><input id="ev-allday" type="checkbox" ${allDay ? "checked" : ""}> All day</label>
          <div class="add-row"><input id="ev-start" type="date" value="${e.start_date || calendarDay}">
            <input id="ev-start-time" type="time" value="${e.start_time || "09:00"}"></div>
          <div class="add-row"><input id="ev-end" type="date" value="${e.end_date || e.start_date || calendarDay}">
            <input id="ev-end-time" type="time" value="${e.end_time || ""}" title="End time (optional)"></div>
          <textarea id="ev-note" rows="3" placeholder="Booking number, address, flight…">${escapeHtml(e.note || "")}</textarea>
          ${householdState ? `<label class="cal-check"><input id="ev-shared" type="checkbox" ${calendarForm.shared ? "checked" : ""}
            ${calendarForm.shared && e.created_by_me === false ? "disabled" : ""}>
            \u{1F3E0} On the household's calendar</label>` : ""}
          <div class="add-row"><button id="ev-save">Save</button><button id="ev-cancel" class="secondary-btn">Cancel</button></div>
        </div>
      </section>`;
    const sync = () => {
      const timed = !document.getElementById("ev-allday").checked;
      document.getElementById("ev-start-time").hidden = !timed;
      document.getElementById("ev-end-time").hidden = !timed;
    };
    document.getElementById("ev-allday").addEventListener("change", sync);
    sync();
    document.getElementById("ev-start").addEventListener("change", ev => {
      const end = document.getElementById("ev-end");
      if (end.value < ev.target.value) end.value = ev.target.value;
    });
    document.getElementById("ev-cancel").addEventListener("click", () => { calendarForm = null; renderCalendarForm(); });
    document.getElementById("ev-save").addEventListener("click", async () => {
      const title = document.getElementById("ev-title").value.trim();
      const startDate = document.getElementById("ev-start").value;
      const endDate = document.getElementById("ev-end").value || startDate;
      const timed = !document.getElementById("ev-allday").checked;
      const startTime = timed ? document.getElementById("ev-start-time").value || null : null;
      const endTime = timed && startTime ? document.getElementById("ev-end-time").value || null : null;
      if (!title || !startDate) return toast("Almost there", "Give the event a title and a date.");
      if (endDate < startDate || (endDate === startDate && endTime && endTime < startTime)) {
        return toast("Check the dates", "It ends before it starts.");
      }
      const id = e.id || randomHexId();
      const { ok } = await calendarCall("save_event", { p_id: id, p_title: title, p_start_date: startDate,
        p_end_date: endDate, p_start_time: startTime, p_end_time: endTime,
        p_note: document.getElementById("ev-note").value.trim(),
        p_shared: Boolean(document.getElementById("ev-shared")?.checked) }, "⚠️ Couldn't save the event");
      if (!ok) return;
      calendarForm = null;
      calendarDay = startDate;
      calendarMonth = null;
      calendarOpen = `event:${id}`;
      await reloadCalendar();
    });
    if (!e.id) document.getElementById("ev-title").focus();
    return;
  }
  box.innerHTML = `
    <section class="card">
      <h2>${kind === "task" ? "New task" : "New shared item"} on ${escapeHtml(isoToDate(calendarDay).toLocaleDateString(undefined, { day: "numeric", month: "short" }))}</h2>
      <div class="stack">
        <input id="day-item-text" type="text" placeholder="${kind === "task" ? "e.g. Renew passport" : "e.g. Pick up the keys"}">
        <input id="day-item-time" type="time" title="Time (optional, you get a reminder then)">
        <div class="add-row"><button id="day-item-add">Add</button><button id="day-item-cancel" class="secondary-btn">Cancel</button></div>
      </div>
    </section>`;
  document.getElementById("day-item-cancel").addEventListener("click", () => { calendarForm = null; renderCalendarForm(); });
  document.getElementById("day-item-add").addEventListener("click", async () => {
    const text = document.getElementById("day-item-text").value.trim();
    const time = document.getElementById("day-item-time").value || null;
    if (!text) return;
    const { ok } = kind === "task"
      ? await calendarCall("add_task", { p_text: text, p_recurrence: "once", p_reminder_time: time, p_due_date: calendarDay },
          "⚠️ Couldn't add the task")
      : await calendarCall("shared_add_task", { p_text: text,
          ...sharedScheduleParams({ recurrence: "once", due_date: calendarDay, due_time: time || "" }) },
          "⚠️ Couldn't add the item");
    if (!ok) return;
    calendarForm = null;
    await reloadCalendar();
  });
  document.getElementById("day-item-text").focus();
}

// ---------- Small utilities ----------

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

let toastTimer = null;
function toast(title, detail) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.innerHTML = `<strong>${escapeHtml(title)}</strong><br>${escapeHtml(detail)}`;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 4000);
}

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
}
