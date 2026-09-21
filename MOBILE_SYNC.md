# Pawmodoro Mobile — setup instructions

This has everything needed to get a cloud-backed phone version of
Pawmodoro (notes, checklist, quests/XP) running on someone's own private
account, separate from the desktop app.

The same backend also supports true two-way sync *within* one account
across multiple devices (e.g. if you want your own notes on both your
phone and desktop) — see Step 2 if that's ever useful, but the setup below
defaults to keeping each person's data theirs alone.

**Phone app, live right now:** https://beeftcg-eng.github.io/pawmodoro/
(nothing will work on it until step 1 below is done and the Supabase
details are entered into it once)

---

## Why not an APK?

An APK is an *Android* installer — it cannot install on an iPhone at all,
that's an Apple platform restriction, not something buildable around. The
correct iPhone equivalent is what's actually built here: a **PWA** (Progressive
Web App). Open the link above in Safari, tap **Share → Add to Home
Screen**, and get a real home-screen icon that opens full-screen, no App
Store and no developer account needed. The exact same link works the same
way on Android too.

---

## Step 1 — Create the Supabase project (~5 minutes)

1. Go to [supabase.com](https://supabase.com) and sign up / log in (free tier
   is enough for this).
2. Create a **New project**. Pick any name/region, set a database password
   (you won't need to remember this one — it's separate from your login).
3. Once it's ready, open **SQL Editor** in the left sidebar → **New query**.
4. Open `supabase/schema.sql` in this repo, copy its entire contents, paste
   into the query editor, and click **Run**. This creates all the tables and
   functions — you should see "Success. No rows returned."
5. Go to **Project Settings → API**. You'll need two values from this page:
   - **Project URL** (looks like `https://xxxxxxxx.supabase.co`)
   - **anon public** key (a long string under "Project API keys")
6. Optional but recommended — turn off email confirmation so login works
   immediately: **Authentication → Providers → Email**, toggle off "Confirm
   email". (If you leave it on, each new account needs to click a
   confirmation link in their email the first time they sign up.)

---

## Updating an existing project (v2.9.0 and later)

If you set this up with an older version, do this **once, before** updating
the desktop app or relying on the phone:

1. Open your Supabase project → **SQL Editor → New query**.
2. Paste the *entire* current `supabase/schema.sql` and click **Run**. It's
   safe to re-run: it only adds what's new (a timezone setting, focus-time
   history, the shared household list, one-time-per-period task XP, and
   weekly task resets) and leaves your data alone.
3. Update the desktop app (re-run `install.sh` / `install.bat`). On the
   phone, close the app and reopen it (twice, if it still shows the old
   version — phones cache web apps aggressively).

Until step 2 is done, the desktop app keeps working locally and **holds your
changes safely in its upload queue** (the ☁️ Sync button shows a ⚠ and a
notification tells you why); everything uploads by itself once the schema is
updated. Nothing is lost.

## The shared household list (two people)

Each of you keeps your own account (your notes, checklist and XP stay
private). A *household* links two accounts so you share one to-do list —
groceries, chores — and see each other's level, streak and this-week focus
totals on the Progress tab.

1. Both of you set up Cloud Sync (desktop and/or phone) with **your own**
   email, as described below.
2. **One** of you opens the **🏠 Shared** tab → **Create a household** →
   enter your name. It shows a 10-character **invite code**.
