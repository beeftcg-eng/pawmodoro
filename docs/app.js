// app.js - Pawmodoro mobile web app. No build step: plain JS + the
// Supabase JS client from a CDN. Talks to the same Postgres functions
// (supabase/schema.sql) the desktop app uses, so quests/XP/streaks/notes/
// checklist stay in sync between this and the desktop app.

const CONFIG_KEY = "pawmodoro_config";       // { url, anonKey }
const SESSION_KEY = "pawmodoro_session";     // supabase session, persisted by the client itself
const SETTINGS_KEY = "pawmodoro_settings";   // { workMin, shortBreakMin, longBreakMin, sessionsBeforeLong }
const THEME_KEY = "pawmodoro_theme";         // theme name, independent per device (matches desktop)

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
let pomodoro = {
  phase: "work",          // "work" | "short_break" | "long_break"
  secondsLeft: 25 * 60,
  running: false,
  sessionCount: 0,
  intervalId: null,
  deadline: 0,            // Date.now() value the running phase ends at (the timer counts against this, not ticks)
  completing: false,
};

let lastTzSent = null;         // UTC offset (minutes) last reported to the server
let tzRetryAt = 0;
let householdState;            // undefined = not fetched yet, null = not in a household, object = the household
let householdSupported = true; // false if the cloud schema predates households
let lastHouseholdFetch = 0;
let wakeLock = null;
const renderKeys = {};         // per tab: what the last render was built from, so polls only re-render on change

const QUEST_ICONS = { pomodoros: "\u{1F43E}", tasks: "✅", breaks: "☕", clear_checklist: "\u{1F9F9}" };

// ---------- Setup (Supabase URL + anon key, entered once) ----------

function getConfig() {
  try {
    return JSON.parse(localStorage.getItem(CONFIG_KEY) || "null");
  } catch {
    return null;
  }
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
      <h1>\u{1F43E} Pawmodoro setup</h1>
      <p class="hint">One-time setup: paste the Project URL and anon public key from your
      Supabase project (Project Settings &rarr; API).</p>
      <input id="setup-url" type="text" placeholder="https://xxxx.supabase.co" autocapitalize="off" autocorrect="off">
      <input id="setup-key" type="text" placeholder="anon public key" autocapitalize="off" autocorrect="off">
      <button id="setup-save">Save &amp; continue</button>
      <p id="setup-error" class="error"></p>
    </div>`;
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
    </div>`;

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
  const { error } = mode === "signup"
    ? await supabaseClient.auth.signUp({ email, password })
    : await supabaseClient.auth.signInWithPassword({ email, password });
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
  if (!error) state = data;
  await refreshHousehold(true);
  renderShell();
  updateLevelBadge();
  startPolling();
}

