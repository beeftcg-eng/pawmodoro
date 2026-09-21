-- Pawmodoro cloud sync schema for Supabase.
--
-- Run this in your Supabase project's SQL Editor (Supabase dashboard ->
-- SQL Editor -> New query -> paste this whole file -> Run). Safe to
-- re-run in full any time (e.g. to pick up a migration added below) —
-- every statement is idempotent (if-not-exists / drop-then-create), so
-- an existing project won't error out partway through and skip later
-- statements like it used to.
--
-- This mirrors the exact XP/level/quest math from gamification.py and
-- storage.py, so the desktop app and the web app behave identically
-- whether they're online (server is authoritative) or the desktop app is
-- offline (falls back to its own local copy of the same logic).

create extension if not exists pgcrypto;

-- ============================================================
-- Tables
-- ============================================================

-- Reference data: the pool of possible daily/weekly quests. Read-only at
-- runtime; ensure_daily_quests()/ensure_weekly_quests() pick 3 at random
-- from here, same as gamification.QUEST_POOL / WEEKLY_QUEST_POOL.
create table if not exists quest_templates (
  id text primary key,
  list_type text not null check (list_type in ('daily', 'weekly')),
  kind text not null,
  target int not null,
  description text not null
);

insert into quest_templates (id, list_type, kind, target, description) values
  ('pomodoros_2', 'daily', 'pomodoros', 2, 'Complete 2 pomodoro sessions'),
  ('pomodoros_4', 'daily', 'pomodoros', 4, 'Complete 4 pomodoro sessions'),
  ('pomodoros_6', 'daily', 'pomodoros', 6, 'Complete 6 pomodoro sessions'),
  ('tasks_2', 'daily', 'tasks', 2, 'Finish 2 checklist tasks'),
  ('tasks_3', 'daily', 'tasks', 3, 'Finish 3 checklist tasks'),
  ('tasks_5', 'daily', 'tasks', 5, 'Finish 5 checklist tasks'),
  ('breaks_1', 'daily', 'breaks', 1, 'Take at least one break'),
  ('breaks_2', 'daily', 'breaks', 2, 'Take 2 breaks'),
  ('clear_checklist', 'daily', 'clear_checklist', 1, 'Clear your whole checklist for today'),
  ('week_pomodoros_15', 'weekly', 'pomodoros', 15, 'Complete 15 pomodoro sessions this week'),
  ('week_pomodoros_25', 'weekly', 'pomodoros', 25, 'Complete 25 pomodoro sessions this week'),
  ('week_pomodoros_35', 'weekly', 'pomodoros', 35, 'Complete 35 pomodoro sessions this week'),
  ('week_tasks_15', 'weekly', 'tasks', 15, 'Finish 15 checklist tasks this week'),
  ('week_tasks_25', 'weekly', 'tasks', 25, 'Finish 25 checklist tasks this week'),
  ('week_breaks_5', 'weekly', 'breaks', 5, 'Take 5 breaks this week'),
  ('week_breaks_8', 'weekly', 'breaks', 8, 'Take 8 breaks this week'),
  ('week_clear_checklist_3', 'weekly', 'clear_checklist', 3, 'Clear your whole checklist 3 times this week')
on conflict (id) do nothing;

alter table quest_templates enable row level security;
drop policy if exists "quest_templates readable by anyone signed in" on quest_templates;
create policy "quest_templates readable by anyone signed in"
  on quest_templates for select
  using (auth.role() = 'authenticated');

-- One row per logged-in user: XP, streaks, notes, and the two active quest
-- lists (stored as JSON arrays, same shape as the desktop app's data.json).
create table if not exists app_state (
  user_id uuid primary key references auth.users(id) on delete cascade,
  notes text not null default '',
  xp int not null default 0,
  total_pomodoros int not null default 0,
  total_tasks int not null default 0,
  current_streak int not null default 0,
  longest_streak int not null default 0,
  last_active_date date,
  quests_date date,
  quests jsonb not null default '[]',
  weekly_quests_start date,
  weekly_quests jsonb not null default '[]',
  updated_at timestamptz not null default now()
);

alter table app_state enable row level security;
drop policy if exists "own app_state row" on app_state;
create policy "own app_state row"
  on app_state for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- Many rows per user: the recurring checklist. `source` separates cards
-- pushed from the Deckbuilder wishlist from the regular checklist, so
-- they can be shown in their own section without mixing into (or
-- counting toward) the regular list.
create table if not exists checklist_tasks (
  id text primary key default encode(gen_random_bytes(6), 'hex'),
  user_id uuid not null references auth.users(id) on delete cascade,
  text text not null,
  recurrence text not null check (recurrence in ('daily', 'weekly', 'once')),
  last_completed date,
  completed_today boolean not null default false,
  reminder_time text,
  last_reminded date,
  sort_order double precision not null default 0,
  source text not null default 'checklist' check (source in ('checklist', 'wishlist')),
  created_at timestamptz not null default now()
);

alter table checklist_tasks enable row level security;
drop policy if exists "own checklist rows" on checklist_tasks;
create policy "own checklist rows"
  on checklist_tasks for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- Migration for a project that already ran this file before `source`
-- existed — a no-op on a fresh install where the column already came
-- from the create table above.
alter table checklist_tasks add column if not exists source text not null default 'checklist';
alter table checklist_tasks drop constraint if exists checklist_tasks_source_check;
alter table checklist_tasks add constraint checklist_tasks_source_check check (source in ('checklist', 'wishlist'));

-- v2.9 migrations (all no-ops when already applied):
--  * tz_offset_min: the client's UTC offset in minutes, so "today" (daily
--    task reset, quest day, streak day) follows YOUR local midnight instead
--    of the server's UTC midnight. See user_today() below.
--  * awarded_on: the last day this task paid out XP/quest progress, so
--    un-checking and re-checking a task can't be farmed for XP.
alter table app_state add column if not exists tz_offset_min int not null default 0;
alter table checklist_tasks add column if not exists awarded_on date;

