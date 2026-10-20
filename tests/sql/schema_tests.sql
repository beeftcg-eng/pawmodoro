-- schema_tests.sql - Calls supabase/schema.sql's functions the way the apps
-- do (as a signed-in user, or as the Edge Function's service role) and
-- asserts on the results. Run by tests/sql/run.sh; any failed assertion
-- stops it with an error.
\set ON_ERROR_STOP 1
\set A '11111111-1111-1111-1111-111111111111'
\set B '22222222-2222-2222-2222-222222222222'
\set C '33333333-3333-3333-3333-333333333333'
insert into auth.users values (:'A', 'a@example.com'), (:'B', 'b@example.com'), (:'C', 'c@example.com');

-- ============ Notes: conflict-checked saves ============
set role authenticated;
select set_config('request.jwt.claim.sub', :'A', false);
select sync_pull() is not null;
do $$
declare r jsonb;
begin
  r := set_notes_checked('one', null);
  assert (r->>'ok')::boolean and (r->>'rev')::int = 1, 'first save: ' || r;
  r := set_notes_checked('two', 1);
  assert (r->>'ok')::boolean and (r->>'rev')::int = 2, 'save on top of the latest: ' || r;
  r := set_notes_checked('stale edit', 1);
  assert not (r->>'ok')::boolean and r->>'notes' = 'two' and (r->>'rev')::int = 2, 'stale save must be refused: ' || r;
  r := set_notes_checked('two', 1);
  assert (r->>'ok')::boolean, 'same text is not a conflict: ' || r;
  perform set_notes('from an old client');  -- still bumps the revision
  r := set_notes_checked('stale again', 3);
  assert not (r->>'ok')::boolean, 'old-client save must still count: ' || r;
  assert (sync_pull()->>'notes_rev')::int = 4, 'sync_pull reports notes_rev';
end $$;

do $$
declare r jsonb;
begin
  r := set_note_page_checked('p1', 'Ideas', '<p>a</p>', null);
  assert (r->>'ok')::boolean and (r->>'rev')::int = 1, 'new page: ' || r;
  r := set_note_page_checked('p1', 'Ideas', '<p>b</p>', 1);
  assert (r->>'ok')::boolean and (r->>'rev')::int = 2, 'page save: ' || r;
  r := set_note_page_checked('p1', 'Ideas', '<p>stale</p>', 1);
  assert not (r->>'ok')::boolean and r->>'html' = '<p>b</p>', 'stale page save refused: ' || r;
  perform set_note_page('p2', 'Old client page', '');
  perform reorder_note_pages('["p2","p1"]'::jsonb);
  assert (sync_pull()->'notes_pages'->0->>'id') = 'p2', 'page order';
  assert (sync_pull()->'notes_pages'->1->>'rev')::int = 2, 'pages carry rev';
  perform remove_note_page('p2');
  assert jsonb_array_length(sync_pull()->'notes_pages') = 1, 'page removed';
end $$;

-- ============ Polling: only send what changed ============
do $$
declare r jsonb; fp text;
begin
  r := sync_pull_if_changed(null);
  fp := r->>'fingerprint';
  assert fp is not null and r ? 'checklist' and not r ? 'unchanged', 'first poll gets everything: ' || r;
  r := sync_pull_if_changed(fp);
  assert (r->>'unchanged')::boolean and not r ? 'checklist' and r->>'fingerprint' = fp, 'nothing changed: ' || r;
  r := sync_pull_if_changed('stale');
  assert r ? 'checklist' and r->>'fingerprint' = fp, 'an unknown fingerprint gets everything';
  perform set_note_page('p3', 'New', '');
  r := sync_pull_if_changed(fp);
  assert r ? 'notes_pages' and r->>'fingerprint' <> fp, 'a change gets everything again';
  perform remove_note_page('p3');
end $$;

-- ============ One timer across devices ============
do $$
declare r jsonb; xp0 int; rev1 bigint;
begin
  assert sync_pull()->'timer' = 'null'::jsonb, 'no timer yet';
  rev1 := set_timer_state('{"phase": "work", "running": true, "ends_at": 1790000000.5, "device": "desk", "junk": 1}');
  r := sync_pull()->'timer';
  assert r->>'phase' = 'work' and (r->>'running')::boolean and (r->>'ends_at')::float = 1790000000.5
         and r->>'device' = 'desk' and not r ? 'junk' and (r->>'rev')::bigint = rev1, 'timer state: ' || r;
  assert set_timer_state('{"phase": "short_break", "running": false, "seconds_left": 300, "device": "phone"}') = rev1 + 1,
    'every change bumps rev';
  begin
    perform set_timer_state('{"phase": "nap"}');
    assert false, 'bad phase accepted';
  exception when raise_exception then null;
  end;

  xp0 := (sync_pull()->>'xp')::int;
  r := record_pomodoro_completed(25, 'work:1790000000');
  assert (r->>'xp_gained')::int = 20, 'first report pays: ' || r;
  r := record_pomodoro_completed(25, 'work:1790000000');
  assert (r->>'xp_gained')::int = 0 and (r->>'already_counted')::boolean, 'second report of the same session: ' || r;
  assert (sync_pull()->>'xp')::int = xp0 + 20, 'paid once';
  r := record_pomodoro_completed(25);
  assert (r->>'xp_gained')::int = 20, 'an older client (no key) still pays';
  assert not (record_break_completed('short_break:1790000300') ? 'already_counted'), 'first break';
  assert (record_break_completed('short_break:1790000300')->>'already_counted')::boolean, 'same break again';
  assert record_break_completed() is not null, 'older client break';
end $$;

-- ============ Done days (per-task streaks) ============
select 1 from (select add_task('streak task', 'daily', null, 'checklist', 'sd1')) x;
do $$
declare days date[];
begin
  update checklist_tasks set done_days = array[user_today() - 2, user_today() - 1] where id = 'sd1';
  perform complete_task('sd1', true);
  select done_days into days from checklist_tasks where id = 'sd1';
  assert days = array[user_today() - 2, user_today() - 1, user_today()], 'a tick adds today: ' || days::text;
  perform complete_task('sd1', false);
  select done_days into days from checklist_tasks where id = 'sd1';
  assert days = array[user_today() - 2, user_today() - 1], 'an un-tick takes today off: ' || days::text;
  assert (select jsonb_typeof(c->'done_days') from jsonb_array_elements(sync_pull()->'checklist') c
          where c->>'id' = 'sd1') = 'array', 'sync_pull sends done_days';
  perform remove_task('sd1');
end $$;

-- ============ Reminder modes, snooze, quiet hours ============
select 1 from (select add_task('daily one', 'daily', null, 'checklist', 't1')) x;
do $$
declare t checklist_tasks;
begin
  perform set_task_reminder_mode('t1', null, 8);
  select * into t from checklist_tasks where id = 't1';
  assert t.reminder_every_h = 8 and t.reminder_time is null and t.push_last_at is not null, 'every 8 hours';
  perform set_task_reminder('t1', null);  -- an older desktop clearing "the time"
  select * into t from checklist_tasks where id = 't1';
  assert t.reminder_every_h = 8, 'a null time from an old client keeps every-N-hours';
  perform set_task_reminder('t1', '09:00');
  select * into t from checklist_tasks where id = 't1';
  assert t.reminder_every_h is null and t.reminder_time = '09:00', 'a daily time replaces every-N-hours';
  begin
    perform set_task_reminder_mode('t1', null, 30);
    assert false, 'every 30 hours should be refused';
  exception when raise_exception then null;
  end;
  perform set_reminder_settings(true, '22:00', '07:30');
  assert sync_pull()->'reminders' = '{"quiet_enabled": true, "quiet_start": "22:00", "quiet_end": "07:30"}'::jsonb, 'quiet hours in sync_pull';
  assert in_quiet_hours(true, '22:00', '08:00', '23:10') and in_quiet_hours(true, '22:00', '08:00', '03:00')
     and not in_quiet_hours(true, '22:00', '08:00', '08:00') and not in_quiet_hours(false, '22:00', '08:00', '23:00')
     and in_quiet_hours(true, '13:00', '14:00', '13:30') and not in_quiet_hours(true, '13:00', '14:00', '14:00'),
     'quiet hours window (mirrors Storage._in_quiet_hours)';
  perform set_reminder_settings(false, '22:00', '08:00');
  perform set_task_reminder('t1', null);  -- or the push tests below would depend on the time of day
end $$;
reset role;

-- ============ Push: what comes due ============
-- A: four tasks with different reminders; B and C: a household, shared items.
update app_state set tz_offset_min = 0;
insert into push_subscriptions values ('https://push.example/a', :'A', 'k', 's', now()),
                                      ('https://push.example/b', :'B', 'k', 's', now());
set role authenticated;
select set_config('request.jwt.claim.sub', :'A', false);
select 1 from (select add_task('daily due', 'daily', '00:00', 'checklist', 'd1')) x;
select 1 from (select add_task('daily later', 'daily', '23:59', 'checklist', 'd2')) x;
select 1 from (select add_task('done today', 'daily', '00:00', 'checklist', 'd3')) x;
select 1 from (select add_task('done yesterday', 'daily', '00:00', 'checklist', 'd4')) x;
select 1 from (select add_task('every 2h', 'daily', null, 'checklist', 'e1')) x;
select 1 from (select add_task('snoozed', 'daily', null, 'checklist', 's1')) x;
select 1 from (select add_task('wishlist card', 'once', '00:00', 'wishlist', 'w1')) x;
select set_task_reminder_mode('e1', null, 2);
select snooze_task('s1', now() - interval '1 minute');
reset role;
update checklist_tasks set completed_today = true, last_completed = (now() at time zone 'utc')::date where id = 'd3';
update checklist_tasks set completed_today = true, last_completed = (now() at time zone 'utc')::date - 1 where id = 'd4';
update checklist_tasks set push_last_at = now() - interval '3 hours' where id = 'e1';

-- Household for B and C (C has no phone subscribed).
insert into app_state (user_id) values (:'B'), (:'C') on conflict do nothing;
insert into households (id, invite_code) values ('44444444-4444-4444-4444-444444444444', 'TESTCODE');
insert into household_members (user_id, household_id, display_name)
values (:'B', '44444444-4444-4444-4444-444444444444', 'Bea'), (:'C', '44444444-4444-4444-4444-444444444444', 'Cal');
insert into shared_tasks (id, household_id, text, recurrence, due_time) values
  ('sh1', '44444444-4444-4444-4444-444444444444', 'bins every day', 'daily', '00:00'),
  ('sh2', '44444444-4444-4444-4444-444444444444', 'later today', 'daily', '23:59'),
  ('sh3', '44444444-4444-4444-4444-444444444444', 'done already', 'daily', '00:00');
insert into shared_tasks (id, household_id, text, recurrence, due_date, due_time) values
  ('sh4', '44444444-4444-4444-4444-444444444444', 'one-off today', 'once', (now() at time zone 'utc')::date, '00:00'),
  ('sh5', '44444444-4444-4444-4444-444444444444', 'one-off last week', 'once', (now() at time zone 'utc')::date - 7, '00:00');
update shared_tasks set done = true, done_at = now(), done_by = :'C' where id = 'sh3';
-- C's turn: its reminder goes only to C (who has no phone), so never to B.
insert into shared_tasks (id, household_id, text, recurrence, due_time, assigned_to) values
  ('sh6', '44444444-4444-4444-4444-444444444444', 'cals turn', 'daily', '00:00', :'C');

set role service_role;
create temp table round1 as select * from due_push_reminders();
do $$
declare got text;
begin
  select string_agg(kind || ':' || ref, ',' order by kind, ref) into got from round1;
  assert got = 'daily:d1,daily:d4,every:e1,shared:sh1,shared:sh4,snooze:s1',
    'first round should be d1, d4 (done yesterday counts as not done), e1, s1, sh1, sh4 -- got ' || got;
  assert (select count(*) from round1 where kind = 'shared' and owner = '22222222-2222-2222-2222-222222222222') = 2,
    'shared items go to B (C has no phone)';
  assert (select count(*) from round1 where token is null) = 0, 'every item carries an action token';
  assert (select count(*) from due_push_reminders()) = 0, 'claimed items are not handed out twice';
end $$;

-- Deliver everything except d4 (pretend its send failed).
select mark_push_sent(jsonb_agg(jsonb_build_object('kind', kind, 'ref', ref, 'owner', owner, 'occurrence', occurrence)))
from round1 where ref <> 'd4';
reset role;
update checklist_tasks set push_claimed_at = now() - interval '3 minutes' where id = 'd4';
update shared_push_sent set claimed_at = now() - interval '3 minutes';
set role service_role;
do $$
declare got text;
begin
  select string_agg(kind || ':' || ref, ',') into got from due_push_reminders();
  assert got = 'daily:d4', 'only the failed send comes back after its claim expires -- got ' || coalesce(got, 'nothing');
end $$;
reset role;
do $$
declare t checklist_tasks;
begin
  select * into t from checklist_tasks where id = 's1';
  assert t.snoozed_until is null, 'a delivered snooze is cleared';
  select * into t from checklist_tasks where id = 'e1';
  assert t.push_last_at > now() - interval '1 minute', 'every-N-hours clock restarts on delivery';
end $$;

-- Quiet hours hold every-N-hours (and nothing else).
update checklist_tasks set push_last_at = now() - interval '3 hours', push_claimed_at = null where id = 'e1';
update app_state set quiet_enabled = true, quiet_start = '00:00', quiet_end = '23:59' where user_id = :'A';
set role service_role;
do $$
begin
  assert (select count(*) from due_push_reminders() where ref = 'e1') = 0
      or to_char(now() at time zone 'utc', 'HH24:MI') = '23:59', 'quiet hours hold every-N-hours';
end $$;
reset role;
update app_state set quiet_enabled = false where user_id = :'A';

-- ============ Specific-day tasks and calendar event pushes (v2.22) ============
set role authenticated;
select set_config('request.jwt.claim.sub', :'A', false);
select 1 from (select add_task('bins day', 'weekday', '00:00', 'checklist', 'wd1', null, null,
                               extract(isodow from (now() at time zone 'utc'))::int - 1)) x;
select 1 from (select add_task('tomorrows day', 'weekday', '00:00', 'checklist', 'wd2', null, null,
                               extract(isodow from (now() at time zone 'utc'))::int % 7)) x;
do $$
declare r jsonb;
begin
  begin
    perform add_task('no day', 'weekday', null, 'checklist', 'wd3');
    assert false, 'a specific-day task needs its day of the week';
  exception when raise_exception then null;
  end;
  select t into r from jsonb_array_elements(sync_pull()->'checklist') t where t->>'id' = 'wd1';
  assert (r->>'weekday')::int = extract(isodow from (now() at time zone 'utc'))::int - 1, 'weekday in sync_pull';
  r := complete_task('wd1', true);
  assert (r->>'xp_gained')::int = 15, 'a specific-day task is worth 15 XP, got ' || r;
  assert checklist_done_on(true, 'weekday', user_today(), user_today(), null,
                           extract(isodow from user_today())::int - 1), 'ticked on its day: done';
  perform complete_task('wd1', false);
end $$;
reset role;
insert into calendar_events (id, user_id, title, start_date, end_date, start_time) values
  ('pe1', :'A', 'Dentist', (now() at time zone 'utc')::date, (now() at time zone 'utc')::date, '00:00'),
  ('pe2', :'A', 'Tomorrow', (now() at time zone 'utc')::date + 1, (now() at time zone 'utc')::date + 1, '00:00'),
  ('pe3', :'A', 'All day', (now() at time zone 'utc')::date, (now() at time zone 'utc')::date, null),
  ('pe4', :'A', 'Last week', (now() at time zone 'utc')::date - 7, (now() at time zone 'utc')::date - 7, '00:00');
insert into calendar_events (id, user_id, household_id, title, start_date, end_date, start_time) values
  ('he1', :'C', '44444444-4444-4444-4444-444444444444', 'Dinner', (now() at time zone 'utc')::date,
   (now() at time zone 'utc')::date, '00:00');
set role service_role;
create temp table round_v222 as
  select * from due_push_reminders() where ref in ('wd1', 'wd2', 'pe1', 'pe2', 'pe3', 'pe4', 'he1');
do $$
declare got text;
begin
  select string_agg(kind || ':' || ref, ',' order by kind, ref) into got from round_v222;
  assert got = 'daily:wd1,event:he1,event:pe1',
    'specific-day task on its day, timed events today (household ones to members with a phone) -- got ' || coalesce(got, 'nothing');
  assert (select owner from round_v222 where ref = 'he1') = '22222222-2222-2222-2222-222222222222',
    'a household event goes to B (C made it but has no phone; A isn''t a member)';
  assert (select title from round_v222 where ref = 'pe1') like '%Dentist' and (select token from round_v222 where ref = 'pe1') is null,
    'an event push is titled after the event, with no buttons';
  perform mark_push_sent(jsonb_agg(jsonb_build_object('kind', kind, 'ref', ref, 'owner', owner, 'occurrence', occurrence)))
  from round_v222;
end $$;
reset role;
update event_push_sent set claimed_at = now() - interval '3 minutes';
set role service_role;
do $$
begin
  assert (select count(*) from due_push_reminders() where kind = 'event') = 0, 'a delivered event isn''t pushed again';
end $$;
reset role;
update event_push_sent set sent = false, claimed_at = now() - interval '3 minutes' where event_id = 'pe1';
set role service_role;
do $$
begin
  assert (select count(*) from due_push_reminders() where ref = 'pe1') = 1, 'an undelivered one comes back after its claim';
end $$;
reset role;
delete from calendar_events where id in ('pe1', 'pe2', 'pe3', 'pe4', 'he1');
delete from checklist_tasks where id in ('wd1', 'wd2');

-- ============ Push: the phone timer ============
set role authenticated;
select set_config('request.jwt.claim.sub', :'A', false);
select schedule_timer_push('https://push.example/a', now() - interval '1 second', 'Focus session over', 'Take a break');
do $$
begin
  begin
    perform schedule_timer_push('https://push.example/b', now(), 'x', 'y');
    assert false, 'scheduling on someone else''s phone must fail';
  exception when raise_exception then null;
  end;
end $$;
reset role;
set role service_role;
do $$
declare n int;
begin
  select count(*) into n from due_push_reminders() where kind = 'timer' and endpoint = 'https://push.example/a';
  assert n = 1, 'timer push due, to that phone only';
  perform mark_push_sent('[{"kind": "timer", "ref": "https://push.example/a"}]'::jsonb);
  assert not exists (select 1 from timer_pushes), 'delivered timer push removed';
end $$;
reset role;
set role authenticated;
select set_config('request.jwt.claim.sub', :'A', false);
select schedule_timer_push('https://push.example/a', now() + interval '20 minutes', 'Focus session over', 'Take a break');
select cancel_timer_push('https://push.example/a');
reset role;
do $$ begin assert not exists (select 1 from timer_pushes), 'cancelled'; end $$;

-- ============ Notification buttons (no login) ============
-- Fresh tokens: re-arm d1 and sh1 and take one round.
update checklist_tasks set push_reminded_on = null, push_claimed_at = null where id = 'd1';
delete from shared_push_sent where task_id = 'sh1';
set role service_role;
create temp table round2 as select * from due_push_reminders();
grant select on round2 to anon;
reset role;
set role anon;
select set_config('request.jwt.claim.sub', '', false);
do $$
declare tok text; xp_before int; xp_after int;
begin
  select token into tok from round2 where ref = 'd1';
  assert push_action(tok, 'snooze') = 'ok', 'snooze button';
  assert push_action(tok, 'snooze') = 'expired', 'tokens are single-use';
  assert push_action('not-a-token', 'done') = 'expired', 'unknown token';
end $$;
reset role;
do $$
declare t checklist_tasks;
begin
  select * into t from checklist_tasks where id = 'd1';
  assert t.snoozed_until between now() + interval '14 minutes' and now() + interval '16 minutes', 'snoozed 15 minutes';
end $$;
-- Done: needs a new token for d1 (the snooze used it); take it from a new round.
update checklist_tasks set snoozed_until = now() - interval '1 second', push_claimed_at = null where id = 'd1';
set role service_role;
create temp table round3 as select * from due_push_reminders();
grant select on round3 to anon;
reset role;
create temp table xp_before as select xp from app_state where user_id = :'A';
set role anon;
do $$
declare tok text;
begin
  select token into tok from round3 where ref = 'd1';
  assert push_action(tok, 'done') = 'ok', 'done button on a task';
  select token into tok from round2 where ref = 'sh1';
  assert push_action(tok, 'done') = 'ok', 'done button on a shared item';
end $$;
reset role;
do $$
declare t checklist_tasks; s shared_tasks;
begin
  select * into t from checklist_tasks where id = 'd1';
  assert t.completed_today, 'task ticked off by the button';
  assert (select xp from app_state where user_id = '11111111-1111-1111-1111-111111111111') > (select xp from xp_before),
    'ticking it from the notification earns XP like the app does';
  select * into s from shared_tasks where id = 'sh1';
  assert s.done and s.done_by = '22222222-2222-2222-2222-222222222222', 'shared item ticked off as its owner';
end $$;

-- ============ Monthly tasks, due dates, subtasks (v2.18) ============
do $$
begin
  assert month_occurrence(15, '2026-10-20') = '2026-10-15', 'this month';
  assert month_occurrence(15, '2026-10-10') = '2026-09-15', 'not yet this month: last month';
  assert month_occurrence(31, '2026-02-28') = '2026-02-28', 'a short month uses its last day';
  assert month_occurrence(31, '2026-03-05') = '2026-02-28', 'last month was short';
  assert month_occurrence(1, '2026-10-01') = '2026-10-01', 'on the day itself';
end $$;
insert into app_state (user_id) values (:'C') on conflict do nothing;
set role authenticated;
select set_config('request.jwt.claim.sub', :'C', false);
do $$
declare t checklist_tasks; r jsonb; today date := user_today();
begin
  perform add_task('rent', 'monthly', null, 'checklist', 'm1', extract(day from today)::int);
  select * into t from checklist_tasks where id = 'm1';
  assert t.month_day = extract(day from today)::int and t.due_date is null, 'monthly task keeps its day';
  begin
    perform add_task('no day', 'monthly', null, 'checklist', 'm2');
    assert false, 'a monthly task without a day should be refused';
  exception when raise_exception then null;
  end;
  perform add_task('taxes', 'once', null, 'checklist', 'o1', 5, today + 3);
  select * into t from checklist_tasks where id = 'o1';
  assert t.month_day is null and t.due_date = today + 3, 'a one-off keeps its due date, not a month day';
  perform add_task('plain', 'daily', null, 'checklist', 'p1', null, today);
  assert (select due_date from checklist_tasks where id = 'p1') is null, 'only one-offs have due dates';
  perform set_task_due_date('o1', today - 1);
  assert (select due_date from checklist_tasks where id = 'o1') = today - 1, 'due date changed';
  perform set_task_due_date('p1', today);
  assert (select due_date from checklist_tasks where id = 'p1') is null, 'due date on a daily task ignored';

  r := complete_task('m1', true);
  assert (r->>'xp_gained')::int = 20, 'a monthly task pays 20 XP: ' || r;
  perform complete_task('m1', false);
  r := complete_task('m1', true);
  assert r->>'xp_gained' is null, 'and only once a month: ' || r;
  update checklist_tasks set last_completed = today - 40, awarded_on = today - 40 where id = 'm1';
  perform roll_recurring_tasks();
  assert not (select completed_today from checklist_tasks where id = 'm1'), 'it un-ticks when its day comes round';
  r := complete_task('m1', true);
  assert (r->>'xp_gained')::int = 20, 'and pays again: ' || r;

  perform set_task_details('o1', 'bring receipts', '[{"id": "a", "text": "Find receipts"}, {"id": "b", "text": "Fill form"}]');
  perform set_subtask_done('o1', 'b', true);
  perform set_task_details('o1', 'bring receipts',
    '[{"id": "b", "text": "Fill the form"}, {"id": "c", "text": "Submit"}, {"id": "a", "text": "Find receipts"}]');
  select * into t from checklist_tasks where id = 'o1';
  assert t.note = 'bring receipts', 'note saved';
  assert t.subtasks = '[{"id": "b", "text": "Fill the form", "done": true}, {"id": "c", "text": "Submit", "done": false},
                        {"id": "a", "text": "Find receipts", "done": false}]'::jsonb,
    'edits keep ticks and order: ' || t.subtasks;
  begin
    perform set_task_details('o1', '', '{"not": "a list"}');
    assert false, 'subtasks must be a list';
  exception when raise_exception then null;
  end;
  perform set_task_details('p1', '', '[{"id": "x", "text": "step"}]');
  perform set_subtask_done('p1', 'x', true);
  perform complete_task('p1', true);
  update checklist_tasks set last_completed = today - 1 where id = 'p1';
  perform roll_recurring_tasks();
  select * into t from checklist_tasks where id = 'p1';
  assert not t.completed_today and not (t.subtasks->0->>'done')::boolean, 'a reset un-ticks the subtasks';
  r := sync_pull();
  assert (select count(*) from jsonb_array_elements(r->'checklist') e
          where e ? 'subtasks' and e ? 'note' and e ? 'month_day' and e ? 'due_date') = 3, 'sync_pull carries the new fields';
