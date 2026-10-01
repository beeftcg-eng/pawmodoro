-- push_reminders.sql - Run ONCE in the Supabase SQL Editor (after
-- schema.sql, and after deploying supabase/functions/send-reminders):
-- calls the send-reminders Edge Function every minute, which pushes any
-- checklist reminders that have come due to subscribed phones.
--
-- Safe to re-run (it replaces the job). To stop phone pushes:
--   select cron.unschedule('pawmodoro-push-reminders');
--
-- The key below is the public `anon` key (the same one in
-- cloud_defaults.py) -- it only gets the call through Supabase's gateway;
-- the function itself works with its own service-role access. Calling the
-- function more often than this, by anyone, sends nothing extra: each
-- reminder is marked as sent for the day in due_push_reminders().
-- If you run your own Supabase project, put its URL and anon key here.

create extension if not exists pg_cron;
create extension if not exists pg_net;

select cron.unschedule(jobid) from cron.job where jobname = 'pawmodoro-push-reminders';

select cron.schedule(
  'pawmodoro-push-reminders',
  '* * * * *',
  $$
  select net.http_post(
    url := 'https://cinxclbsgamdprftcbek.supabase.co/functions/v1/send-reminders',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNpbnhjbGJzZ2FtZHByZnRjYmVrIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODk1NTc4OTIsImV4cCI6MjEwNTEzMzg5Mn0.1hpyBV2ZO3MeQUSnx3K5-XG6BbzY-gXb-4uG6Ls7Fr0'
    ),
    body := '{}'::jsonb,
    timeout_milliseconds := 10000
  );
  $$
);