-- One row per user per local day: feeds the Progress tab's history chart and
-- the household "this week" totals.
create table if not exists daily_stats (
  user_id uuid not null references auth.users(id) on delete cascade,
  day date not null,
  pomodoros int not null default 0,
  focus_min int not null default 0,
  tasks int not null default 0,
  primary key (user_id, day)
);

alter table daily_stats enable row level security;
drop policy if exists "own daily_stats rows" on daily_stats;
create policy "own daily_stats rows"
  on daily_stats for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- ============================================================
-- Household (shared list for two people)
-- ============================================================
-- A household is a small group (capped at 4) that shares one to-do list
-- (groceries, chores) and can see each other's level/streak/weekly totals.
-- Everything else (notes, personal checklist, XP) stays private per account.
-- Nothing here is writable directly: clients go through the household_* and
-- shared_* functions below.

create table if not exists households (
  id uuid primary key default gen_random_uuid(),
  invite_code text not null unique default upper(encode(gen_random_bytes(5), 'hex')),
  created_at timestamptz not null default now()
);

-- user_id is the primary key: an account belongs to at most one household.
create table if not exists household_members (
  user_id uuid primary key references auth.users(id) on delete cascade,
  household_id uuid not null references households(id) on delete cascade,
  display_name text not null default '',
  joined_at timestamptz not null default now()
);

create table if not exists shared_tasks (
  id text primary key default encode(gen_random_bytes(6), 'hex'),
  household_id uuid not null references households(id) on delete cascade,
  text text not null,
  done boolean not null default false,
  done_by uuid references auth.users(id) on delete set null,
  done_at timestamptz,
  sort_order double precision not null default 0,
  created_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now()
);

