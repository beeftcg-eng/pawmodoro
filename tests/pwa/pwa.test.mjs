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
      if (name === "schema_version") return { data: window.eval("SCHEMA_VERSION"), error: null };
      // The polling RPCs answer from the plain ones (never "unchanged", unless a test says so).
      if (name === "sync_pull_if_changed") return { data: state, error: null };
      if (name === "household_pull_if_changed") {
        const { data, error } = handlers.household_pull ? await handlers.household_pull() : { data: null, error: null };
        return error ? { data: null, error } : { data: { household: data, fingerprint: null }, error: null };
      }
      if (name === "sync_pull") return { data: state, error: null };
      return { data: null, error: null };
    },
    // Storage (attached files): recorded as "storage.<method>", answered by handlers of the same name.
    storage: {
      from: bucket => {
        const call = (method, params, fallback) => {
          calls.push({ name: `storage.${method}`, params: { bucket, ...params } });
          return handlers[`storage.${method}`] ? handlers[`storage.${method}`](params) : fallback;
        };
        return {
          upload: async (path, file, options) => call("upload", { path, size: file.size, options }, { data: { path }, error: null }),
          remove: async paths => call("remove", { paths }, { data: [], error: null }),
          createSignedUrls: async (paths, expiresIn) => call("createSignedUrls", { paths, expiresIn },
            { data: paths.map(path => ({ path, signedUrl: `https://signed.example/${path}` })), error: null }),
        };
      },
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
    assert.equal(doc.getElementById("task-weekday").hidden, true);
    const bins = await add("Bins", "weekday", () => { doc.getElementById("task-weekday").value = "3"; });
    assert.equal(bins.p_weekday, 3);
    assert.equal(doc.getElementById("task-weekday").hidden, false);
    const daily = await add("Walk", "daily");
    assert.deepEqual(Object.keys(daily).sort(), ["p_recurrence", "p_reminder_time", "p_text"]);
  } finally { app.close(); }
});

