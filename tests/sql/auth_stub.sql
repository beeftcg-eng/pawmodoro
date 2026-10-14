-- Just enough of Supabase for schema.sql to load into a plain Postgres:
-- the API roles, auth.users, and auth.uid() / auth.role() reading the same
-- settings Supabase's real ones do (request.jwt.claim.sub, checked against
-- the live project: auth.uid() = coalesce(that, request.jwt.claims->>sub)).
do $$ begin
  create role anon nologin;
  create role authenticated nologin;
  create role service_role nologin bypassrls;
exception when duplicate_object then null;
end $$;
create schema if not exists auth;
create schema if not exists extensions;
create table if not exists auth.users (id uuid primary key, email text);
create or replace function auth.uid() returns uuid language sql stable as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
create or replace function auth.role() returns text language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claim.role', true), ''), 'authenticated') $$;
grant usage on schema auth, public, extensions to anon, authenticated, service_role;
-- Supabase's defaults: the API roles get table/function access, RLS does the rest.
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant execute on functions to anon, authenticated, service_role;
-- Supabase Storage's two tables (only the columns schema.sql and the tests
-- use), with RLS on like the real ones.
create schema if not exists storage;
create table if not exists storage.buckets (
  id text primary key, name text not null, public boolean default false, file_size_limit bigint);
create table if not exists storage.objects (
  id uuid primary key default gen_random_uuid(), bucket_id text references storage.buckets(id), name text,
  owner uuid, unique (bucket_id, name));
alter table storage.objects enable row level security;
grant usage on schema storage to anon, authenticated, service_role;
grant all on storage.objects, storage.buckets to anon, authenticated, service_role;