3. The other opens **🏠 Shared** → **Join with an invite code…** → enter the
   code and their name. (Codes aren't case-sensitive.)
4. That's it — the list appears on every desktop app and phone signed in to
   either account. Tick items off, add, remove, or **Clear completed**.

A household holds up to 4 people; **Leave household** removes just you (the
last person out deletes the list).

## Lock down sign-ups once you're both in

The "Create account" button works for anyone who has the project URL and
anon key. Once you and your partner have your accounts: Supabase dashboard →
**Authentication → Sign In / Providers** → turn **off** "Allow new users to
sign up". Existing logins keep working.

## Keeping the free Supabase project awake

Supabase's free tier pauses a project after about a week with no activity.
Using the desktop app (it syncs every few seconds while open) counts as
activity, and `.github/workflows/keep-supabase-awake.yml` pings it every two
days as a backstop. **One catch:** GitHub automatically *disables* scheduled
workflows in a repository with no commits for 60 days, and emails you a
warning first. If you get that email (or the project ever pauses), open the
repo's **Actions** tab → **Keep Supabase project awake** → **Enable
workflow**, and (if it paused) **Restore** the project in the Supabase
dashboard.

---

## Step 2 — Separate accounts, one Supabase project

**Important:** the Supabase project just created is shared *infrastructure*
(one database), but every login gets its own private, walled-off data —
the schema's row-level security means one account can never see another
account's notes, checklist, or progress, full stop. So: each person who
uses this should sign up with their own email, not share a login.

With separate accounts, a given phone is its own independent thing —
nothing syncs between it and anyone else's desktop or phone, *except* the
opt-in shared household list described above. What the shared Supabase
project otherwise buys is durable cloud backup (data survives a lost phone,
a browser reset, etc.).

If you want your *own* desktop notes/checklist/progress backed up too
(e.g. to later add a second device of your own), click the **☁️ Sync**
button next to Theme in the header of the desktop app, paste in the
Project URL + anon key from Step 1, and create an account with your own
email.

---

## Step 3 — Set up a phone

1. On the phone, open Safari (or Chrome on Android) and go to:
   **https://beeftcg-eng.github.io/pawmodoro/**
2. The first time, it'll ask for the **Project URL** and **anon key** — same
   two values from Step 1 (this is the shared *project*, not a shared
   account). Saved on that phone only, once.
3. Tap **"First time — create account"** and pick a personal email +
   password — this is what keeps that account's data private to it.
4. Tap the **Share** icon (square with an arrow) → **Add to Home Screen** →
   **Add**. A "Pawmodoro" icon now sits on the home screen like any other
   app.

---

## What syncs, what doesn't

This describes what syncs *within* a single account across that account's
own devices — not between different people's separate accounts.

| | Synced? |
|---|---|
| Notes | ✅ (plain text on the phone — desktop's bold/highlight/lists formatting isn't editable there, but isn't lost either) |
| Checklist | ✅ (tasks, order, renames, reminders' times; the desktop-only "specific day" tasks stay on the desktop) |
| Shared household list | ✅ — shared with the people in your household |
| Focus-minutes history | ✅ |
| XP, level, streak | ✅ |
| Daily & weekly quests | ✅ — completing a pomodoro/task/break on *either* device advances the same quests |
| Ambient sounds | ❌ desktop-only |
| Music tab (YouTube Music / Spotify) | ❌ phone-only makes no sense here — use the phone's own music apps |
| Theme, pomodoro timer durations, chime / auto-start settings | Independent per device (not synced, so each device can have its own) |

## A real limitation worth knowing

Phone browsers (especially iOS Safari, including installed PWAs) pause
JavaScript once the screen locks or the app isn't in the foreground. The
phone timer counts against a fixed end time, so it always shows the right
time and catches up the moment you reopen the app (crediting the session if
the time is up) — and it asks the phone to keep the screen awake while a
session runs. What it can't do is fire the end-of-session alert *while the
screen is locked*; that alert can be late. A truly reliable background
timer on iOS would require a native app.

## Where things live

- `supabase/schema.sql` — the database schema + all sync logic (run once, per Step 1).
- `docs/` — the phone app's source, auto-deployed to the URL above via GitHub Pages.
- `supabase_sync.py`, `sync_engine.py`, `sync_settings_dialog.py` — the desktop app's sync client, background upload queue, and settings dialog.

## If something breaks

- **"Couldn't connect" in the desktop dialog, or a phone won't log in:**
  double check the Project URL and anon key were copied exactly (no extra
  spaces), and that step 1.4 (running the SQL) actually succeeded.
- **Desktop seems to ignore sync entirely:** it never blocks or crashes the
  app. If the project is unreachable, changes are saved locally and queued
  (hover the ☁️ Sync button, or open the dialog, for the status: ✓ synced,
  ↑N changes waiting, ⚠ needs attention). They upload on their own once the
  connection is back. ⚠ means either "log in again" or "re-run
  `supabase/schema.sql`" — the message says which; nothing is lost either way.
- **Wrong data got uploaded/adopted on first connect:** the first-connect
  rule is simple — if the cloud side is empty, that device's local data is
  uploaded to it; if the cloud side already has data, it replaces the local
  copy. If that went the wrong way once, you can always re-run the SQL
  file's `app_state` and `checklist_tasks` table creation again on a fresh
  Supabase project to start over.
