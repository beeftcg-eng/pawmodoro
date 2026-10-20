-- parity_tests.sql - The schema's copies of the desktop's date, reminder and
-- quest rules, checked against tests/parity/cases.json (what the Python
-- rules answer; regenerate it with tools/parity_cases.py). Run by
-- tests/sql/run.sh, which passes the file's path in as :casefile.
\set ON_ERROR_STOP 1
\set cases `cat :'casefile'`
create temp table parity as select :'cases'::jsonb as c;

do $$
declare
  cases jsonb := (select c from parity);
  r jsonb;
  t jsonb;
  got text;
begin
  for r in select * from jsonb_array_elements(cases->'week_start') loop
    got := week_start_for((r->>'day')::date)::text;
    assert got = r->>'expect', format('week_start_for(%s) = %s, Python says %s', r->>'day', got, r->>'expect');
  end loop;

  for r in select * from jsonb_array_elements(cases->'month_occurrence') loop
    got := month_occurrence((r->>'month_day')::int, (r->>'day')::date)::text;
    assert got = r->>'expect', format('month_occurrence(%s, %s) = %s, Python says %s',
                                      r->>'month_day', r->>'day', got, r->>'expect');
  end loop;

  for r in select * from jsonb_array_elements(cases->'reminds') loop
    t := r->'task';
    got := task_reminds_on(t->>'recurrence', (t->>'month_day')::int, (t->>'due_date')::date, (r->>'day')::date,
                           (t->>'weekday')::int)::text;
    assert got = r->>'expect', format('task_reminds_on(%s, %s) = %s, Python says %s', t, r->>'day', got, r->>'expect');
  end loop;

  for r in select * from jsonb_array_elements(cases->'task_reset') loop
    t := r->'task';
    got := checklist_needs_reset(t->>'recurrence', (t->>'last_completed')::date, (t->>'month_day')::int,
                                 (r->>'day')::date, (t->>'weekday')::int)::text;
    assert got = r->>'expect', format('checklist_needs_reset(%s, %s) = %s, Python says %s', t, r->>'day', got, r->>'expect');
    got := (not coalesce(checklist_done_on(true, t->>'recurrence', (t->>'last_completed')::date, (r->>'day')::date,
                                           (t->>'month_day')::int, (t->>'weekday')::int), false))::text;
    assert got = r->>'expect', format('checklist_done_on disagrees with the reset for %s on %s', t, r->>'day');
  end loop;

  for r in select * from jsonb_array_elements(cases->'shared_occurrence') loop
    t := r->'schedule';
    got := coalesce(shared_last_occurrence(t->>'recurrence', (t->>'weekday')::int, (t->>'month_day')::int,
                                           (r->>'day')::date)::text, 'null');
    assert got = coalesce(r->>'expect', 'null'),
      format('shared_last_occurrence(%s, %s) = %s, Python says %s', t, r->>'day', got, r->>'expect');
  end loop;

  for r in select * from jsonb_array_elements(cases->'done_days') loop
    got := to_jsonb(task_done_days(array(select jsonb_array_elements_text(r->'days'))::date[],
                                   (r->>'day')::date, (r->>'done')::boolean))::text;
    assert got::jsonb = r->'expect', format('task_done_days(%s, %s, %s) = %s, Python says %s',
                                             r->'days', r->>'day', r->>'done', got, r->'expect');
  end loop;

  for r in select * from jsonb_array_elements(cases->'quiet_hours') loop
    t := r->'settings';
    got := in_quiet_hours((t->>'quiet_enabled')::boolean, t->>'quiet_start', t->>'quiet_end', r->>'hm')::text;
    assert got = r->>'expect', format('in_quiet_hours(%s, %s) = %s, Python says %s', t, r->>'hm', got, r->>'expect');
  end loop;
end $$;

-- The quests the cloud picks for a day / a week (pick_quests, which
-- ensure_daily_quests / ensure_weekly_quests use).
do $$
declare
  cases jsonb := (select c from parity);
  r jsonb;
  got jsonb;
begin
  for r in select * from jsonb_array_elements(cases->'daily_quests') loop
    select coalesce(jsonb_agg(q->'id' order by n), '[]'::jsonb) into got
      from jsonb_array_elements(pick_quests('daily', (r->>'day')::date)) with ordinality as x(q, n);
    assert got = r->'expect', format('daily quests for %s: cloud %s, Python %s', r->>'day', got, r->'expect');
  end loop;
  for r in select * from jsonb_array_elements(cases->'weekly_quests') loop
    select coalesce(jsonb_agg(q->'id' order by n), '[]'::jsonb) into got
      from jsonb_array_elements(pick_quests('weekly', (r->>'week_start')::date)) with ordinality as x(q, n);
    assert got = r->'expect', format('weekly quests for %s: cloud %s, Python %s', r->>'week_start', got, r->'expect');
  end loop;
end $$;
\echo 'all parity assertions passed'