function startPolling() {
  // Logging out and back in within the same page session (no reload)
  // would otherwise stack up a new poller on top of any still-running
  // one from a previous login.
  stopPolling();
  // pick up changes made on the desktop app; skipped while the page is hidden
  // (screen off / another app in front), which saves battery and data
  pollIntervalId = setInterval(() => { if (!document.hidden) pullAndRender(); }, 5000);
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
  const { data, error } = await supabaseClient.rpc("household_pull");
  if (error) {
    // A cloud schema from before households existed: carry on without them.
    if (error.code === "PGRST202" || error.status === 404) householdSupported = false;
    if (householdState === undefined) householdState = null;
    return;
  }
  householdState = data; // null when this account isn't in a household
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
    </nav>`;
  document.getElementById("logout-btn").addEventListener("click", async () => {
    stopPolling();
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
}

let pullRequestId = 0;

async function pullAndRender() {
  // Every call actually fires the request — an action like checking off a
  // task needs its own follow-up pull to always go through, never get
  // silently skipped because a periodic poll happened to be in flight.
  // What's guarded against instead is a *stale* response landing after a
  // newer one already applied: each call gets an id, and a response is
  // only applied if its id is still the most recent one issued, so a slow
  // older request can never overwrite state a faster newer one already set.
  const requestId = ++pullRequestId;
  await sendTzOffset();
  const { data, error } = await supabaseClient.rpc("sync_pull");
  if (requestId !== pullRequestId) return;
  if (error) {
    console.error("sync_pull failed", error);
    return;
  }
  state = data;
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
    if (editor && document.activeElement !== editor) {
      editor.innerHTML = state.notes ?? "";
    }
  } else if (activeTab === "checklist") {
    if (renderKeys.checklist !== checklistKey()) renderChecklist();
  } else if (activeTab === "progress") {
    if (renderKeys.progress !== progressKey()) renderProgress();
  } else if (activeTab === "shared") {
    if (renderKeys.shared !== sharedKey()) renderShared();
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
  view.innerHTML = `
    <div class="pane">
      <div class="notes-toolbar">${toolbarHtml}</div>
      <div id="notes-editor" class="notes-editor" contenteditable="true" data-placeholder="Jot anything here. It saves itself."></div>
      <p id="notes-status" class="hint">Autosaved</p>
    </div>`;
  const editor = document.getElementById("notes-editor");
  editor.innerHTML = state?.notes ?? "";

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
  notesSaveTimer = setTimeout(async () => {
    const html = editor.innerHTML;
    await supabaseClient.rpc("set_notes", { p_notes: html });
    if (state) state.notes = html;
    const status = document.getElementById("notes-status");
    if (status) status.textContent = "Autosaved";
  }, 800);
}

// ---------- Checklist tab ----------

// Shared row renderer for both the regular checklist and the wishlist
// section below it — identical markup/behavior except the regular list
// also shows each task's recurrence tag.
function renderTaskList(container, tasks, { showRecurrence = false } = {}) {
  tasks.forEach((task, index) => {
    const li = document.createElement("li");
    li.className = "task-item" + (task.completed_today ? " done" : "");
    li.innerHTML = `
      <div class="reorder-col">
        <button class="reorder-btn" data-dir="up" title="Move up" ${index === 0 ? "disabled" : ""}>▲</button>
        <button class="reorder-btn" data-dir="down" title="Move down" ${index === tasks.length - 1 ? "disabled" : ""}>▼</button>
      </div>
      <label>
        <input type="checkbox" ${task.completed_today ? "checked" : ""}>
        <span>${escapeHtml(task.text)}${showRecurrence ? ` <em>[${task.recurrence}]</em>` : ""}</span>
      </label>
      <button class="rename-btn" title="Rename">✎</button>
      <button class="remove-btn" title="Remove">✕</button>`;
    li.querySelector("input").addEventListener("change", async e => {
      await toggleTask(task.id, e.target.checked);
    });
    li.querySelector(".rename-btn").addEventListener("click", async () => {
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
  return JSON.stringify(state?.checklist ?? []);
}

function renderChecklist() {
  const restoreDraft = keepDraft("task-text");
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
      <ul id="task-list" class="task-list"></ul>
      <div class="add-row">
        <input id="task-text" type="text" placeholder="e.g. Walk the dogs">
        <select id="task-recurrence">
          <option value="daily">daily</option>
          <option value="weekly">weekly</option>
          <option value="once">once</option>
        </select>
        <button id="task-add">Add</button>
      </div>
      ${wishlistTasks.length ? `
        <h3 class="section-heading">\u{1F0CF} Card Wishlist</h3>
        <ul id="wishlist-task-list" class="task-list"></ul>
      ` : ""}
    </div>`;

  renderTaskList(document.getElementById("task-list"), tasks, { showRecurrence: true });
  const wishlistList = document.getElementById("wishlist-task-list");
  if (wishlistList) renderTaskList(wishlistList, wishlistTasks);

  document.getElementById("task-add").addEventListener("click", async () => {
    const text = document.getElementById("task-text").value.trim();
    if (!text) return;
    const recurrence = document.getElementById("task-recurrence").value;
    await supabaseClient.rpc("add_task", { p_text: text, p_recurrence: recurrence, p_reminder_time: null });
    document.getElementById("task-text").value = "";
    await pullAndRender();
  });
  restoreDraft();
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
  if (!pomodoro.running) pomodoro.secondsLeft = phaseSeconds(pomodoro.phase, s);
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
    };
    saveSettings(newSettings);
    if (!pomodoro.running) {
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
}