-- v2.11 migrations (no-ops when already applied): scheduled shared items.
--  * recurrence: 'once' (optionally with an exact date + time), 'daily',
--    'weekly' (on `weekday`, 0=Monday..6=Sunday -- "every Tuesday") or
--    'monthly' (on `month_day`; short months use their last day).
--  * due_time: "HH:MM", the time of day the item is for (all recurrences).
-- A ticked repeating item un-ticks itself once it comes due again; see
-- shared_last_occurrence() / roll_shared_tasks() below (mirrors
-- shared_schedule.py).
alter table shared_tasks add column if not exists recurrence text not null default 'once';
alter table shared_tasks add column if not exists weekday int;
alter table shared_tasks add column if not exists month_day int;
alter table shared_tasks add column if not exists due_date date;
alter table shared_tasks add column if not exists due_time text;
alter table shared_tasks drop constraint if exists shared_tasks_schedule_check;
alter table shared_tasks add constraint shared_tasks_schedule_check check (
  recurrence in ('once', 'daily', 'weekly', 'monthly')
  and (weekday is null or weekday between 0 and 6)
  and (month_day is null or month_day between 1 and 31)
  and (due_time is null or due_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$')
);

-- security definer so the membership lookup inside the RLS policies below
-- doesn't itself trip over household_members' own RLS (infinite recursion).
create or replace function is_household_member(hid uuid) returns boolean
language sql stable security definer set search_path = public, extensions as $$
  select exists (select 1 from household_members where household_id = hid and user_id = auth.uid());
$$;

create or replace function my_household_id() returns uuid
language sql stable security definer set search_path = public, extensions as $$
  select household_id from household_members where user_id = auth.uid();
$$;

alter table households enable row level security;
drop policy if exists "members read their household" on households;
create policy "members read their household"
  on households for select
  using (is_household_member(id));

alter table household_members enable row level security;
drop policy if exists "members read their household roster" on household_members;
create policy "members read their household roster"
  on household_members for select
  using (is_household_member(household_id));

alter table shared_tasks enable row level security;
drop policy if exists "members manage shared tasks" on shared_tasks;
create policy "members manage shared tasks"
  on shared_tasks for all
  using (is_household_member(household_id))
  with check (is_household_member(household_id));

-- ============================================================
-- Pure helper functions (mirror gamification.py exactly)
-- ============================================================

create or replace function level_from_xp(total_xp int)
returns table(level int, xp_into int, xp_needed int)
language plpgsql immutable as $$
declare
  lvl int := 1;
  remaining int := total_xp;
  needed int;
begin
  needed := 80 + (lvl - 1) * 20;
  while remaining >= needed loop
    remaining := remaining - needed;
    lvl := lvl + 1;
    needed := 80 + (lvl - 1) * 20;
  end loop;
  level := lvl;
  xp_into := remaining;
  xp_needed := needed;
  return next;
end;
$$;

-- Quest week resets on Tuesday. Postgres' extract(dow) is Sunday=0..
-- Saturday=6; converted here to Python's Monday=0..Sunday=6 convention so
-- this matches gamification.week_start_for() exactly.
create or replace function week_start_for(d date) returns date
language sql immutable as $$
  select d - ((((extract(dow from d)::int + 6) % 7) - 1 + 7) % 7);
$$;

-- "Today" for this user, at THEIR local midnight rather than the server's
-- (UTC) one. Clients report their UTC offset via set_tz_offset() (it's
-- re-sent whenever it changes, so daylight-saving shifts follow along).
-- Every daily/weekly rollover below (task reset, quest day, streak day)
-- uses this instead of current_date -- otherwise, at UTC-6 for example,
-- everything would roll over at 6pm.
create or replace function user_today() returns date
language sql stable security invoker set search_path = public, extensions as $$
  select ((now() at time zone 'utc')
          + coalesce((select tz_offset_min from app_state where user_id = auth.uid()), 0) * interval '1 minute')::date;
$$;

create or replace function set_tz_offset(p_minutes int) returns void
language plpgsql security invoker set search_path = public, extensions as $$
begin
  perform ensure_app_state();
  update app_state set tz_offset_min = greatest(-840, least(840, p_minutes)) where user_id = auth.uid();
end;
$$;

-- ============================================================
-- State-mutating functions (all run as the calling user; RLS applies)
-- ============================================================

create or replace function ensure_app_state() returns app_state
language plpgsql security invoker set search_path = public, extensions as $$
declare
  s app_state;
begin
  select * into s from app_state where user_id = auth.uid();
  if not found then
    insert into app_state (user_id) values (auth.uid()) returning * into s;
  end if;
  return s;
end;
$$;

create or replace function ensure_daily_quests() returns void
language plpgsql security invoker set search_path = public, extensions as $$
declare
  s app_state;
  today date := user_today();
  picked jsonb;
begin
  s := ensure_app_state();
  if s.quests_date is distinct from today then
    select coalesce(jsonb_agg(jsonb_build_object(
        'id', id, 'kind', kind, 'target', target, 'desc', description,
        'progress', 0, 'completed', false)), '[]'::jsonb)
      into picked
      from (select * from quest_templates where list_type = 'daily' order by random() limit 3) t;
    update app_state set quests_date = today, quests = picked, updated_at = now()
      where user_id = auth.uid();
  end if;
end;
$$;

create or replace function ensure_weekly_quests() returns void
language plpgsql security invoker set search_path = public, extensions as $$
declare
  s app_state;
  wk date := week_start_for(user_today());
  picked jsonb;
begin
  s := ensure_app_state();
  if s.weekly_quests_start is distinct from wk then
    select coalesce(jsonb_agg(jsonb_build_object(
        'id', id, 'kind', kind, 'target', target, 'desc', description,
        'progress', 0, 'completed', false)), '[]'::jsonb)
      into picked
      from (select * from quest_templates where list_type = 'weekly' order by random() limit 3) t;
    update app_state set weekly_quests_start = wk, weekly_quests = picked, updated_at = now()
      where user_id = auth.uid();
  end if;
end;
$$;

create or replace function add_xp(amount int) returns void
language plpgsql security invoker set search_path = public, extensions as $$
begin
  perform ensure_daily_quests();
  perform ensure_weekly_quests();
  update app_state set xp = greatest(0, xp + amount), updated_at = now()
    where user_id = auth.uid();
end;
$$;

create or replace function bump_streak() returns void
language plpgsql security invoker set search_path = public, extensions as $$
declare
  s app_state;
  today date := user_today();
  yesterday date := user_today() - 1;
  new_streak int;
begin
  select * into s from app_state where user_id = auth.uid();
  if s.last_active_date = today then
    return;
  end if;
  new_streak := case when s.last_active_date = yesterday then coalesce(s.current_streak, 0) + 1 else 1 end;
  update app_state set
    current_streak = new_streak,
    longest_streak = greatest(coalesce(longest_streak, 0), new_streak),
    last_active_date = today,
    updated_at = now()
  where user_id = auth.uid();
end;
$$;

-- Progresses every incomplete quest of `p_kind` in `p_quests` by
-- `p_amount`. Returns the updated list plus whichever quests were newly
-- completed by this call (mirrors Storage._advance_quest_list).
create or replace function advance_quest_list(p_quests jsonb, p_kind text, p_amount int, p_bonus_xp int)
returns table(new_list jsonb, newly_completed jsonb)
language plpgsql immutable as $$
declare
  q jsonb;
  result jsonb := '[]'::jsonb;
  completed jsonb := '[]'::jsonb;
  progress int;
  target int;
begin
  for q in select * from jsonb_array_elements(p_quests) loop
    if (q ->> 'kind') = p_kind and not (q ->> 'completed')::boolean then
      target := (q ->> 'target')::int;
      progress := least(target, (q ->> 'progress')::int + p_amount);
      q := jsonb_set(q, '{progress}', to_jsonb(progress));
      if progress >= target then
        q := jsonb_set(q, '{completed}', 'true'::jsonb);
        q := jsonb_set(q, '{bonus_xp}', to_jsonb(p_bonus_xp));
        completed := completed || jsonb_build_array(q);
      end if;
    end if;
    result := result || jsonb_build_array(q);
  end loop;
  new_list := result;
  newly_completed := completed;
  return next;
end;
$$;

-- Progresses every incomplete daily AND weekly quest of `p_kind`, awards
-- bonus XP for newly-completed quests (plus the "cleared the whole list"
-- bonus, only on the call that finishes it), returns the combined list of
-- newly-completed quests. Mirrors Storage._advance_quests.
create or replace function advance_quests(p_kind text, p_amount int) returns jsonb
language plpgsql security invoker set search_path = public, extensions as $$
declare
  s app_state;
  daily_result record;
  weekly_result record;
  bonus_total int := 0;
begin
  select * into s from app_state where user_id = auth.uid();

  select * into daily_result from advance_quest_list(s.quests, p_kind, p_amount, 30);
  select * into weekly_result from advance_quest_list(s.weekly_quests, p_kind, p_amount, 80);

  update app_state set quests = daily_result.new_list, weekly_quests = weekly_result.new_list, updated_at = now()
    where user_id = auth.uid();

  bonus_total := (select coalesce(sum((c ->> 'bonus_xp')::int), 0) from jsonb_array_elements(daily_result.newly_completed) c)
               + (select coalesce(sum((c ->> 'bonus_xp')::int), 0) from jsonb_array_elements(weekly_result.newly_completed) c);
  if bonus_total > 0 then
    perform add_xp(bonus_total);
  end if;

  if jsonb_array_length(daily_result.newly_completed) > 0
     and (select bool_and((e ->> 'completed')::boolean) from jsonb_array_elements(daily_result.new_list) e) then
    perform add_xp(50);
  end if;
  if jsonb_array_length(weekly_result.newly_completed) > 0
     and (select bool_and((e ->> 'completed')::boolean) from jsonb_array_elements(weekly_result.new_list) e) then
    perform add_xp(150);
  end if;

  return daily_result.newly_completed || weekly_result.newly_completed;
end;
$$;

-- Adds to today's row in daily_stats (creating it if needed).
create or replace function bump_daily_stats(p_pomodoros int, p_focus_min int, p_tasks int) returns void
language sql security invoker set search_path = public, extensions as $$
  insert into daily_stats (user_id, day, pomodoros, focus_min, tasks)
    values (auth.uid(), user_today(), p_pomodoros, p_focus_min, p_tasks)
  on conflict (user_id, day) do update set
    pomodoros = daily_stats.pomodoros + excluded.pomodoros,
    focus_min = daily_stats.focus_min + excluded.focus_min,
    tasks = greatest(0, daily_stats.tasks + excluded.tasks);
$$;

-- p_minutes is the length of the work session, for the focus-time history.
-- The old zero-argument version is dropped so the two can't both exist and
-- make a no-argument call ambiguous.
drop function if exists record_pomodoro_completed();
create or replace function record_pomodoro_completed(p_minutes int default 25) returns jsonb
language plpgsql security invoker set search_path = public, extensions as $$
declare
  s app_state;
  old_lvl record;
  new_lvl record;
  completed jsonb;
begin
  perform ensure_daily_quests();
  perform ensure_weekly_quests();
  perform bump_streak();
  perform bump_daily_stats(1, greatest(0, least(coalesce(p_minutes, 25), 600)), 0);

  select * into s from app_state where user_id = auth.uid();
  select * into old_lvl from level_from_xp(s.xp);

  update app_state set total_pomodoros = total_pomodoros + 1, updated_at = now()
    where user_id = auth.uid();

  perform add_xp(20);
  completed := advance_quests('pomodoros', 1);

  select * into s from app_state where user_id = auth.uid();
  select * into new_lvl from level_from_xp(s.xp);

  return jsonb_build_object(
    'xp_gained', 20,
    'old_level', old_lvl.level,
    'new_level', new_lvl.level,
    'completed_quests', completed
  );
end;
$$;

create or replace function record_break_completed() returns jsonb
language plpgsql security invoker set search_path = public, extensions as $$
declare
  completed jsonb;
begin
  perform ensure_daily_quests();
  perform ensure_weekly_quests();
  completed := advance_quests('breaks', 1);
  return jsonb_build_object('completed_quests', completed);
end;
$$;

-- Flips just the checklist row (no XP/quest side effects). Split out from
-- the XP logic below so the desktop app, which makes these as two
-- separate local calls (set_task_done then record_task_event), can call
-- through to the same two steps remotely instead of needing one combined
-- RPC with a different shape than its existing code.
create or replace function set_task_completed_flag(p_task_id text, p_done boolean) returns checklist_tasks
language plpgsql security invoker set search_path = public, extensions as $$
declare
  t checklist_tasks;
begin
  update checklist_tasks set
    completed_today = p_done,
    last_completed = case when p_done then user_today() else last_completed end
  where id = p_task_id and user_id = auth.uid()
  returning * into t;
  if not found then
    raise exception 'task not found';
  end if;
  return t;
end;
$$;

-- The XP/streak/quest payout for completing a task of the given recurrence.
-- Assumes the checklist row was already flipped by set_task_completed_flag,
-- since the "cleared the whole checklist" check reads current
-- checklist_tasks state. Mirrors Storage._award_task.
create or replace function award_task(p_recurrence text) returns jsonb
language plpgsql security invoker set search_path = public, extensions as $$
declare
  task_xp int;
  old_lvl record;
  new_lvl record;
  completed jsonb := '[]'::jsonb;
  total_tasks_count int;
  done_tasks_count int;
  cur_xp int;
begin
  perform ensure_daily_quests();
  perform ensure_weekly_quests();

  task_xp := case p_recurrence when 'daily' then 10 when 'weekly' then 15 when 'once' then 25 else 10 end;

  perform bump_streak();
  update app_state set total_tasks = total_tasks + 1, updated_at = now() where user_id = auth.uid();
  perform bump_daily_stats(0, 0, 1);

  select xp into cur_xp from app_state where user_id = auth.uid();
  select * into old_lvl from level_from_xp(cur_xp);

  perform add_xp(task_xp);
  completed := advance_quests('tasks', 1);

  select count(*), count(*) filter (where completed_today) into total_tasks_count, done_tasks_count
    from checklist_tasks where user_id = auth.uid() and source = 'checklist';
  if total_tasks_count > 0 and total_tasks_count = done_tasks_count then
    completed := completed || advance_quests('clear_checklist', 1);
  end if;

  select xp into cur_xp from app_state where user_id = auth.uid();
  select * into new_lvl from level_from_xp(cur_xp);

  return jsonb_build_object('xp_gained', task_xp, 'old_level', old_lvl.level, 'new_level', new_lvl.level, 'completed_quests', completed);
end;
$$;

-- Legacy entry point, kept only so desktop apps older than v2.9 (which call
-- set_task_completed_flag + apply_task_xp separately) keep working. New
-- clients use complete_task below, which also blocks XP farming.
create or replace function apply_task_xp(p_recurrence text, p_done boolean) returns jsonb
language plpgsql security invoker set search_path = public, extensions as $$
begin
  if p_done then
    return award_task(p_recurrence);
  end if;
  perform ensure_daily_quests();
  perform ensure_weekly_quests();
  update app_state set total_tasks = greatest(0, total_tasks - 1), updated_at = now() where user_id = auth.uid();
  perform add_xp(-case p_recurrence when 'daily' then 10 when 'weekly' then 15 when 'once' then 25 else 10 end);
  return jsonb_build_object('completed_quests', '[]'::jsonb);
end;
$$;

-- Ticks (or un-ticks) a task. A task pays out XP/quest progress only the
-- FIRST time it's completed in its period (once: ever; weekly: per quest
-- week; daily: per day) and un-ticking never takes XP back -- so
-- check/uncheck/check can't be farmed. Mirrors Storage.set_task_done and
-- gamification.task_already_awarded.
create or replace function complete_task(p_task_id text, p_done boolean) returns jsonb
language plpgsql security invoker set search_path = public, extensions as $$
declare
  t checklist_tasks;
  today date := user_today();
  already boolean;
