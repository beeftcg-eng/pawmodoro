// app.js - Pawmodoro mobile web app. No build step: plain JS + the
// Supabase JS client from a CDN. Talks to the same Postgres functions
// (supabase/schema.sql) the desktop app uses, so quests/XP/streaks/notes/
// checklist stay in sync between this and the desktop app.

const CONFIG_KEY = "pawmodoro_config";       // { url, anonKey }
const SESSION_KEY = "pawmodoro_session";     // supabase session, persisted by the client itself
const SETTINGS_KEY = "pawmodoro_settings";   // { workMin, shortBreakMin, longBreakMin, sessionsBeforeLong }

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

async function enterApp() {
  renderShell();
  await pullAndRender();
  setInterval(pullAndRender, 20000); // pick up changes made on the desktop app
}

function renderShell() {
  document.getElementById("app").innerHTML = `
    <header id="header">
      <span id="level-badge"></span>
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
    await supabaseClient.auth.signOut();
    showLoginScreen();
  });
  document.querySelectorAll("#tabbar button").forEach(btn => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
  switchTab("pomodoro");
}

let activeTab = "pomodoro";

function switchTab(tab) {
  activeTab = tab;
  document.querySelectorAll("#tabbar button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  if (tab === "notes") renderNotes();
  else if (tab === "checklist") renderChecklist();
  else if (tab === "pomodoro") renderPomodoro();
  else if (tab === "progress") renderProgress();
}

async function pullAndRender() {
  const { data, error } = await supabaseClient.rpc("sync_pull");
  if (error) {
    console.error("sync_pull failed", error);
    return;
  }
  state = data;
  updateLevelBadge();
  // Only re-render the currently visible tab so typing in Notes isn't
  // clobbered by a poll landing mid-keystroke.
  if (activeTab === "notes") { /* left alone: notes editing owns its own buffer */ }
  else if (activeTab === "checklist") renderChecklist();
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

function renderNotes() {
  const view = document.getElementById("view");
  view.innerHTML = `
    <div class="pane">
      <textarea id="notes-editor" placeholder="Jot anything here. It saves itself.">${escapeHtml(state?.notes ?? "")}</textarea>
      <p id="notes-status" class="hint">Autosaved</p>
    </div>`;
  const editor = document.getElementById("notes-editor");
  editor.addEventListener("input", () => {
    document.getElementById("notes-status").textContent = "Saving…";
    clearTimeout(notesSaveTimer);
    notesSaveTimer = setTimeout(async () => {
      await supabaseClient.rpc("set_notes", { p_notes: editor.value });
      document.getElementById("notes-status").textContent = "Autosaved";
      if (state) state.notes = editor.value;
    }, 800);
  });
}

// ---------- Checklist tab ----------

function renderChecklist() {
  const view = document.getElementById("view");
  const tasks = state?.checklist ?? [];
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
    </div>`;

  const list = document.getElementById("task-list");
  tasks.forEach(task => {
    const li = document.createElement("li");
    li.className = "task-item" + (task.completed_today ? " done" : "");
    li.innerHTML = `
      <label>
        <input type="checkbox" ${task.completed_today ? "checked" : ""}>
        <span>${escapeHtml(task.text)} <em>[${task.recurrence}]</em></span>
      </label>
      <button class="remove-btn" title="Remove">✕</button>`;
    li.querySelector("input").addEventListener("change", async e => {
      await toggleTask(task.id, e.target.checked);
    });
    li.querySelector(".remove-btn").addEventListener("click", async () => {
      await supabaseClient.rpc("remove_task", { p_task_id: task.id });
      await pullAndRender();
    });
    list.appendChild(li);
  });

  document.getElementById("task-add").addEventListener("click", async () => {
    const text = document.getElementById("task-text").value.trim();
    if (!text) return;
    const recurrence = document.getElementById("task-recurrence").value;
    await supabaseClient.rpc("add_task", { p_text: text, p_recurrence: recurrence, p_reminder_time: null });
    document.getElementById("task-text").value = "";
    await pullAndRender();
  });
}

async function toggleTask(taskId, done) {
  const { data, error } = await supabaseClient.rpc("complete_task", { p_task_id: taskId, p_done: done });
  if (!error && done && data) {
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