end $$;
-- Reminders: a dated one-off from its date, a monthly task only on its day.
select 1 from (select add_task('due tomorrow', 'once', '00:00', 'checklist', 'r1', null, user_today() + 1)) x;
select 1 from (select add_task('was due yesterday', 'once', '00:00', 'checklist', 'r2', null, user_today() - 1)) x;
select 1 from (select add_task('monthly, today', 'monthly', '00:00', 'checklist', 'r3', extract(day from user_today())::int)) x;
select 1 from (select add_task('monthly, another day', 'monthly', '00:00', 'checklist', 'r4',
                               extract(day from user_today())::int % 28 + 1)) x;
reset role;
insert into push_subscriptions values ('https://push.example/c', :'C', 'k', 's', now());
set role service_role;
do $$
declare got text;
begin
  select string_agg(ref, ',' order by ref) into got from due_push_reminders()
  where owner = '33333333-3333-3333-3333-333333333333' and kind not in ('shared', 'event');
  assert got = 'r2,r3', 'only the overdue one-off and today''s monthly task remind -- got ' || coalesce(got, 'nothing');
end $$;
reset role;
delete from push_subscriptions where user_id = :'C';

-- An old 3-argument add_task left over in a database would make the phone's
-- plain add ambiguous to PostgREST; schema.sql must drop it.
do $$
begin
  assert (select count(*) from pg_proc where proname = 'add_task') = 1, 'exactly one add_task';
