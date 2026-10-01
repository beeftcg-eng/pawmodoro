// Tests for the phone app (docs/app.js, docs/sw.js) in a simulated browser
// (jsdom) with a fake Supabase client: the real app boots, signs in, pulls
// state and renders, and each test drives a feature and checks which RPCs it
// called. Run: cd tests/pwa && npm install && npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import { JSDOM } from "jsdom";

const docs = fileURLToPath(new URL("../../docs/", import.meta.url));
const APP_JS = readFileSync(docs + "app.js", "utf8");
const SW_JS = readFileSync(docs + "sw.js", "utf8");

function pullState(overrides = {}) {
  return {
    notes: "<p>main</p>", notes_rev: 3, xp: 0, total_pomodoros: 0, total_tasks: 0,
    current_streak: 0, longest_streak: 0, quests: [], weekly_quests: [], history: [],
    checklist: [
      { id: "t1", text: "Water", recurrence: "daily", completed_today: false, reminder_time: null,
        reminder_every_h: 8, snoozed_until: null, source: "checklist" },
      { id: "t2", text: "Walk", recurrence: "daily", completed_today: false, reminder_time: "09:00",
        reminder_every_h: null, snoozed_until: new Date(Date.now() + 10 * 60000).toISOString(), source: "checklist" },
    ],
    notes_pages: [{ id: "p1", title: "Ideas", html: "<p>idea</p>", rev: 2 }],
    reminders: { quiet_enabled: false, quiet_start: "22:00", quiet_end: "08:00" },
    ...overrides,
  };
}

// Boots docs/app.js signed in. `handlers[name](params)` answers an RPC
// ({data, error}); every call is recorded in `calls`.
async function bootApp(handlers = {}) {
  const calls = [];
  const state = pullState();
  const fakeClient = {
    auth: { getSession: async () => ({ data: { session: { user: { id: "u1", email: "me@example.com" } } }, error: null }) },
    rpc: async (name, params) => {
      calls.push({ name, params });
      if (handlers[name]) return handlers[name](params);
      if (name === "sync_pull") return { data: state, error: null };
      return { data: null, error: null };
    },
  };
  const dom = new JSDOM(`<!doctype html><body><div id="app"></div></body>`, {
    url: "https://beeftcg-eng.github.io/pawmodoro/", runScripts: "dangerously", pretendToBeVisual: true,
  });
  const { window } = dom;
  window.supabase = { createClient: () => fakeClient };
  window.localStorage.setItem("pawmodoro_config", JSON.stringify({ url: "https://x.supabase.co", anonKey: "anon" }));
  window.prompt = () => null;
  window.confirm = () => true;
  const script = window.document.createElement("script");
  script.textContent = APP_JS;
  window.document.body.appendChild(script);
  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  await until(() => window.document.getElementById("view"));
  return { window, calls, state, close: () => { window.eval("stopPolling()"); window.close(); } };
}

async function until(check, ms = 2000) {
  const end = Date.now() + ms;
  while (!check()) {
    if (Date.now() > end) throw new Error("timed out");
    await new Promise(r => setTimeout(r, 10));
  }
}

const settle = () => new Promise(r => setTimeout(r, 50));

test("checklist shows every-N-hours and snoozed reminders", async () => {
  const app = await bootApp();
  try {
    app.window.eval('switchTab("checklist")');
    const text = app.window.document.getElementById("task-list").textContent;
    assert.match(text, /every 8h/);
    assert.match(text, /09:00/);
    assert.match(text, /💤 \d\d:\d\d/);
  } finally { app.close(); }
});

test("setting 'every 6 hours' calls set_task_reminder_mode", async () => {
  const app = await bootApp();
  try {
    app.window.eval('switchTab("checklist")');
    const row = app.window.document.querySelectorAll("#task-list .task-item")[1];
    row.querySelector(".reminder-btn").click();
    const mode = row.querySelector(".reminder-mode");
    mode.value = "every";
    mode.dispatchEvent(new app.window.Event("change"));
    assert.equal(row.querySelector(".reminder-time").hidden, true);
    row.querySelector(".reminder-every input").value = "6";
    row.querySelector(".reminder-save").click();
    await settle();
    const call = app.calls.find(c => c.name === "set_task_reminder_mode");
    // (params come from the page's realm, so compare as plain JSON)
    assert.deepEqual(JSON.parse(JSON.stringify(call.params)), { p_task_id: "t2", p_reminder_time: null, p_every_h: 6 });
  } finally { app.close(); }
});