begin
  t := set_task_completed_flag(p_task_id, p_done);
  if not p_done then
    return jsonb_build_object('completed_quests', '[]'::jsonb);
  end if;

  already := case t.recurrence
    when 'once' then t.awarded_on is not null
    when 'weekly' then t.awarded_on is not null and t.awarded_on >= week_start_for(today)
    else t.awarded_on is not null and t.awarded_on = today
  end;
  if already then
    return jsonb_build_object('completed_quests', '[]'::jsonb);
  end if;

  update checklist_tasks set awarded_on = today where id = t.id and user_id = auth.uid();
  return award_task(t.recurrence);
end;
$$;

-- Daily tasks un-check at the start of each (local) day; weekly tasks
-- un-check when the quest week rolls over (Tuesday), the same reset as the
-- weekly quests. Mirrors Storage._roll_recurring_tasks.
create or replace function roll_recurring_tasks() returns void
language sql security invoker set search_path = public, extensions as $$
  update checklist_tasks set completed_today = false
  where user_id = auth.uid()
    and completed_today = true
    and (
      (recurrence = 'daily' and last_completed is distinct from user_today())
      or (recurrence = 'weekly' and (last_completed is null or last_completed < week_start_for(user_today())))
    );
$$;

create or replace function set_notes(p_notes text) returns void
language sql security invoker set search_path = public, extensions as $$
  update app_state set notes = p_notes, updated_at = now() where user_id = auth.uid();