end $$;

-- ============ Who may call what ============
set role authenticated;
select set_config('request.jwt.claim.sub', :'A', false);
do $$
begin
  begin perform due_push_reminders(); assert false, 'due_push_reminders must be service-only';
  exception when insufficient_privilege then null; end;
  begin perform mark_push_sent('[]'::jsonb); assert false, 'mark_push_sent must be service-only';
  exception when insufficient_privilege then null; end;
  begin perform check_cron_secret('x'); assert false, 'check_cron_secret must be service-only';
  exception when insufficient_privilege then null; end;
  begin perform count(*) from server_settings; assert false, 'server_settings must be unreadable';
  exception when insufficient_privilege then null; end;
  assert (select count(*) from push_subscriptions) = 1, 'only your own subscriptions are visible';
  begin perform save_push_subscription('javascript:alert(1)', 'x', 'y'); assert false, 'bad endpoint accepted';
  exception when raise_exception then null; end;
end $$;
reset role;
set role service_role;
do $$
begin
  assert check_cron_secret((select value from server_settings where name = 'cron_secret')), 'the right secret passes';
  assert not check_cron_secret('wrong'), 'a wrong secret fails';
end $$;
reset role;

-- household_pull_if_changed: B is in a household, A isn't.
set role authenticated;
select set_config('request.jwt.claim.sub', :'B', false);
do $$
declare r jsonb; fp text;
begin
  r := household_pull_if_changed(null);
  fp := r->>'fingerprint';
  assert jsonb_typeof(r->'household') = 'object' and fp is not null, 'household with fingerprint: ' || r;
  r := household_pull_if_changed(fp);
  assert (r->>'unchanged')::boolean and not r ? 'household', 'unchanged household: ' || r;
  perform shared_add_task('new item');
  r := household_pull_if_changed(fp);
  assert r ? 'household' and r->>'fingerprint' <> fp, 'changed household comes back';
