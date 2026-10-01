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
  where owner = '33333333-3333-3333-3333-333333333333' and kind <> 'shared';
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
\echo 'all schema assertions passed'
