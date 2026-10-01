-- push_reminders.sql - Run in the Supabase SQL Editor after schema.sql (and
-- after deploying supabase/functions/send-reminders): calls the
-- send-reminders Edge Function every 30 seconds, which pushes whatever has
-- come due (checklist reminders, shared-list items, the phone timer) to
-- subscribed phones.
--
-- Safe to re-run (it replaces the job). To stop phone pushes:
--   select cron.unschedule('pawmodoro-push-reminders');
--
-- The x-cron-secret header carries a random secret that schema.sql made in
-- the server_settings table; the function refuses calls without it. The
-- Authorization header is the public `anon` key (the same one in
-- cloud_defaults.py), which only gets the call through Supabase's gateway.
-- If you run your own Supabase project, put its URL and anon key here.

create extension if not exists pg_cron;
create extension if not exists pg_net;

select cron.unschedule(jobid) from cron.job where jobname = 'pawmodoro-push-reminders';

select cron.schedule(
  'pawmodoro-push-reminders',
  '30 seconds',
  $$
  select net.http_post(
    url := 'https://cinxclbsgamdprftcbek.supabase.co/functions/v1/send-reminders',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNpbnhjbGJzZ2FtZHByZnRjYmVrIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODk1NTc4OTIsImV4cCI6MjEwNTEzMzg5Mn0.1hpyBV2ZO3MeQUSnx3K5-XG6BbzY-gXb-4uG6Ls7Fr0',
      'x-cron-secret', (select value from public.server_settings where name = 'cron_secret')
    ),
    body := '{}'::jsonb,
    timeout_milliseconds := 20000
  );
  $$
);