end $$;
select set_config('request.jwt.claim.sub', :'A', false);
do $$
declare r jsonb;
begin
  r := household_pull_if_changed(null);
  assert r ? 'household' and jsonb_typeof(r->'household') = 'null', 'no household is a null household: ' || r;
  assert (household_pull_if_changed(r->>'fingerprint')->>'unchanged')::boolean, 'still none';
end $$;
reset role;

-- Assigning shared items.
set role authenticated;
select set_config('request.jwt.claim.sub', :'B', false);
do $$
declare item jsonb;
begin
  perform shared_assign('sh2', '33333333-3333-3333-3333-333333333333');
  select t into item from jsonb_array_elements(household_pull()->'tasks') t where t->>'id' = 'sh2';
  assert item->>'assigned_name' = 'Cal' and not (item->>'assigned_to_me')::boolean, 'assigned to Cal: ' || item;
  begin
    perform shared_assign('sh2', '11111111-1111-1111-1111-111111111111');
    assert false, 'assigned to someone outside the household';
  exception when raise_exception then null;
  end;
  perform shared_assign('sh2', null);
  select t into item from jsonb_array_elements(household_pull()->'tasks') t where t->>'id' = 'sh2';
  assert item->'assigned_to' = 'null'::jsonb, 'back to anyone';