const plain = value => JSON.parse(JSON.stringify(value));

test("adding a monthly task or a dated one-off sends the day / date (and a plain one neither)", async () => {
  const app = await bootApp();
  try {
    app.window.eval('switchTab("checklist")');
    const doc = app.window.document;
    const add = async (text, repeat, setPicker) => {
      doc.getElementById("task-text").value = text;
      const select = doc.getElementById("task-recurrence");
      select.value = repeat;
      select.dispatchEvent(new app.window.Event("change"));
      setPicker?.();
      doc.getElementById("task-add").click();
      await settle();
      return plain(app.calls.filter(c => c.name === "add_task").at(-1).params);
    };
    assert.equal(doc.getElementById("task-month-day").hidden, true);
    const monthly = await add("Rent", "monthly", () => { doc.getElementById("task-month-day").value = "31"; });
    assert.equal(monthly.p_month_day, 31);
    assert.equal("p_due_date" in monthly, false);
    const dated = await add("Taxes", "once", () => { doc.getElementById("task-due-date").value = "2026-10-15"; });
    assert.equal(dated.p_due_date, "2026-10-15");
    const daily = await add("Walk", "daily");
    assert.deepEqual(Object.keys(daily).sort(), ["p_recurrence", "p_reminder_time", "p_text"]);
  } finally { app.close(); }
});

test("overdue one-offs, monthly tasks and steps show in the list", async () => {
  const app = await bootApp();
  try {
    app.state.checklist.push(
      { id: "t3", text: "Taxes", recurrence: "once", due_date: "2020-01-01", completed_today: false, source: "checklist",
        note: "receipts in the drawer", subtasks: [{ id: "a", text: "Find receipts", done: true }, { id: "b", text: "File", done: false }] },
      { id: "t4", text: "Rent", recurrence: "monthly", month_day: 1, completed_today: false, source: "checklist", subtasks: [] });
    app.window.eval('switchTab("checklist")');
    const rows = app.window.document.querySelectorAll("#task-list .task-item");
    assert.match(rows[2].className, /due-overdue/);
    assert.match(rows[2].textContent, /⚠ Taxes/);
    assert.match(rows[2].textContent, /☑ 1\/2/);
    assert.match(rows[3].textContent, /\[monthly, 1st\]/);
    assert.equal(rows[2].querySelector(".task-details").hidden, true);
    rows[2].querySelector(".details-btn").click();
    assert.equal(rows[2].querySelector(".task-details").hidden, false);
  } finally { app.close(); }
});

test("ticking a step, adding one, and editing the note", async () => {
  const app = await bootApp();
  try {
    app.state.checklist.push(
      { id: "t3", text: "Taxes", recurrence: "once", due_date: null, completed_today: false, source: "checklist",
        note: "", subtasks: [{ id: "a", text: "Find receipts", done: false }] });
    app.window.eval('switchTab("checklist")');
    const row = () => app.window.document.querySelectorAll("#task-list .task-item")[2];
    row().querySelector(".details-btn").click();
    const tick = row().querySelector(".subtask input");
    tick.checked = true;
    tick.dispatchEvent(new app.window.Event("change"));
    await settle();
    assert.deepEqual(plain(app.calls.find(c => c.name === "set_subtask_done").params),
      { p_task_id: "t3", p_sub_id: "a", p_done: true });
    assert.equal(row().querySelector(".task-details").hidden, false, "the panel stays open after a re-render");
    row().querySelector(".subtask-text").value = "File it";
    row().querySelector(".subtask-add-btn").click();
    await settle();
    const added = plain(app.calls.filter(c => c.name === "set_task_details").at(-1).params);
    assert.deepEqual(added.p_subtasks.map(s => s.text), ["Find receipts", "File it"]);
    assert.equal(added.p_subtasks[0].id, "a");
    const note = row().querySelector(".task-note");
    note.value = "by the 15th";
    note.dispatchEvent(new app.window.Event("change"));
    await settle();
    assert.equal(app.calls.filter(c => c.name === "set_task_details").at(-1).params.p_note, "by the 15th");
    const due = row().querySelector(".task-due");
    due.value = "2026-11-02";
    due.dispatchEvent(new app.window.Event("change"));
    await settle();
    assert.deepEqual(plain(app.calls.find(c => c.name === "set_task_due_date").params),
      { p_task_id: "t3", p_due_date: "2026-11-02" });
  } finally { app.close(); }
});

