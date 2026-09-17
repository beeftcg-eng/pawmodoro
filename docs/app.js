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
};

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

  const { data: { session } } = await supabaseClient.auth.getSession();
  if (session) {
    await enterApp();
  } else {
    showLoginScreen();
  }
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

async function enterApp() {
  // Fetch state before building the shell — the shell's initial
  // switchTab() renders a tab immediately, and pullAndRender() skips
  // refreshing the Notes editor whenever it's focused (so a poll can't
  // clobber text being typed right now), so if Notes is the tab shown on
  // load, it needs real data on this very first render regardless.
  const { data, error } = await supabaseClient.rpc("sync_pull");
  if (!error) state = data;
  renderShell();
  updateLevelBadge();
  // Logging out and back in within the same page session (no reload)
  // would otherwise stack up a new poller on top of any still-running
  // one from a previous login.
  clearInterval(pollIntervalId);
  pollIntervalId = setInterval(pullAndRender, 5000); // pick up changes made on the desktop app
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
    </nav>`;
  document.getElementById("logout-btn").addEventListener("click", async () => {
    clearInterval(pollIntervalId);
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
  const { data, error } = await supabaseClient.rpc("sync_pull");
  if (requestId !== pullRequestId) return;
  if (error) {
    console.error("sync_pull failed", error);
    return;
  }
  state = data;
  updateLevelBadge();
  if (activeTab === "notes") {
    // Only refresh if the editor doesn't currently have focus, so a
    // poll landing mid-keystroke can never clobber text being typed
    // right now — it picks up on the next poll after you tap away.
    const editor = document.getElementById("notes-editor");
    if (editor && document.activeElement !== editor) {
      editor.innerHTML = state.notes ?? "";
    }
  } else if (activeTab === "checklist") renderChecklist();
  else if (activeTab === "progress") renderProgress();
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
      <button class="remove-btn" title="Remove">✕</button>`;
    li.querySelector("input").addEventListener("change", async e => {
      await toggleTask(task.id, e.target.checked);
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

function renderChecklist() {
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
      <p class="hint">Heads up: mobile browsers can throttle timers once the screen locks or the
      app is backgrounded, so for a reliable alert, keep this tab open and the screen on during a session.</p>
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
  pomodoro.running = !pomodoro.running;
  updateTimerDisplay();
  if (pomodoro.running) {
    pomodoro.intervalId = setInterval(tickPomodoro, 1000);
  } else {
    clearInterval(pomodoro.intervalId);
  }
}

function resetPomodoro() {
  clearInterval(pomodoro.intervalId);
  pomodoro.running = false;
  pomodoro.phase = "work";
  pomodoro.secondsLeft = phaseSeconds("work", getSettings());
  updateTimerDisplay();
}

async function tickPomodoro() {
  pomodoro.secondsLeft -= 1;
  if (pomodoro.secondsLeft <= 0) {
    clearInterval(pomodoro.intervalId);
    pomodoro.running = false;
    await onPhaseComplete();
  }
  updateTimerDisplay();
}

async function onPhaseComplete() {
  const s = getSettings();
  if (pomodoro.phase === "work") {
    pomodoro.sessionCount += 1;
    const { data } = await supabaseClient.rpc("record_pomodoro_completed");
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
  if (window.Notification && Notification.permission === "granted") {
    new Notification(title, { body });
  }
}

// ---------- Progress tab ----------

function renderProgress() {
  if (!state) return;
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