end $$;
select set_config('request.jwt.claim.sub', :'C', false);
do $$
begin
  perform household_leave();
  assert not exists (select 1 from shared_tasks where assigned_to = '33333333-3333-3333-3333-333333333333'),
    'leaving hands your items back to anyone';
end $$;
reset role;
-- ============ Calendar events and attached files ============
-- A, B and Cal are in one household by now? Start clean: A and B share one.
set role authenticated;
select set_config('request.jwt.claim.sub', :'A', false);
do $$ begin perform household_leave(); end $$;
select set_config('request.jwt.claim.sub', :'B', false);
do $$ begin perform household_leave(); end $$;
select set_config('request.jwt.claim.sub', :'A', false);
select household_create('Ann') ->> 'invite_code' as code \gset
select set_config('request.jwt.claim.sub', :'B', false);
select household_join(:'code', 'Bo') is not null;
select set_config('request.jwt.claim.sub', :'A', false);
do $$
declare r jsonb; fp text;
begin
  r := save_event('ev1', 'Lisbon trip', '2026-10-20', '2026-10-25', null, null, 'flight TP123', false);
  assert r->>'title' = 'Lisbon trip' and not r ? 'user_id', 'saved: ' || r;
  r := sync_pull();
  assert jsonb_array_length(r->'events') = 1 and r->'events'->0->>'note' = 'flight TP123'
         and (r->'events'->0->>'created_by_me')::boolean, 'personal event in sync_pull: ' || (r->'events');
  assert jsonb_array_length(household_pull()->'events') = 0, 'not on the household calendar';
  perform save_event('ev1', 'Lisbon trip', '2026-10-20', '2026-10-26', '08:30', null, 'flight TP123', false);
  assert (sync_pull()->'events'->0->>'end_date') = '2026-10-26' and sync_pull()->'events'->0->>'start_time' = '08:30',
    'updated in place';
  begin
    perform save_event('bad', 'Backwards', '2026-10-20', '2026-10-19', null, null, '', false);
    assert false, 'an event ending before it starts';
  exception when raise_exception then null;
  end;
  begin
    perform save_event('bad', 'Odd', '2026-10-20', '2026-10-20', null, '10:00', '', false);
    assert false, 'an end time without a start time';
  exception when raise_exception then null;
  end;
  perform save_event('ev2', 'Dinner at Mum''s', '2026-10-18', '2026-10-18', '19:00', '21:00', '', true);
  assert jsonb_array_length(sync_pull()->'events') = 1, 'a shared event is not in sync_pull';
  r := household_pull()->'events'->0;
  assert r->>'id' = 'ev2' and r->>'created_by_name' = 'Ann' and r->>'end_time' = '21:00', 'shared event: ' || r;

  -- A file on a personal task: registered, then uploaded, then listed.
  perform add_task('Pay rent', 'monthly', null, 'checklist', 'rent1', 1);
  r := add_attachment('file0001', 'task', 'rent1', 'receipt.pdf', 'application/pdf', 1000);
  assert r->>'path' = 'u/11111111-1111-1111-1111-111111111111/file0001' and not (r->>'uploaded')::boolean, 'registered: ' || r;
  assert add_attachment('file0001', 'task', 'rent1', 'receipt.pdf', 'application/pdf', 1000)->>'path' = r->>'path',
    'registering again is a no-op';
  assert jsonb_array_length(sync_pull()->'attachments') = 0, 'not listed before the upload finished';
  insert into storage.objects (bucket_id, name) values ('attachments', 'u/11111111-1111-1111-1111-111111111111/file0001');
  perform attachment_uploaded('file0001');
  r := sync_pull()->'attachments';
  assert jsonb_array_length(r) = 1 and r->0->>'file_name' = 'receipt.pdf' and (r->0->>'size')::int = 1000, 'listed: ' || r;
  begin
    insert into storage.objects (bucket_id, name) values ('attachments', 'u/11111111-1111-1111-1111-111111111111/unregistered');
    assert false, 'uploaded a file with no row';
  exception when insufficient_privilege then null;
  end;
  begin
    perform add_attachment('file-big', 'task', 'rent1', 'huge.mov', 'video/mp4', 26 * 1024 * 1024);
    assert false, 'a file over the limit';
  exception when raise_exception then null;
  end;

  -- On a shared event: the household's path, and in household_pull.
  r := add_attachment('file0002', 'event', 'ev2', 'menu.jpg', 'image/jpeg', 2000);
  assert r->>'path' like 'h/%/file0002', 'household path: ' || r;
  insert into storage.objects (bucket_id, name) values ('attachments', r->>'path');
  perform attachment_uploaded('file0002');
  assert household_pull()->'attachments'->0->>'id' = 'file0002', 'in household_pull';
  assert jsonb_array_length(sync_pull()->'attachments') = 1, 'not in sync_pull';

  -- Removing the item puts its files in the trash; putting it back takes them out.
  perform remove_task('rent1');
  assert jsonb_array_length(sync_pull()->'attachments') = 0, 'gone with its task';
  r := sync_pull()->'attachments_trash';
  assert jsonb_array_length(r) = 1 and r->0->>'id' = 'file0001' and r->0->>'trashed_at' is not null, 'in the trash: ' || r;
  perform add_task('Pay rent', 'monthly', null, 'checklist', 'rent1', 1);
  assert jsonb_array_length(sync_pull()->'attachments') = 1 and jsonb_array_length(sync_pull()->'attachments_trash') = 0,
    'an undo brings it back';