function startPomodoro() {
  pomodoro.running = true;
  pomodoro.deadline = Date.now() + pomodoro.secondsLeft * 1000;
  clearInterval(pomodoro.intervalId);
  pomodoro.intervalId = setInterval(tickPomodoro, 500);
  requestWakeLock();
  updateTimerDisplay();
}

function pausePomodoro() {
  settlePomodoro();
  pomodoro.running = false;
  clearInterval(pomodoro.intervalId);
  releaseWakeLock();
  updateTimerDisplay();
}

// Time left is always derived from the end time, never counted tick by tick.
function settlePomodoro() {
  pomodoro.secondsLeft = Math.max(0, Math.ceil((pomodoro.deadline - Date.now()) / 1000));
}

function resetPomodoro() {
  clearInterval(pomodoro.intervalId);
  pomodoro.running = false;
  releaseWakeLock();
  pomodoro.phase = "work";
  pomodoro.secondsLeft = phaseSeconds("work", getSettings());
  updateTimerDisplay();
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
  if (pomodoro.phase === "work") {
    pomodoro.sessionCount += 1;
    const { data } = await supabaseClient.rpc("record_pomodoro_completed", { p_minutes: s.workMin });
    notify("Focus session complete!", data ? `+${data.xp_gained} XP` : "Nice work!");
    if (data) {
      for (const q of data.completed_quests ?? []) {
        toast("\u{1F31F} Quest complete!", `${q.desc} — +${q.bonus_xp} XP`);
      }
    }
    pomodoro.phase = (pomodoro.sessionCount % s.sessionsBeforeLong === 0) ? "long_break" : "short_break";
  } else {
    await supabaseClient.rpc("record_break_completed");
    notify("Break's over", "Back to it when you're ready.");
    pomodoro.phase = "work";
  }
  pomodoro.secondsLeft = phaseSeconds(pomodoro.phase, s);
  updateTimerDisplay();
  await pullAndRender();
}

function notify(title, body) {
  toast(title, body);
  if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
  if (window.Notification && Notification.permission === "granted") {
    new Notification(title, { body });
  }
}

// ---------- Progress tab ----------

function progressKey() {
  return JSON.stringify([state, householdState]);
}

function localISODate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
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
  const daysSinceReset = ((now.getDay() + 6) % 7 - 1 + 7) % 7;
  const weekStart = new Date(now);
  weekStart.setDate(weekStart.getDate() - daysSinceReset);
  const weekKey = localISODate(weekStart);
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
      <div class="add-row shared-actions">
        <button id="shared-clear" class="secondary-btn">Clear completed</button>
        <button id="shared-leave" class="secondary-btn">Leave household</button>
      </div>
    </div>`;
  const list = document.getElementById("shared-list");
  for (const task of h.tasks) {
    const li = document.createElement("li");
    li.className = "task-item" + (task.done ? " done" : "");
    li.innerHTML = `
      <label>
        <input type="checkbox" ${task.done ? "checked" : ""}>
        <span>${escapeHtml(task.text)}${task.done && task.done_by_name ? ` <em>✓ ${escapeHtml(task.done_by_name)}</em>` : ""}</span>
      </label>
      <button class="remove-btn" title="Remove">✕</button>`;
    li.querySelector("input").addEventListener("change", async e => {
      await sharedCall("shared_set_done", { p_id: task.id, p_done: e.target.checked });
      await reloadShared();
    });
    li.querySelector(".remove-btn").addEventListener("click", async () => {
      await sharedCall("shared_remove_task", { p_id: task.id });
      await reloadShared();
    });
    list.appendChild(li);
  }
  const add = async () => {
    const input = document.getElementById("shared-text");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    await sharedCall("shared_add_task", { p_text: text });
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