$$;

-- p_id lets the desktop app (which queues edits made offline) create a task
-- under the id it already gave it locally; calling again with an id that
-- exists just returns the existing row, so a retried request is harmless.
-- The old 4-argument version is dropped so the two can't be ambiguous.
drop function if exists add_task(text, text, text, text);
create or replace function add_task(p_text text, p_recurrence text, p_reminder_time text default null, p_source text default 'checklist', p_id text default null)
returns checklist_tasks
language plpgsql security invoker set search_path = public, extensions as $$
declare
  t checklist_tasks;
  next_order double precision;
begin
  if p_id is not null then
    select * into t from checklist_tasks where id = p_id and user_id = auth.uid();
    if found then
      return t;
    end if;
  end if;
  select coalesce(max(sort_order), 0) + 1 into next_order from checklist_tasks where user_id = auth.uid();
  insert into checklist_tasks (id, user_id, text, recurrence, reminder_time, sort_order, source)
    values (coalesce(p_id, encode(gen_random_bytes(6), 'hex')), auth.uid(), p_text, p_recurrence, p_reminder_time, next_order, p_source)
    returning * into t;
  return t;
end;
$$;

create or replace function rename_task(p_task_id text, p_text text) returns void
language sql security invoker set search_path = public, extensions as $$
  update checklist_tasks set text = p_text where id = p_task_id and user_id = auth.uid();