end $$;

-- The other member sees (and can open) the household's file, not A's own.
select set_config('request.jwt.claim.sub', :'B', false);
do $$
declare r jsonb;
begin
  r := household_pull();
  assert r->'attachments'->0->>'added_by_name' = 'Ann' and not (r->'attachments'->0->>'added_by_me')::boolean, 'B sees it';
  assert jsonb_array_length(sync_pull()->'attachments') = 0 and jsonb_array_length(sync_pull()->'events') = 0,
    'A''s own calendar stays A''s';
  assert (select count(*) from storage.objects where bucket_id = 'attachments') = 1, 'B can read only the shared file';
  begin
    perform save_event('ev2', 'Dinner', '2026-10-18', '2026-10-18', null, null, '', false);
    assert false, 'B took A''s event off the shared calendar';
  exception when raise_exception then null;
  end;
  perform save_event('ev2', 'Dinner at Mum''s', '2026-10-18', '2026-10-18', '19:30', null, 'bring wine', true);
  assert household_pull()->'events'->0->>'start_time' = '19:30', 'any member edits a shared event';
  begin
    perform save_event('ev1', 'Hijack', '2026-10-01', '2026-10-01', null, null, '', false);
    assert false, 'B overwrote A''s personal event';
  exception when unique_violation then null;
  end;
  begin
    perform add_attachment('file0003', 'event', 'ev1', 'x.txt', 'text/plain', 1);
    assert false, 'B attached a file to A''s personal event';
  exception when raise_exception then null;
  end;
end $$;

-- Someone outside the household sees none of it.
select set_config('request.jwt.claim.sub', :'C', false);
do $$
begin
  assert (select count(*) from storage.objects where bucket_id = 'attachments') = 0, 'C can read no files';
  assert (select count(*) from attachments) = 0 and (select count(*) from calendar_events) = 0, 'C sees no rows';
end $$;

