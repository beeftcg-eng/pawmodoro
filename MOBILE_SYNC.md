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

## Step 2 — Separate accounts, one Supabase project

**Important:** the Supabase project just created is shared *infrastructure*
(one database), but every login gets its own private, walled-off data —
the schema's row-level security means one account can never see another
account's notes, checklist, or progress, full stop. So: each person who
uses this should sign up with their own email, not share a login.

With separate accounts, a given phone is its own independent thing —
nothing syncs between it and anyone else's desktop or phone. What the
shared Supabase project buys is durable cloud backup (data survives a
lost phone, a browser reset, etc.), not sharing between people.

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
| Checklist | ✅ |
| XP, level, streak | ✅ |
| Daily & weekly quests | ✅ — completing a pomodoro/task/break on *either* device advances the same quests |
| Ambient sounds | ❌ desktop-only |
| Music tab (YouTube Music / Spotify) | ❌ phone-only makes no sense here — use the phone's own music apps |
| Theme, pomodoro timer durations | Independent per device (not synced, so each device can have its own) |

## A real limitation worth knowing

Phone browsers (especially iOS Safari, including installed PWAs) throttle
or pause JavaScript timers once the screen locks or the app isn't in the
foreground. That means a 25-minute countdown running while the phone is
locked in a pocket may not fire its completion alert exactly on time. For
the most reliable experience, the screen should stay on and the app in the
foreground during a session. This is a platform restriction, not a bug —
a fully reliable background timer on iOS would require a native app.

## Where things live

- `supabase/schema.sql` — the database schema + all sync logic (run once, per Step 1).
- `docs/` — the phone app's source, auto-deployed to the URL above via GitHub Pages.
- `supabase_sync.py`, `sync_settings_dialog.py` — the desktop app's sync client and settings dialog.

## If something breaks

- **"Couldn't connect" in the desktop dialog, or a phone won't log in:**
  double check the Project URL and anon key were copied exactly (no extra
  spaces), and that step 1.4 (running the SQL) actually succeeded.
- **Desktop seems to ignore sync entirely:** it fails *silently* by design —
  if the Supabase project is unreachable, the app just falls back to
  local-only behavior exactly like sync was never turned on, so a typo in
  the URL won't ever crash or block the app. Reopen the Sync dialog to check
  the status line, or check the internet connection.
- **Wrong data got uploaded/adopted on first connect:** the first-connect
  rule is simple — if the cloud side is empty, that device's local data is
  uploaded to it; if the cloud side already has data, it replaces the local
  copy. If that went the wrong way once, you can always re-run the SQL
  file's `app_state` and `checklist_tasks` table creation again on a fresh
  Supabase project to start over.