test("an older cloud falls back to a plain daily time", async () => {
  const missing = { data: null, error: { code: "PGRST202", message: "Could not find the function" } };
  const app = await bootApp({ set_task_reminder_mode: () => missing });
  try {
    await app.window.eval('setTaskReminder("t1", "07:15", null)');
    assert.ok(app.calls.some(c => c.name === "set_task_reminder" && c.params.p_reminder_time === "07:15"));
  } finally { app.close(); }
});

test("notes saved on top of the latest revision go straight through", async () => {
  const app = await bootApp({ set_notes_checked: () => ({ data: { ok: true, rev: 4 }, error: null }) });
  try {
    await app.window.eval('saveNotesChecked("main", "Notes", "<p>mine</p>")');
    const saves = app.calls.filter(c => c.name === "set_notes_checked");
    assert.equal(saves.length, 1);
    assert.equal(saves[0].params.p_base_rev, 3);
    assert.equal(app.window.eval("state.notes_rev"), 4);
  } finally { app.close(); }
});

test("a notes conflict keeps the other version as a page, then saves ours", async () => {
  let attempt = 0;
  const app = await bootApp({
    set_notes_checked: () => (++attempt === 1
      ? { data: { ok: false, rev: 7, notes: "<p>desktop's</p>" }, error: null }
      : { data: { ok: true, rev: 8 }, error: null }),
    set_note_page_checked: () => ({ data: { ok: true, rev: 1 }, error: null }),
  });
  try {
    await app.window.eval('saveNotesChecked("main", "Notes", "<p>phone\'s</p>")');
    const copy = app.calls.find(c => c.name === "set_note_page_checked");
    assert.equal(copy.params.p_html, "<p>desktop's</p>");
    assert.match(copy.params.p_title, /^Notes \(other device, \d\d:\d\d\)$/);
    const saves = app.calls.filter(c => c.name === "set_notes_checked");
    assert.deepEqual(saves.map(s => s.params.p_base_rev), [3, 7]);
    assert.equal(saves[1].params.p_notes, "<p>phone's</p>");
  } finally { app.close(); }
});