-- A shared event taken back off the calendar, then removed; a file deleted.
select set_config('request.jwt.claim.sub', :'A', false);
do $$
declare r jsonb;
begin
  perform save_event('ev2', 'Dinner at Mum''s', '2026-10-18', '2026-10-18', null, null, '', false);
  assert jsonb_array_length(household_pull()->'events') = 0 and jsonb_array_length(household_pull()->'attachments') = 0,
    'off the household calendar, with its file';
  r := sync_pull()->'attachments';
  assert jsonb_array_length(r) = 2 and (select path from attachments where id = 'file0002') like 'h/%',
    'the file follows (and keeps its Storage path)';
  perform remove_event('ev2');
  assert jsonb_array_length(sync_pull()->'attachments_trash') = 1, 'its file is in the trash';
  delete from storage.objects where name = (select path from attachments where id = 'file0002');
  perform remove_attachment('file0002');
  perform remove_attachment('file0001');
  assert jsonb_array_length(sync_pull()->'attachments_trash') = 0 and jsonb_array_length(sync_pull()->'attachments') = 0,
    'removed';
  perform remove_event('ev1');
  perform remove_task('rent1');
  perform household_leave();
end $$;
select set_config('request.jwt.claim.sub', :'B', false);
do $$ begin perform household_leave(); end $$;
reset role;

-- ============ Dungeons Desk: characters and campaign files ============
set role authenticated;
select set_config('request.jwt.claim.sub', :'A', false);
do $$
declare r jsonb; s bigint;
begin
  r := dmdesk_put_character('c1', 'Thorin', '{"cls":"Fighter"}', null);
  assert (r->>'ok')::boolean and (r->>'rev')::int = 1, 'new character: ' || r;
  r := dmdesk_put_character('c1', 'Thorin', '{"cls":"Fighter","level":2}', 1);
  assert (r->>'ok')::boolean and (r->>'rev')::int = 2, 'save on top: ' || r;
  r := dmdesk_put_character('c1', 'Thorin', '{"cls":"Wizard"}', 1);
  assert not (r->>'ok')::boolean and (r->>'rev')::int = 2 and r->'data'->>'level' = '2', 'stale save refused, newer comes back: ' || r;
  r := dmdesk_pull_characters(0);
  assert jsonb_array_length(r->'rows') = 1 and r->'rows'->0->>'name' = 'Thorin', 'pull everything: ' || r;
  s := (r->>'since')::bigint;
  assert jsonb_array_length(dmdesk_pull_characters(s)->'rows') = 0, 'nothing new after since';
  assert (dmdesk_pull_characters(s)->>'since')::bigint = s, 'since stays put when idle';
  r := dmdesk_delete_character('c1', 1);
  assert not (r->>'ok')::boolean, 'stale delete refused';
  r := dmdesk_delete_character('c1', 2);
  assert (r->>'ok')::boolean, 'delete';
  r := dmdesk_pull_characters(s);
  assert jsonb_array_length(r->'rows') = 1 and (r->'rows'->0->>'deleted')::boolean and r->'rows'->0->'data' = 'null'::jsonb, 'deletion is pulled, without data: ' || r;
  r := dmdesk_put_character('c1', 'Thorin again', '{}', 3);
  assert (r->>'ok')::boolean and (r->>'rev')::int = 4, 'a deleted character can come back: ' || r;

  perform dmdesk_save_campaign('camp1', 'Thornwood');
  assert jsonb_array_length(dmdesk_campaigns_list()) = 1, 'campaign listed';
  r := dmdesk_put_file('camp1', 'NPCs/Mira.md', '# Mira', 0);
  assert (r->>'ok')::boolean and (r->>'rev')::int = 1, 'new file: ' || r;
  r := dmdesk_put_file('camp1', '.dmtools/state.json', '{}', null);
  assert (r->>'ok')::boolean, 'state file: ' || r;
  r := dmdesk_put_file('camp1', 'NPCs/Mira.md', '# Mira (other device)', 0);
  assert not (r->>'ok')::boolean and r->>'content' = '# Mira', 'two devices creating the same note: second is refused: ' || r;
  assert (dmdesk_campaigns_list()->0->>'notes')::int = 1, 'note count leaves out state.json';
  r := dmdesk_pull_files('camp1', 0);
  assert (r->>'exists')::boolean and jsonb_array_length(r->'rows') = 2, 'pull files: ' || r;
  s := (r->>'since')::bigint;
  r := dmdesk_delete_file('camp1', 'NPCs/Mira.md', 1);
  assert (r->>'ok')::boolean and (r->>'rev')::int = 2, 'delete file: ' || r;
  r := dmdesk_pull_files('camp1', s);
  assert jsonb_array_length(r->'rows') = 1 and (r->'rows'->0->>'deleted')::boolean, 'deletion pulled: ' || r;
  begin
    perform dmdesk_put_file('camp1', '../escape.md', 'x', 0);
    assert false, 'a path with .. must be refused';
  exception when raise_exception then null;
  end;
  begin
    perform dmdesk_put_file('nope', 'a.md', 'x', 0);
    assert false, 'files need a campaign';
  exception when raise_exception then null;
  end;
end $$;

-- Another user sees none of it and can't write into it.
select set_config('request.jwt.claim.sub', :'B', false);
do $$
declare r jsonb;
begin
  assert jsonb_array_length(dmdesk_pull_characters(0)->'rows') = 0, 'B sees no characters';
  assert jsonb_array_length(dmdesk_campaigns_list()) = 0, 'B sees no campaigns';
  r := dmdesk_pull_files('camp1', 0);
  assert not (r->>'exists')::boolean and jsonb_array_length(r->'rows') = 0, 'B can''t pull A''s files: ' || r;
  begin
    perform dmdesk_put_file('camp1', 'x.md', 'x', 0);
    assert false, 'B can''t write into A''s campaign';
  exception when raise_exception then null;
  end;
  r := dmdesk_put_character('c1', 'B''s own', '{}', null);
  assert (r->>'ok')::boolean and (r->>'rev')::int = 1, 'same id, separate per user: ' || r;
  insert into storage.objects (bucket_id, name) values ('dmdesk', '22222222-2222-2222-2222-222222222222/camp/media/a.png');
  begin
    insert into storage.objects (bucket_id, name) values ('dmdesk', '11111111-1111-1111-1111-111111111111/camp1/media/b.png');
    assert false, 'B can''t upload under A''s folder';
  exception when insufficient_privilege then null;
  end;
end $$;