test("a specific-day task shows its day and reminds only then", async () => {
  const app = await bootApp();
  try {
    const today = app.window.eval("mondayIndex(new Date())");
    app.state.checklist.push(
      { id: "b1", text: "Bins", recurrence: "weekday", weekday: today, completed_today: false, source: "checklist" },
      { id: "b2", text: "Gym", recurrence: "weekday", weekday: (today + 1) % 7, completed_today: false, source: "checklist" });
    app.window.eval('switchTab("checklist")');
    await app.window.eval("pullAndRender()");
    const names = app.window.eval("WEEKDAY_NAMES");
    const row = id => app.window.document.querySelector(`[data-task-id="${id}"]`).textContent;
    assert.match(row("b1"), new RegExp(`every ${names[today]}`));
    assert.equal(app.window.eval('taskRemindsToday(state.checklist.find(t => t.id === "b1"), new Date())'), true);
    assert.equal(app.window.eval('taskRemindsToday(state.checklist.find(t => t.id === "b2"), new Date())'), false);
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

test("an older cloud gets the update screen instead of the app", async () => {
  let answer = null;  // null = a newer cloud, which is fine
  const app = await bootApp({ schema_version: () => answer ?? { data: 9999, error: null } });
  try {
    const doc = app.window.document;
    const older = [{ data: 210, error: null },
                   { data: null, error: { code: "PGRST202", message: "Could not find the function" } }];
    for (answer of older) {
      app.window.eval("stopPolling()");
      app.calls.length = 0;
      await app.window.eval("enterApp()");
      assert.ok(doc.getElementById("schema-retry-btn"), "the update screen");
      assert.ok(!app.calls.some(c => c.name === "sync_pull"), "no pull from an older cloud");
    }
    answer = null;
    doc.getElementById("schema-retry-btn").click();
    await until(() => doc.getElementById("view"));
  } finally { app.close(); }
});

test("a network error checking the schema still opens the app", async () => {
  const app = await bootApp({ schema_version: () => ({ data: null, error: { message: "Failed to fetch" } }) });
  try {
    assert.ok(app.window.document.getElementById("view"));
    assert.ok(app.calls.some(c => c.name === "sync_pull"));
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

test("a notes conflict adds the other version to the same page, then saves", async () => {
  let attempt = 0;
  const app = await bootApp({
    set_notes_checked: () => (++attempt === 1
      ? { data: { ok: false, rev: 7, notes: "<html><body><p>desktop's</p></body></html>" }, error: null }
      : { data: { ok: true, rev: 8 }, error: null }),
  });
  try {
    await app.window.eval('saveNotesChecked("main", "Notes", "<p>phone\'s</p>")');
    assert.ok(!app.calls.some(c => c.name === "set_note_page_checked"), "no extra page");
    const saves = app.calls.filter(c => c.name === "set_notes_checked");
    assert.deepEqual(saves.map(s => s.params.p_base_rev), [3, 7]);
    assert.match(saves[1].params.p_notes, /^<p>phone's<\/p><hr><p><i>From your other device \(\d\d:\d\d\):<\/i><\/p><p>desktop's<\/p>$/);
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
    assert.equal(calls.length, 2);
    assert.equal(calls[0].params.p_base_rev, 2);
    assert.equal(calls[1].params.p_id, "p1");
    assert.equal(calls[1].params.p_base_rev, 5);
    assert.match(calls[1].params.p_html, /^<p>mine<\/p><hr>.*<p>theirs<\/p>$/);
  } finally { app.close(); }
});

test("typing during a slow save is never a clash with this phone's own text", async () => {
  // A cloud that takes a while to answer, as on mobile data.
  const cloud = { rev: 3, notes: "<p>main</p>" };
  const app = await bootApp({
    set_notes_checked: async (p) => {
      await new Promise(r => setTimeout(r, 30));
      if (p.p_base_rev !== cloud.rev && p.p_notes !== cloud.notes) return { data: { ok: false, rev: cloud.rev, notes: cloud.notes }, error: null };
      cloud.rev += 1; cloud.notes = p.p_notes;
      return { data: { ok: true, rev: cloud.rev }, error: null };
    },
  });
  try {
    await app.window.eval(`Promise.all([
      saveNotesChecked("main", "Notes", "<p>a</p>"),
      saveNotesChecked("main", "Notes", "<p>ab</p>"),
    ])`);
    // Then a pull that started before those saves lands with the old revision.
    app.window.eval("state.notes_rev = 3");
    await app.window.eval('saveNotesChecked("main", "Notes", "<p>abc</p>")');
    assert.equal(cloud.notes, "<p>abc</p>", "no other-device copy appended");
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

// The phone's copies of the desktop's date rules, against what the Python
// rules answer (tests/parity/cases.json; regenerate with tools/parity_cases.py).
test("date rules match the desktop's (tests/parity)", async () => {
  const cases = JSON.parse(readFileSync(fileURLToPath(new URL("../parity/cases.json", import.meta.url)), "utf8"));
  const app = await bootApp();
  try {
    // noon local time on that day
    const at = day => { const [y, m, d] = day.split("-").map(Number); return new app.window.Date(y, m - 1, d, 12); };
    const call = (fn, ...args) => app.window[fn](...args);
    for (const c of cases.week_start) {
      assert.equal(call("weekStartFor", at(c.day)), c.expect, `weekStartFor(${c.day})`);
    }
    for (const c of cases.month_occurrence) {
      assert.equal(call("monthOccurrence", c.month_day, at(c.day)), c.expect, `monthOccurrence(${c.month_day}, ${c.day})`);
    }
    for (const c of cases.streak) {
      assert.equal(call("taskStreak", c.task, at(c.day)), c.expect, `taskStreak(${JSON.stringify(c.task)}, ${c.day})`);
    }
    for (const c of cases.reminds) {
      assert.equal(call("taskRemindsToday", c.task, at(c.day)), c.expect, `taskRemindsToday(${JSON.stringify(c.task)}, ${c.day})`);
    }
    for (const c of cases.calendar_task) {
      const what = `${JSON.stringify(c.task)} on ${c.day}`;
      assert.equal(call("taskOnDay", c.task, c.day), c.expect.on, `taskOnDay(${what})`);
      assert.equal(call("taskDoneOn", c.task, c.day), c.expect.done, `taskDoneOn(${what})`);
    }
    for (const c of cases.calendar_shared) {
      const what = `${JSON.stringify(c.task)} on ${c.day}`;
      assert.equal(call("sharedOnDay", c.task, c.day), c.expect.on, `sharedOnDay(${what})`);
      assert.equal(call("sharedDoneOn", c.task, c.day, c.today), c.expect.done, `sharedDoneOn(${what}, today ${c.today})`);
    }
  } finally { app.close(); }
});

test("a poll with nothing new keeps what's shown, and an older cloud gets plain pulls", async () => {
  let unchanged = false;
  const app = await bootApp({
    sync_pull_if_changed: params => unchanged
      ? { data: { unchanged: true, fingerprint: "fp1" }, error: null }
      : { data: { ...pullState(), fingerprint: "fp1" }, error: null },
  });
  try {
    await app.window.eval("pullAndRender({ poll: true })");
    unchanged = true;
    await app.window.eval("pullAndRender({ poll: true })");
    const polls = app.calls.filter(c => c.name === "sync_pull_if_changed");
    assert.equal(polls.at(-1).params.p_fingerprint, "fp1", "a poll offers what it has");
    assert.equal(app.window.eval("state.checklist.length"), 2, "an unchanged answer keeps the state");
    await app.window.eval("pullAndRender()");
    assert.equal(app.calls.filter(c => c.name === "sync_pull_if_changed").at(-1).params.p_fingerprint, null,
      "the pull after an action is a full one");
  } finally { app.close(); }
});

test("a shared item's turn goes round the members, and shows who", async () => {
  const household = {
    id: "h1", invite_code: "CODE",
    members: [{ user_id: "u1", name: "Me", is_me: true }, { user_id: "u2", name: "Bea", is_me: false }],
    tasks: [{ id: "s1", text: "Bins", done: false, recurrence: "once", assigned_to: "u2", assigned_name: "Bea", assigned_to_me: false }],
  };
  const app = await bootApp({ household_pull: () => ({ data: household, error: null }) });
  try {
    app.window.eval('switchTab("shared")');
    await until(() => app.window.document.querySelector("#shared-list .assign-btn"));
    assert.match(app.window.document.getElementById("shared-list").textContent, /→ Bea/);
    app.window.document.querySelector("#shared-list .assign-btn").click();
    await settle();
    assert.deepEqual(plain(app.calls.find(c => c.name === "shared_assign").params), { p_id: "s1", p_user: null },
      "after the last member comes anyone");
  } finally { app.close(); }
});

// ---------- one timer across devices ----------

test("starting and pausing the timer tells the other devices", async () => {
  const app = await bootApp({ set_timer_state: () => ({ data: 5, error: null }) });
  try {
    app.window.eval('switchTab("pomodoro")');
    app.window.document.getElementById("pomo-start-pause").click();
    await settle();
    const started = plain(app.calls.filter(c => c.name === "set_timer_state").at(-1).params.p_state);
    assert.equal(started.phase, "work");
    assert.equal(started.running, true);
    assert.ok(Math.abs(started.ends_at - (Date.now() / 1000 + 25 * 60)) < 3);
    assert.match(started.device, /^phone-/);
    app.window.eval("pomodoro.deadline -= 60000"); // a minute in
    app.window.document.getElementById("pomo-start-pause").click();
    await settle();
    const paused = plain(app.calls.filter(c => c.name === "set_timer_state").at(-1).params.p_state);
    assert.equal(paused.running, false);
    assert.equal(paused.seconds_left, 24 * 60);
    app.window.eval('switchTab("checklist"); switchTab("pomodoro")');
    assert.equal(app.window.document.getElementById("timer-text").textContent, "24:00", "a re-render keeps the paused time");
  } finally { app.close(); }
});

test("the phone follows the desktop's timer, and leaves the count to it", async () => {
  const app = await bootApp();
  try {
    app.window.eval('switchTab("pomodoro")');
    const endsAt = Date.now() / 1000 + 600;
    app.window.eval(`followRemoteTimer({ phase: "short_break", running: true, ends_at: ${endsAt}, device: "desk", rev: 4 })`);
    assert.equal(app.window.eval("pomodoro.phase"), "short_break");
    assert.equal(app.window.eval("pomodoro.running"), true);
    assert.match(app.window.document.getElementById("timer-text").textContent, /^(10:00|09:59)$/);
    assert.ok(!app.calls.some(c => c.name === "set_timer_state"), "following isn't sent back");
    app.window.eval('followRemoteTimer({ phase: "short_break", running: true, ends_at: 1, device: "desk", rev: 4 })');
    assert.equal(app.window.eval("pomodoro.deadline"), endsAt * 1000, "a revision is followed once");

    // the desktop's session ends: the phone doesn't count it itself...
    app.window.eval(`followRemoteTimer({ phase: "work", running: true, ends_at: ${Date.now() / 1000 - 1}, device: "desk", rev: 5 })`);
    await app.window.eval("tickPomodoro()");
    await settle();
    assert.ok(!app.calls.some(c => c.name === "record_pomodoro_completed"));
    assert.equal(app.window.eval("pomodoro.takeover.phase"), "work");
    // ...unless the desktop never reports back
    await app.window.eval("takeOverTimer(pomodoro.takeover)");
    const counted = app.calls.find(c => c.name === "record_pomodoro_completed");
    assert.match(counted.params.p_session, /^work:\d+$/);
    assert.equal(app.window.eval("pomodoro.owner"), true);
  } finally { app.close(); }
});

test("its own timer counts its sessions with a key", async () => {
  const app = await bootApp({
    record_pomodoro_completed: () => ({ data: { xp_gained: 20, completed_quests: [] }, error: null }),
  });
  try {
    app.window.eval('switchTab("pomodoro"); startPomodoro(Date.now() - 1); pomodoro.owner = true');
    await app.window.eval("tickPomodoro()");
    await settle();
    const calls = app.calls.filter(c => c.name === "record_pomodoro_completed").map(c => plain(c.params));
    assert.equal(calls.length, 1);
    assert.match(calls[0].p_session, /^work:/);
    assert.equal(app.window.eval("pomodoro.phase"), "short_break");
  } finally { app.close(); }
});

// ---------- Calendar: events and attached files ----------

function calendarState(app, extra = {}) {
  Object.assign(app.state, {
    events: [{ id: "ev1", title: "Lisbon trip", start_date: "2026-10-20", end_date: "2026-10-22", start_time: null,
      end_time: null, note: "TP123", created_by_me: true }],
    attachments: [{ id: "f1", item_kind: "event", item_id: "ev1", file_name: "hotel.jpg", mime: "image/jpeg", size: 2048,
      path: "u/u1/f1", added_by_me: true }],
    attachments_trash: [],
    ...extra,
  });
  app.state.checklist.push({ id: "t3", text: "Renew passport", recurrence: "once", due_date: "2026-10-21",
    completed_today: false, source: "checklist" });
}

function showDay(app, day) {
  app.window.eval(`calendarDay = "${day}"; calendarMonth = null; calendarOpen = null; switchTab("calendar")`);
}

test("the calendar puts events and dated tasks on their days", async () => {
  const app = await bootApp();
  try {
    calendarState(app);
    showDay(app, "2026-10-21");
    const doc = app.window.document;
    const cell = doc.querySelector('.cal-cell[data-day="2026-10-21"]');
    assert.equal(cell.querySelectorAll(".cal-dot").length, 2);
    assert.ok(cell.classList.contains("selected"));
    assert.equal(doc.querySelectorAll('.cal-cell[data-day="2026-10-23"] .cal-dot').length, 0, "the trip is over by then");
    const items = [...doc.querySelectorAll("#cal-day-list .cal-entry")].map(li => li.textContent.replace(/\s+/g, " ").trim());
    assert.equal(items.length, 2);
    assert.match(items[0], /… Lisbon trip/);
    assert.match(items[0], /📎1/);
    assert.match(items[1], /Renew passport/);
    doc.querySelector("#cal-day-list .cal-entry-head").click();
    await settle();
    const details = doc.querySelector(".cal-details");
    assert.match(details.textContent, /TP123/);
    const link = details.querySelector(".cal-files a");
    assert.equal(link.href, "https://signed.example/u/u1/f1", "a signed link to the private file");
    assert.equal(link.querySelector("img").src, "https://signed.example/u/u1/f1", "a thumbnail for a photo");
  } finally { app.close(); }
});

test("adding an event sends it, all day or at a time", async () => {
  const app = await bootApp({ save_event: () => ({ data: {}, error: null }) });
  try {
    calendarState(app);
    showDay(app, "2026-10-14");
    const doc = app.window.document;
    doc.querySelector('[data-add="event"]').click();
    doc.getElementById("ev-title").value = "Dentist";
    doc.getElementById("ev-allday").checked = false;
    doc.getElementById("ev-allday").dispatchEvent(new app.window.Event("change"));
    doc.getElementById("ev-start-time").value = "15:30";
    doc.getElementById("ev-end-time").value = "16:00";
    doc.getElementById("ev-save").click();
    await settle();
    const sent = plain(app.calls.find(c => c.name === "save_event").params);
    assert.match(sent.p_id, /^[0-9a-f]{12}$/);
    assert.deepEqual({ ...sent, p_id: undefined }, { p_id: undefined, p_title: "Dentist", p_start_date: "2026-10-14",
      p_end_date: "2026-10-14", p_start_time: "15:30", p_end_time: "16:00", p_note: "", p_shared: false });
    assert.equal(app.window.eval("calendarOpen"), `event:${sent.p_id}`, "its details open, ready for files");
  } finally { app.close(); }
});

test("a task or a shared item can be put on a day", async () => {
  const household = { id: "h1", invite_code: "C", members: [{ user_id: "u1", name: "Me", is_me: true }], tasks: [] };
  const app = await bootApp({ household_pull: () => ({ data: household, error: null }) });
  try {
    calendarState(app);
    await app.window.eval("refreshHousehold(true)");
    showDay(app, "2026-10-14");
    const doc = app.window.document;
    doc.querySelector('[data-add="shared"]').click();
    doc.getElementById("day-item-text").value = "Pick up the keys";
    doc.getElementById("day-item-time").value = "18:00";
    doc.getElementById("day-item-add").click();
    await settle();
    assert.deepEqual(plain(app.calls.find(c => c.name === "shared_add_task").params), {
      p_text: "Pick up the keys", p_recurrence: "once", p_weekday: null, p_month_day: null,
      p_due_date: "2026-10-14", p_due_time: "18:00" });
    doc.querySelector('[data-add="task"]').click();
    doc.getElementById("day-item-text").value = "Renew passport";
    doc.getElementById("day-item-add").click();
    await settle();
    assert.deepEqual(plain(app.calls.find(c => c.name === "add_task").params), {
      p_text: "Renew passport", p_recurrence: "once", p_reminder_time: null, p_due_date: "2026-10-14" });
  } finally { app.close(); }
});

function attachFile(app, file) {
  const input = app.window.document.querySelector(".cal-attach input");
  Object.defineProperty(input, "files", { value: [file] });
  input.dispatchEvent(new app.window.Event("change"));
}

test("a file is registered, uploaded to its path, then marked done", async () => {
  const app = await bootApp({ add_attachment: params => ({ data: { path: `u/u1/${params.p_id}` }, error: null }) });
  try {
    calendarState(app);
    showDay(app, "2026-10-20");
    app.window.document.querySelector("#cal-day-list .cal-entry-head").click();
    await settle();
    attachFile(app, new app.window.File(["%PDF-1.4"], "ticket.pdf", { type: "application/pdf" }));
    await until(() => app.calls.some(c => c.name === "attachment_uploaded"));
    const add = plain(app.calls.find(c => c.name === "add_attachment").params);
    assert.deepEqual({ ...add, p_id: undefined }, { p_id: undefined, p_item_kind: "event", p_item_id: "ev1",
      p_file_name: "ticket.pdf", p_mime: "application/pdf", p_size: 8 });
    const upload = plain(app.calls.find(c => c.name === "storage.upload").params);
    assert.equal(upload.bucket, "attachments");
    assert.equal(upload.path, `u/u1/${add.p_id}`);
    assert.deepEqual(upload.options, { upsert: true, contentType: "application/pdf" });
    assert.equal(app.calls.find(c => c.name === "attachment_uploaded").params.p_id, add.p_id);
  } finally { app.close(); }
});

test("a failed upload takes its registration back, and big files never start", async () => {
  const app = await bootApp({
    add_attachment: params => ({ data: { path: `u/u1/${params.p_id}` }, error: null }),
    "storage.upload": () => ({ data: null, error: { message: "network" } }),
  });
  try {
    calendarState(app);
    showDay(app, "2026-10-20");
    app.window.document.querySelector("#cal-day-list .cal-entry-head").click();
    await settle();
    attachFile(app, new app.window.File(["x"], "a.txt", { type: "text/plain" }));
    await until(() => app.calls.some(c => c.name === "remove_attachment"));
    assert.ok(!app.calls.some(c => c.name === "attachment_uploaded"));
    const big = await app.window.eval(`uploadAttachment("event", "ev1", { name: "film.mov", size: ${26 * 1024 * 1024} })`);
    assert.equal(big, false);
    assert.equal(app.calls.filter(c => c.name === "add_attachment").length, 1);
  } finally { app.close(); }
});

test("removing a file deletes it from Storage before forgetting it", async () => {
  const app = await bootApp();
  try {
    calendarState(app);
    showDay(app, "2026-10-20");
    app.window.document.querySelector("#cal-day-list .cal-entry-head").click();
    await settle();
    app.window.document.querySelector(".cal-files .remove-btn").click();
    await until(() => app.calls.some(c => c.name === "remove_attachment"));
    const names = app.calls.map(c => c.name).filter(n => n === "storage.remove" || n === "remove_attachment");
    assert.deepEqual(names, ["storage.remove", "remove_attachment"]);
    assert.deepEqual(plain(app.calls.find(c => c.name === "storage.remove").params.paths), ["u/u1/f1"]);
  } finally { app.close(); }
});

test("files in the trash for over an hour are deleted, once", async () => {
  const old = new Date(Date.now() - 2 * 3600 * 1000).toISOString();
  const app = await bootApp();
  try {
    calendarState(app, { attachments_trash: [{ id: "gone", path: "u/u1/gone", trashed_at: old },
      { id: "recent", path: "u/u1/recent", trashed_at: new Date().toISOString() }] });
    await app.window.eval("pullAndRender()");
    await app.window.eval("pullAndRender()");
    await settle();
    const removed = app.calls.filter(c => c.name === "storage.remove").map(c => plain(c.params.paths));
    assert.deepEqual(removed, [["u/u1/gone"]]);
    assert.deepEqual(app.calls.filter(c => c.name === "remove_attachment").map(c => c.params.p_id), ["gone"]);
  } finally { app.close(); }
});