$$;

create or replace function remove_task(p_task_id text) returns void
language sql security invoker set search_path = public, extensions as $$
  delete from checklist_tasks where id = p_task_id and user_id = auth.uid();
$$;

-- Persists a full reordering of the checklist: p_ordered_ids is a JSON
-- array of this user's task ids in the desired new order (a jsonb param
-- rather than text[] — plain arrays need `[]` in the signature, which at
-- least one SQL editor has been seen to mangle on paste). An id that
-- doesn't belong to (or no longer exists for) this user is simply
-- ignored, rather than erroring, so a stale list from a slow client can't
-- fail the whole call.
create or replace function reorder_tasks(p_ordered_ids jsonb) returns void
language sql security invoker set search_path = public, extensions as $$
  update checklist_tasks t set sort_order = x.ord
    from jsonb_array_elements_text(p_ordered_ids) with ordinality as x(id, ord)
    where t.id = x.id and t.user_id = auth.uid();
$$;

create or replace function set_task_reminder(p_task_id text, p_reminder_time text) returns void
language sql security invoker set search_path = public, extensions as $$
  update checklist_tasks set reminder_time = p_reminder_time, last_reminded = null
  where id = p_task_id and user_id = auth.uid();
$$;

-- One-time bootstrap: seeds this user's cloud row with the desktop app's
-- existing local progress (only meant to be called once, the first time
-- sync is turned on and the cloud side is still empty).
create or replace function import_state(
  p_notes text, p_xp int, p_total_pomodoros int, p_total_tasks int,
  p_current_streak int, p_longest_streak int, p_last_active_date date
) returns void
language plpgsql security invoker set search_path = public, extensions as $$
begin
  perform ensure_app_state();
  update app_state set
    notes = p_notes,
    xp = p_xp,
    total_pomodoros = p_total_pomodoros,
    total_tasks = p_total_tasks,
    current_streak = p_current_streak,
    longest_streak = p_longest_streak,
    last_active_date = p_last_active_date,
    updated_at = now()
  where user_id = auth.uid();
end;
$$;

-- One call to fetch everything the web app / desktop app needs, after
-- rolling over daily tasks and making sure quest lists are fresh.
create or replace function sync_pull() returns jsonb
language plpgsql security invoker set search_path = public, extensions as $$
declare
  s app_state;
  tasks jsonb;
  hist jsonb;
begin
  perform roll_recurring_tasks();
  perform ensure_daily_quests();
  perform ensure_weekly_quests();

  select * into s from app_state where user_id = auth.uid();
  select coalesce(jsonb_agg(to_jsonb(c) - 'user_id' order by c.sort_order, c.created_at), '[]'::jsonb) into tasks
    from checklist_tasks c where user_id = auth.uid();
  select coalesce(jsonb_agg(jsonb_build_object(
      'day', d.day, 'pomodoros', d.pomodoros, 'focus_min', d.focus_min, 'tasks', d.tasks) order by d.day), '[]'::jsonb) into hist
    from daily_stats d where d.user_id = auth.uid() and d.day >= user_today() - 41;

  return jsonb_build_object(
    'notes', s.notes,
    'xp', s.xp,
    'total_pomodoros', s.total_pomodoros,
    'total_tasks', s.total_tasks,
    'current_streak', s.current_streak,
    'longest_streak', s.longest_streak,
    'quests', s.quests,
    'weekly_quests', s.weekly_quests,
    'checklist', tasks,
    'history', hist
  );
end;
$$;

-- ============================================================
-- Household functions
-- ============================================================

-- The most recent day on or before p_today that a repeating shared item came
-- due (null for a one-off). Mirrors shared_schedule.last_occurrence().
create or replace function shared_last_occurrence(p_recurrence text, p_weekday int, p_month_day int, p_today date)
returns date
language plpgsql immutable set search_path = public, extensions as $$
declare
  this_first date := date_trunc('month', p_today::timestamp)::date;
  prev_first date := (date_trunc('month', p_today::timestamp) - interval '1 month')::date;
  candidate date;
begin
  if p_recurrence = 'daily' then
    return p_today;
  elsif p_recurrence = 'weekly' and p_weekday is not null then
    return p_today - (((extract(isodow from p_today)::int - 1) - p_weekday + 7) % 7);
  elsif p_recurrence = 'monthly' and p_month_day is not null then
    candidate := this_first + (least(p_month_day, (this_first + interval '1 month - 1 day')::date - this_first + 1) - 1);
    if candidate <= p_today then
      return candidate;
    end if;
    return prev_first + (least(p_month_day, this_first - prev_first) - 1);
  end if;
  return null;
