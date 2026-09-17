-- Pawmodoro cloud sync schema for Supabase.
--
-- Run this ONCE in your Supabase project's SQL Editor (Supabase dashboard
-- -> SQL Editor -> New query -> paste this whole file -> Run).
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
create table quest_templates (
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
  ('week_clear_checklist_3', 'weekly', 'clear_checklist', 3, 'Clear your whole checklist 3 times this week');

alter table quest_templates enable row level security;
create policy "quest_templates readable by anyone signed in"
  on quest_templates for select
  using (auth.role() = 'authenticated');

-- One row per logged-in user: XP, streaks, notes, and the two active quest
-- lists (stored as JSON arrays, same shape as the desktop app's data.json).
create table app_state (
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
create policy "own app_state row"
  on app_state for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- Many rows per user: the recurring checklist.
create table checklist_tasks (
  id text primary key default encode(gen_random_bytes(6), 'hex'),
  user_id uuid not null references auth.users(id) on delete cascade,
  text text not null,
  recurrence text not null check (recurrence in ('daily', 'weekly', 'once')),
  last_completed date,
  completed_today boolean not null default false,
  reminder_time text,
  last_reminded date,
  sort_order double precision not null default 0,
  created_at timestamptz not null default now()
);

alter table checklist_tasks enable row level security;
create policy "own checklist rows"
  on checklist_tasks for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

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

-- ============================================================
-- State-mutating functions (all run as the calling user; RLS applies)
-- ============================================================

create or replace function ensure_app_state() returns app_state
language plpgsql security invoker as $$
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
language plpgsql security invoker as $$
declare
  s app_state;
  today date := current_date;
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
language plpgsql security invoker as $$
declare
  s app_state;
  wk date := week_start_for(current_date);
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
language plpgsql security invoker as $$
begin
  perform ensure_daily_quests();
  perform ensure_weekly_quests();
  update app_state set xp = greatest(0, xp + amount), updated_at = now()
    where user_id = auth.uid();
end;
$$;

create or replace function bump_streak() returns void
language plpgsql security invoker as $$
declare
  s app_state;
  today date := current_date;
  yesterday date := current_date - 1;
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
language plpgsql security invoker as $$
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

create or replace function record_pomodoro_completed() returns jsonb
language plpgsql security invoker as $$
declare
  s app_state;
  old_lvl record;
  new_lvl record;
  completed jsonb;
begin
  perform ensure_daily_quests();
  perform ensure_weekly_quests();
  perform bump_streak();

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
language plpgsql security invoker as $$
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
language plpgsql security invoker as $$
declare
  t checklist_tasks;
begin
  update checklist_tasks set
    completed_today = p_done,
    last_completed = case when p_done then current_date else last_completed end
  where id = p_task_id and user_id = auth.uid()
  returning * into t;
  if not found then
    raise exception 'task not found';
  end if;
  return t;
end;
$$;

-- XP/streak/quest side effects for completing (or un-completing) a task
-- of the given recurrence. Assumes the checklist row was already flipped
-- by set_task_completed_flag, since the "cleared the whole checklist"
-- check reads current checklist_tasks state. Mirrors
-- Storage.record_task_event exactly.
create or replace function apply_task_xp(p_recurrence text, p_done boolean) returns jsonb
language plpgsql security invoker as $$
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

  if p_done then
    perform bump_streak();
    update app_state set total_tasks = total_tasks + 1, updated_at = now() where user_id = auth.uid();

    select xp into cur_xp from app_state where user_id = auth.uid();
    select * into old_lvl from level_from_xp(cur_xp);

    perform add_xp(task_xp);
    completed := advance_quests('tasks', 1);

    select count(*), count(*) filter (where completed_today) into total_tasks_count, done_tasks_count
      from checklist_tasks where user_id = auth.uid();
    if total_tasks_count > 0 and total_tasks_count = done_tasks_count then
      completed := completed || advance_quests('clear_checklist', 1);
    end if;

    select xp into cur_xp from app_state where user_id = auth.uid();
    select * into new_lvl from level_from_xp(cur_xp);

    return jsonb_build_object('xp_gained', task_xp, 'old_level', old_lvl.level, 'new_level', new_lvl.level, 'completed_quests', completed);
  else
    update app_state set total_tasks = greatest(0, total_tasks - 1), updated_at = now() where user_id = auth.uid();
    perform add_xp(-task_xp);
    return jsonb_build_object('completed_quests', '[]'::jsonb);
  end if;
end;
$$;

-- Convenience single call combining both steps above, for clients (the
-- web app) that don't need to split them.
create or replace function complete_task(p_task_id text, p_done boolean) returns jsonb
language plpgsql security invoker as $$
declare
  t checklist_tasks;
begin
  t := set_task_completed_flag(p_task_id, p_done);
  return apply_task_xp(t.recurrence, p_done);
end;
$$;

create or replace function roll_recurring_tasks() returns void
language sql security invoker as $$
  update checklist_tasks set completed_today = false
  where user_id = auth.uid()
    and recurrence = 'daily'
    and completed_today = true
    and (last_completed is distinct from current_date);
$$;

create or replace function set_notes(p_notes text) returns void
language sql security invoker as $$
  update app_state set notes = p_notes, updated_at = now() where user_id = auth.uid();
$$;

create or replace function add_task(p_text text, p_recurrence text, p_reminder_time text default null)
returns checklist_tasks
language plpgsql security invoker as $$
declare
  t checklist_tasks;
  next_order double precision;
begin
  select coalesce(max(sort_order), 0) + 1 into next_order from checklist_tasks where user_id = auth.uid();
  insert into checklist_tasks (user_id, text, recurrence, reminder_time, sort_order)
    values (auth.uid(), p_text, p_recurrence, p_reminder_time, next_order)
    returning * into t;
  return t;
end;
$$;

create or replace function remove_task(p_task_id text) returns void
language sql security invoker as $$
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
language sql security invoker as $$
  update checklist_tasks t set sort_order = x.ord
    from jsonb_array_elements_text(p_ordered_ids) with ordinality as x(id, ord)
    where t.id = x.id and t.user_id = auth.uid();
$$;

create or replace function set_task_reminder(p_task_id text, p_reminder_time text) returns void
language sql security invoker as $$
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
language plpgsql security invoker as $$
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
language plpgsql security invoker as $$
declare
  s app_state;
  tasks jsonb;
begin
  perform roll_recurring_tasks();
  perform ensure_daily_quests();
  perform ensure_weekly_quests();

  select * into s from app_state where user_id = auth.uid();
  select coalesce(jsonb_agg(to_jsonb(c) - 'user_id' order by c.sort_order, c.created_at), '[]'::jsonb) into tasks
    from checklist_tasks c where user_id = auth.uid();

  return jsonb_build_object(
    'notes', s.notes,
    'xp', s.xp,
    'total_pomodoros', s.total_pomodoros,
    'total_tasks', s.total_tasks,
    'current_streak', s.current_streak,
    'longest_streak', s.longest_streak,
    'quests', s.quests,
    'weekly_quests', s.weekly_quests,
    'checklist', tasks
  );
end;
$$;