test("a page conflict works the same way", async () => {
  let attempt = 0;
  const app = await bootApp({
    set_note_page_checked: (p) => (p.p_id === "p1" && ++attempt === 1
      ? { data: { ok: false, rev: 5, title: "Ideas", html: "<p>theirs</p>" }, error: null }
      : { data: { ok: true, rev: 6 }, error: null }),
  });
  try {
    await app.window.eval('saveNotesChecked("p1", "Ideas", "<p>mine</p>")');
    const calls = app.calls.filter(c => c.name === "set_note_page_checked");
    assert.equal(calls[0].params.p_base_rev, 2);
    assert.equal(calls[1].params.p_html, "<p>theirs</p>");
    assert.match(calls[1].params.p_title, /^Ideas \(other device/);
    assert.equal(calls[2].params.p_base_rev, 5);
  } finally { app.close(); }
});

test("with push on, the timer books and cancels its push", async () => {
  const app = await bootApp();
  try {
    app.window.eval('pushOn = true; pushEndpoint = "https://push.example/me";');
    app.window.eval('switchTab("pomodoro"); startPomodoro()');
    await settle();
    const booked = app.calls.find(c => c.name === "schedule_timer_push");
    assert.equal(booked.params.p_endpoint, "https://push.example/me");
    const inMinutes = (new Date(booked.params.p_fire_at) - Date.now()) / 60000;
    assert.ok(inMinutes > 24 && inMinutes <= 25, `fires at the end of the session (${inMinutes} min)`);
    app.window.eval("pausePomodoro()");
    await settle();
    assert.ok(app.calls.some(c => c.name === "cancel_timer_push"));
  } finally { app.close(); }
});

test("without push, the timer books nothing", async () => {
  const app = await bootApp();
  try {
    app.window.eval('switchTab("pomodoro"); startPomodoro(); pausePomodoro()');
    await settle();
    assert.ok(!app.calls.some(c => c.name === "schedule_timer_push" || c.name === "cancel_timer_push"));
  } finally { app.close(); }
});

test("in-app reminders stay quiet when push is on (no double alerts)", async () => {
  const app = await bootApp();
  try {
    app.window.eval('state.checklist[1].reminder_time = "00:00"; state.checklist[1].snoozed_until = null;');
    app.window.eval("pushOn = true; checkTaskReminders()");
    assert.equal(app.window.localStorage.getItem("pawmodoro_reminded"), null);
    app.window.eval("pushOn = false; checkTaskReminders()");
    assert.match(app.window.localStorage.getItem("pawmodoro_reminded"), /t2/);
  } finally { app.close(); }
});

// ---------- the service worker ----------

function loadServiceWorker() {
  const handlers = {};
  const shown = [];
  const fetches = [];
  const opened = [];
  const self = {
    addEventListener: (type, fn) => { handlers[type] = fn; },
    registration: {
      showNotification: async (title, options) => { shown.push({ title, options }); },
      getNotifications: async () => [],
    },
    skipWaiting: () => {},
    clients: { claim: () => {} },
  };
  const context = {
    self, caches: {}, location: { origin: "https://beeftcg-eng.github.io" }, URL, setTimeout,
    clients: { matchAll: async () => [], openWindow: async (url) => { opened.push(url); } },
    fetch: async (url, init) => { fetches.push({ url, init }); return { ok: true, json: async () => "ok" }; },
  };
  vm.runInNewContext(SW_JS, context);
  const fire = async (type, event) => {
    let waited;
    handlers[type]({ ...event, waitUntil: (p) => { waited = p; } });
    await waited;
  };
  return { fire, shown, fetches, opened };
}

test("a push shows a notification that alerts, with its buttons", async () => {
  const sw = loadServiceWorker();
  const payload = { title: "Task reminder", body: "Water", tag: "task-t1", token: "abc",
    api: { url: "https://x.supabase.co", key: "anon" },
    actions: [{ action: "done", title: "✅ Done" }, { action: "snooze", title: "💤 15 min" }] };
  await sw.fire("push", { data: { json: () => payload } });
  const { title, options } = sw.shown[0];
  assert.equal(title, "Task reminder");
  assert.equal(options.renotify, true);
  assert.equal(options.silent, false);
  assert.ok(options.vibrate.length > 0);
  assert.deepEqual(options.actions.map(a => a.action), ["done", "snooze"]);
  assert.equal(options.data.token, "abc");
});

test("tapping Snooze calls push_action with the token, without opening the app", async () => {
  const sw = loadServiceWorker();
  const notification = { close() {}, body: "Water", tag: "task-t1",
    data: { token: "abc", api: { url: "https://x.supabase.co", key: "anon" } } };
  await sw.fire("notificationclick", { action: "snooze", notification });
  assert.equal(sw.fetches[0].url, "https://x.supabase.co/rest/v1/rpc/push_action");
  assert.deepEqual(JSON.parse(sw.fetches[0].init.body), { p_token: "abc", p_action: "snooze" });
  assert.deepEqual(sw.opened, []);
  assert.match(sw.shown[0].title, /Snoozed/);
});

test("tapping the notification itself opens the app", async () => {
  const sw = loadServiceWorker();
  await sw.fire("notificationclick", { action: "", notification: { close() {}, data: {} } });
  assert.deepEqual(sw.opened, ["./"]);
  assert.equal(sw.fetches.length, 0);
});