end;
$$;

-- Un-ticks the household's repeating items that have come due again since
-- they were ticked (in the caller's local day, see user_today()). Runs at
-- the start of every household_pull, so there is no scheduled job. One-off
-- items are never touched.
create or replace function roll_shared_tasks(hid uuid) returns void
language plpgsql security definer set search_path = public, extensions as $$
declare
  today date := user_today();
  offset_min int := coalesce((select tz_offset_min from app_state where user_id = auth.uid()), 0);
begin
  -- callable as an RPC, so never act on a household that isn't the caller's
  if hid is distinct from my_household_id() then
    return;
  end if;
  update shared_tasks t set done = false, done_by = null, done_at = null
  where t.household_id = hid and t.done and t.recurrence <> 'once'
    and (
      t.done_at is null
      or ((t.done_at at time zone 'utc') + offset_min * interval '1 minute')::date
         < shared_last_occurrence(t.recurrence, t.weekday, t.month_day, today)
    );
end;
$$;

-- Rejects a schedule the CHECK constraint or the UI would never produce, with
-- a readable message.
create or replace function shared_check_schedule(p_recurrence text, p_weekday int, p_month_day int, p_due_time text)
returns void
language plpgsql immutable set search_path = public, extensions as $$
begin
  if p_recurrence not in ('once', 'daily', 'weekly', 'monthly') then
    raise exception 'invalid repeat: %', p_recurrence;
  end if;
  if p_recurrence = 'weekly' and (p_weekday is null or p_weekday not between 0 and 6) then
    raise exception 'pick a day of the week';
  end if;
  if p_recurrence = 'monthly' and (p_month_day is null or p_month_day not between 1 and 31) then
    raise exception 'pick a day of the month';
  end if;
  if p_due_time is not null and p_due_time !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' then
    raise exception 'invalid time: %', p_due_time;
  end if;
end;
$$;

-- Everything the household UI needs in one call: the roster (with each
-- member's level inputs, streak and this-week totals) plus the shared list.
-- Returns null when the caller isn't in a household. security definer
-- because it reads the OTHER members' app_state/daily_stats rows, which
-- RLS otherwise hides -- it only ever does so for the caller's own household.
create or replace function household_pull() returns jsonb
language plpgsql security definer set search_path = public, extensions as $$
declare
  hid uuid;
  wk date;
  result jsonb;
begin
  if auth.uid() is null then
    raise exception 'not signed in';
  end if;
  hid := my_household_id();
  if hid is null then
    return null;
  end if;
  wk := week_start_for(user_today());
  perform roll_shared_tasks(hid);

  select jsonb_build_object(
    'id', h.id,
    'invite_code', h.invite_code,
    'members', (
      select coalesce(jsonb_agg(jsonb_build_object(
          'user_id', m.user_id,
          'name', m.display_name,
          'is_me', m.user_id = auth.uid(),
          'xp', coalesce(a.xp, 0),
          'streak', coalesce(a.current_streak, 0),
          'week_pomodoros', coalesce(w.pomodoros, 0),
          'week_focus_min', coalesce(w.focus, 0),
          'week_tasks', coalesce(w.tasks, 0)
        ) order by m.joined_at), '[]'::jsonb)
      from household_members m
      left join app_state a on a.user_id = m.user_id
      left join lateral (
        select sum(d.pomodoros)::int as pomodoros, sum(d.focus_min)::int as focus, sum(d.tasks)::int as tasks
        from daily_stats d where d.user_id = m.user_id and d.day >= wk
      ) w on true
      where m.household_id = h.id
    ),
    'tasks', (
      select coalesce(jsonb_agg(jsonb_build_object(
          'id', t.id, 'text', t.text, 'done', t.done, 'done_at', t.done_at,
          'recurrence', t.recurrence, 'weekday', t.weekday, 'month_day', t.month_day,
          'due_date', t.due_date, 'due_time', t.due_time,
          'done_by_name', (select display_name from household_members where user_id = t.done_by),
          -- v2.13: who added / ticked an item, so the desktop app can notify you about the
          -- OTHER members' changes (shared_activity.py). Older clients ignore the extra keys.
          'created_by_name', (select display_name from household_members where user_id = t.created_by),
          'created_by_me', coalesce(t.created_by = auth.uid(), false),
          'done_by_me', coalesce(t.done_by = auth.uid(), false)
        ) order by t.sort_order, t.created_at), '[]'::jsonb)
      from shared_tasks t where t.household_id = h.id
    )
  ) into result
  from households h where h.id = hid;
  return result;
end;
$$;

create or replace function household_create(p_name text) returns jsonb
language plpgsql security definer set search_path = public, extensions as $$
declare
  hid uuid;
begin
  if auth.uid() is null then
    raise exception 'not signed in';
  end if;
  if my_household_id() is not null then
    raise exception 'already in a household';
  end if;
  insert into households default values returning id into hid;
  insert into household_members (user_id, household_id, display_name)
    values (auth.uid(), hid, left(coalesce(nullif(trim(p_name), ''), 'Me'), 40));
  return household_pull();
end;
$$;

create or replace function household_join(p_code text, p_name text) returns jsonb
language plpgsql security definer set search_path = public, extensions as $$
declare
  hid uuid;
  mine uuid;
begin
  if auth.uid() is null then
    raise exception 'not signed in';
  end if;
  select id into hid from households where invite_code = upper(trim(p_code));
  if hid is null then
    raise exception 'invalid invite code';
  end if;
  mine := my_household_id();
  if mine is not null then
    if mine = hid then
      return household_pull();
    end if;
    raise exception 'already in a household';
  end if;
  if (select count(*) from household_members where household_id = hid) >= 4 then
    raise exception 'household is full';
  end if;
  insert into household_members (user_id, household_id, display_name)
    values (auth.uid(), hid, left(coalesce(nullif(trim(p_name), ''), 'Me'), 40));
  return household_pull();
end;
$$;

-- Leaves the household; the last person out deletes it (and its shared list).
create or replace function household_leave() returns void
language plpgsql security definer set search_path = public, extensions as $$
declare
  hid uuid;
begin
  if auth.uid() is null then
    raise exception 'not signed in';
  end if;
  hid := my_household_id();
  if hid is null then
    return;
  end if;
  delete from household_members where user_id = auth.uid();
  if not exists (select 1 from household_members where household_id = hid) then
    delete from households where id = hid;
  end if;
end;
$$;

-- The shared-list functions run as the caller (security invoker); the RLS
-- policy on shared_tasks is what actually limits them to the caller's own
-- household.
-- v2.11 gave this function schedule parameters; drop the old signature so
-- the two overloads don't become ambiguous to PostgREST.
drop function if exists shared_add_task(text, text);
create or replace function shared_add_task(
  p_text text, p_id text default null,
  p_recurrence text default 'once', p_weekday int default null, p_month_day int default null,
  p_due_date date default null, p_due_time text default null
) returns shared_tasks
language plpgsql security invoker set search_path = public, extensions as $$
declare
  hid uuid := my_household_id();
  t shared_tasks;
  next_order double precision;
begin
  if hid is null then
    raise exception 'not in a household';
  end if;
  if p_id is not null then
    select * into t from shared_tasks where id = p_id and household_id = hid;
    if found then
      return t;
    end if;
  end if;
  perform shared_check_schedule(p_recurrence, p_weekday, p_month_day, p_due_time);
  select coalesce(max(sort_order), 0) + 1 into next_order from shared_tasks where household_id = hid;
  insert into shared_tasks (id, household_id, text, sort_order, created_by,
                            recurrence, weekday, month_day, due_date, due_time)
    values (coalesce(p_id, encode(gen_random_bytes(6), 'hex')), hid, p_text, next_order, auth.uid(),
            p_recurrence,
            case when p_recurrence = 'weekly' then p_weekday end,
            case when p_recurrence = 'monthly' then p_month_day end,
            case when p_recurrence = 'once' then p_due_date end,
            p_due_time)
    returning * into t;
  return t;
end;
$$;

create or replace function shared_set_schedule(
  p_id text, p_recurrence text, p_weekday int default null, p_month_day int default null,
  p_due_date date default null, p_due_time text default null
) returns void
language plpgsql security invoker set search_path = public, extensions as $$
begin
  perform shared_check_schedule(p_recurrence, p_weekday, p_month_day, p_due_time);
  update shared_tasks set
    recurrence = p_recurrence,
    weekday = case when p_recurrence = 'weekly' then p_weekday end,
    month_day = case when p_recurrence = 'monthly' then p_month_day end,
    due_date = case when p_recurrence = 'once' then p_due_date end,
    due_time = p_due_time
  where id = p_id and household_id = my_household_id();
  if not found then
    raise exception 'task not found';
  end if;
end;
$$;

-- p_ordered_ids: every shared item's id, in the new order. An item missing
-- from the list (one a partner added a moment ago) is pushed to the end.
create or replace function shared_reorder(p_ordered_ids jsonb) returns void
language plpgsql security invoker set search_path = public, extensions as $$
declare
  hid uuid := my_household_id();
  n int := jsonb_array_length(p_ordered_ids);
begin
  update shared_tasks t set sort_order = x.ord
    from jsonb_array_elements_text(p_ordered_ids) with ordinality as x(id, ord)
    where t.id = x.id and t.household_id = hid;
  update shared_tasks set sort_order = sort_order + n
    where household_id = hid and id not in (select jsonb_array_elements_text(p_ordered_ids));
end;
$$;

create or replace function shared_set_done(p_id text, p_done boolean) returns void
language plpgsql security invoker set search_path = public, extensions as $$
begin
  update shared_tasks set
    done = p_done,
    done_by = case when p_done then auth.uid() else null end,
    done_at = case when p_done then now() else null end
  where id = p_id and household_id = my_household_id();
  if not found then
    raise exception 'task not found';
  end if;
end;
$$;

create or replace function shared_remove_task(p_id text) returns void
language sql security invoker set search_path = public, extensions as $$
  delete from shared_tasks where id = p_id and household_id = my_household_id();
$$;

-- Only one-off items: a repeating item that's ticked is just waiting to come
-- due again.
create or replace function shared_clear_done() returns void
language sql security invoker set search_path = public, extensions as $$
  delete from shared_tasks where household_id = my_household_id() and done and recurrence = 'once';
$$;
