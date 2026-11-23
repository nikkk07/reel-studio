-- 0029 Mac first, cloud as backup: the Mac is told about new work instead of asking every few seconds.
--  * wake: one tiny row per event the Mac must react to, pushed to it by Supabase Realtime (the Mac subscribes with its
--    service key; nobody else can read the table).
--      job  - a job was created, changed status or its waiting time, or was changed by the website / a timer (not by the
--             Mac or the cloud poster themselves, so the Mac's own progress saves never echo back to it)
--      ping - "Connect" on the website asked whether the Mac is there
--  * Rows older than an hour are removed as new ones arrive; the Mac never reads the table itself, only the pushes.

create table if not exists public.wake (
  id bigint generated always as identity primary key,
  kind text not null check (kind in ('job', 'ping')),
  ref text,
  at timestamptz not null default now());
create index if not exists wake_at_idx on public.wake (at);
alter table public.wake enable row level security;           -- no policies: only the service role (the Mac) sees it
revoke all on public.wake from anon, authenticated;

create or replace function public.wake_on_job() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  if tg_op = 'INSERT' or new.status is distinct from old.status or new.not_before is distinct from old.not_before
     or coalesce(auth.role(), '') <> 'service_role' then
    insert into public.wake (kind, ref) values ('job', new.id::text);
    delete from public.wake where at < now() - interval '1 hour';
  end if;
  return null;
end $$;

create or replace function public.wake_on_ping() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  insert into public.wake (kind, ref) values ('ping', new.id::text);
  return null;
end $$;

drop trigger if exists jobs_wake on public.jobs;
create trigger jobs_wake after insert or update on public.jobs for each row execute function public.wake_on_job();
drop trigger if exists pings_wake on public.pings;
create trigger pings_wake after insert on public.pings for each row execute function public.wake_on_ping();

revoke all on function public.wake_on_job() from public, anon, authenticated;
revoke all on function public.wake_on_ping() from public, anon, authenticated;

do $$ begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    begin alter publication supabase_realtime add table public.wake; exception when duplicate_object then null; end;
  end if;
end $$;