select set_config('request.jwt.claim.sub', :'A', false);
do $$
begin
  assert (select count(*) from storage.objects where bucket_id = 'dmdesk') = 0, 'A can''t see B''s images';
  perform dmdesk_delete_campaign('camp1');
  assert jsonb_array_length(dmdesk_campaigns_list()) = 0 and not (dmdesk_pull_files('camp1', 0)->>'exists')::boolean, 'campaign deleted';
end $$;
reset role;

-- ============ Dungeons Desk: online tables ============
set role authenticated;
select set_config('request.jwt.claim.sub', :'A', false);
select dmdesk_table_open(null, 'Thornwood') as table_code \gset
select set_config('dd.code', :'table_code', false);
do $$
declare c text := current_setting('dd.code'); r jsonb;
begin
  assert length(c) = 14 and c ~ '^[a-z0-9]+$', 'a long random code: ' || c;
  assert dmdesk_table_open(c, 'Thornwood 2') = c, 'reopening keeps the code';
  assert dmdesk_table_list() = array[c], 'listed';
  perform dmdesk_table_publish(c, jsonb_build_object('', '{"party":["Thorin"]}'::jsonb, 'thorin', '{"party":["Thorin"],"card":{"name":"Thorin"}}'::jsonb), array['', 'thorin']);
end $$;
reset role;

-- Players need no account, just the code.
set role anon;
select set_config('request.jwt.claim.sub', '', false);
do $$
declare c text := current_setting('dd.code'); r jsonb; rev bigint;
begin
  assert not (dmdesk_table_join('wrongcode12345', 'Thorin')->>'ok')::boolean, 'a wrong code gets nothing';
  assert not (dmdesk_table_view('wrongcode12345', 'Thorin', null)->>'ok')::boolean, 'no view for a wrong code';
  r := dmdesk_table_join(c, 'Thorin');
  assert (r->>'ok')::boolean and r->>'name' = 'Thornwood 2', 'join: ' || r;
  r := dmdesk_table_view(c, 'Thorin', null);
  assert r->'view'->'card'->>'name' = 'Thorin', 'Thorin gets his own view: ' || r;
  rev := (r->>'rev')::bigint;
  r := dmdesk_table_view(c, 'thorin', rev);
  assert (r->>'ok')::boolean and r->'view' is null, 'unchanged view isn''t sent again: ' || r;
  r := dmdesk_table_view(c, 'Mira', null);
  assert r->'view'->'card' is null and r->'view'->'party'->>0 = 'Thorin', 'someone else gets the shared view, no card: ' || r;
  r := dmdesk_table_act(c, 'Thorin', 'move', '{"map":"m1","token":"t1","x":10,"y":20}');
  assert (r->>'ok')::boolean, 'action: ' || r;
  begin
    perform dmdesk_table_act(c, 'Thorin', 'delete_everything', '{}');
    assert false, 'unknown action types are refused';
  exception when raise_exception then null;
  end;
  begin
    perform dmdesk_table_act(c, 'Thorin', 'ping', jsonb_build_object('x', repeat('9', 3000)));
    assert false, 'oversized actions are refused';
  exception when raise_exception then null;
  end;
  for i in 1..19 loop perform dmdesk_table_act(c, 'Thorin', 'ping', '{"x":1,"y":1}'); end loop;
  r := dmdesk_table_act(c, 'Thorin', 'ping', '{"x":1,"y":1}');
  assert not (r->>'ok')::boolean and (r->>'slow')::boolean, 'too many actions too fast: ' || r;
  assert (select count(*) from dmdesk_table_views) = 0 and (select count(*) from dmdesk_tables) = 0
    and (select count(*) from dmdesk_table_actions) = 0, 'anon can''t read the tables directly';
  begin
    perform dmdesk_table_poll(c, 0);
    assert false, 'anon can''t read the DM''s queue';
  exception when insufficient_privilege then null;
  end;
end $$;
reset role;

set role authenticated;
select set_config('request.jwt.claim.sub', :'B', false);
do $$
declare c text := current_setting('dd.code');
begin
  assert not (dmdesk_table_poll(c, 0)->>'exists')::boolean, 'B can''t read A''s table';
  begin
    perform dmdesk_table_publish(c, '{"thorin":{"fake":true}}', array['thorin']);
    assert false, 'B can''t publish to A''s table';
  exception when raise_exception then null;
  end;
  perform dmdesk_table_close(c);
  assert (select count(*) from dmdesk_table_views) = 0, 'B sees no views';
  begin
    insert into storage.objects (bucket_id, name) values ('dmdesk-table', '11111111-1111-1111-1111-111111111111/' || c || '/m/x.png');
    assert false, 'B can''t upload into A''s table images';
  exception when insufficient_privilege then null;
  end;
end $$;

select set_config('request.jwt.claim.sub', :'A', false);
do $$
declare c text := current_setting('dd.code'); r jsonb; last bigint;
begin
  insert into storage.objects (bucket_id, name) values ('dmdesk-table', '11111111-1111-1111-1111-111111111111/' || c || '/m/x.png');
  r := dmdesk_table_poll(c, 0);
  assert (r->>'exists')::boolean and r->'members'->0->>'name' = 'Thorin' and jsonb_array_length(r->'actions') = 20, 'A sees who''s there and their actions: ' || r;
  assert r->'actions'->0->>'type' = 'move' and r->'actions'->0->'data'->>'token' = 't1', 'actions in order';
  last := (r->'actions'->-1->>'id')::bigint;
  r := dmdesk_table_poll(c, last);
  assert jsonb_array_length(r->'actions') = 0, 'handled actions are gone: ' || r;
  perform dmdesk_table_publish(c, '{}', array['']);
  assert dmdesk_table_view(c, 'Thorin', null)->'view'->'card' is null, 'views not kept are removed: Thorin is back to the shared one';
  perform dmdesk_table_open(null, 'two'); perform dmdesk_table_open(null, 'three'); perform dmdesk_table_open(null, 'four');
  assert array_length(dmdesk_table_list(), 1) = 3, 'at most 3 tables per DM';
  assert not (c = any (dmdesk_table_list())), 'the oldest went';
  foreach c in array dmdesk_table_list() loop perform dmdesk_table_close(c); end loop;
  assert array_length(dmdesk_table_list(), 1) is null, 'closed';
end $$;
reset role;

\echo 'all schema assertions passed'
