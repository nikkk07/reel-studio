-- We One Aviation Reel Studio: schema, security and queue functions.
-- Apply once in Supabase: Dashboard > SQL Editor > paste > Run  (or `supabase db push`).

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------- members (allow-list)
create table if not exists public.members (
  email text primary key,
  role text not null default 'member' check (role in ('owner', 'member')),
  created_at timestamptz not null default now()
);

create or replace function public.is_member() returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.members m where lower(m.email) = lower(coalesce(auth.jwt() ->> 'email', '')));
$$;

-- ---------------------------------------------------------------- workers (heartbeat)
create table if not exists public.workers (
  id text primary key,
  last_seen timestamptz not null default now(),
  status text not null default 'idle',          -- idle | busy | paused
  version text,
  claude_ok boolean,
  claude_checked_at timestamptz,
  claude_note text,                              -- e.g. 'usage limit until 18:30'
  info jsonb not null default '{}'::jsonb        -- disk, current job, reels today, cap ...
);

-- ---------------------------------------------------------------- pings (connect-to-server round trip)
create table if not exists public.pings (
  id bigserial primary key,
  created_at timestamptz not null default now(),
  created_by uuid default auth.uid(),
  answered_at timestamptz,
  worker_id text,
  reply jsonb
);

-- ---------------------------------------------------------------- jobs
create table if not exists public.jobs (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('suggest', 'optimize', 'reel')),
  status text not null default 'queued'
    check (status in ('queued', 'running', 'review', 'done', 'failed', 'cancelled', 'waiting_quota')),
  stage text,
  progress real not null default 0,
  input jsonb not null default '{}'::jsonb,
  output jsonb not null default '{}'::jsonb,
  error text,
  attempts int not null default 0,
  lease_owner text,
  lease_expires_at timestamptz,
  not_before timestamptz,                        -- used for waiting_quota resume time
  parent_id uuid references public.jobs(id) on delete set null,
  created_by uuid default auth.uid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists jobs_queue_idx on public.jobs (status, kind, created_at);
create index if not exists jobs_parent_idx on public.jobs (parent_id);

create table if not exists public.job_events (
  id bigserial primary key,
  job_id uuid not null references public.jobs(id) on delete cascade,
  ts timestamptz not null default now(),
  level text not null default 'info' check (level in ('debug', 'info', 'warn', 'error')),
  message text not null,
  data jsonb
);
create index if not exists job_events_job_idx on public.job_events (job_id, id);

create or replace function public.touch_updated_at() returns trigger language plpgsql set search_path = '' as $$
begin new.updated_at = now(); return new; end $$;
drop trigger if exists jobs_touch on public.jobs;
create trigger jobs_touch before update on public.jobs for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------- queue: claim / renew (called by the worker with the service key)
create or replace function public.claim_job(p_worker text, p_kinds text[], p_lease_seconds int default 90)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs;
begin
  select * into j from public.jobs
   where kind = any(p_kinds)
     and (
          (status = 'queued' and coalesce(not_before, now()) <= now())   -- not_before = retry back-off
       or (status = 'waiting_quota' and coalesce(not_before, now()) <= now())
       or (status = 'running' and lease_expires_at < now())          -- crashed / disconnected worker
     )
   order by created_at
   limit 1
   for update skip locked;
  if not found then return null; end if;
  update public.jobs
     set status = 'running', lease_owner = p_worker,
         lease_expires_at = now() + make_interval(secs => p_lease_seconds),
         attempts = attempts + 1, error = null
   where id = j.id
  returning * into j;
  return j;
end $$;

create or replace function public.renew_lease(p_job uuid, p_worker text, p_lease_seconds int default 90)
returns boolean language sql security definer set search_path = '' as $$
  update public.jobs set lease_expires_at = now() + make_interval(secs => p_lease_seconds)
   where id = p_job and lease_owner = p_worker and status = 'running'
  returning true;
$$;

-- ---------------------------------------------------------------- member-facing RPCs
-- create a job (members only); kind + input validated here so the table itself stays closed for inserts
create or replace function public.create_job(p_kind text, p_input jsonb, p_parent uuid default null)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs; running_reels int;
begin
  if not public.is_member() then raise exception 'not a member'; end if;
  if p_kind not in ('suggest', 'optimize', 'reel') then raise exception 'bad kind'; end if;
  if p_kind = 'reel' then
    select count(*) into running_reels from public.jobs
     where kind = 'reel' and status in ('queued', 'running', 'waiting_quota');
    if running_reels >= 3 then raise exception 'queue full: % reels already waiting', running_reels; end if;
  end if;
  insert into public.jobs(kind, input, parent_id, created_by) values (p_kind, coalesce(p_input, '{}'::jsonb), p_parent, auth.uid())
  returning * into j;
  return j;
end $$;

create or replace function public.set_review_decision(p_job uuid, p_decision text, p_notes text default null)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs;
begin
  if not public.is_member() then raise exception 'not a member'; end if;
  if p_decision not in ('approve', 'reject') then raise exception 'bad decision'; end if;
  update public.jobs
     set status = case when p_decision = 'approve' then 'done' else 'cancelled' end,
         output = output || jsonb_build_object('decision', p_decision, 'notes', p_notes, 'decided_at', now())
   where id = p_job and kind = 'reel' and status = 'review'
  returning * into j;
  if not found then raise exception 'job not in review'; end if;
  return j;
end $$;

create or replace function public.cancel_job(p_job uuid)
returns void language sql security definer set search_path = '' as $$
  update public.jobs set status = 'cancelled'
   where id = p_job and public.is_member() and status in ('queued', 'waiting_quota', 'running');
$$;

-- ---------------------------------------------------------------- row level security
alter table public.members    enable row level security;
alter table public.workers    enable row level security;
alter table public.pings      enable row level security;
alter table public.jobs       enable row level security;
alter table public.job_events enable row level security;

drop policy if exists members_read on public.members;
create policy members_read on public.members for select to authenticated using ((select public.is_member()));
drop policy if exists workers_read on public.workers;
create policy workers_read on public.workers for select to authenticated using ((select public.is_member()));
drop policy if exists pings_read on public.pings;
create policy pings_read on public.pings for select to authenticated using ((select public.is_member()));
drop policy if exists pings_insert on public.pings;
create policy pings_insert on public.pings for insert to authenticated
  with check ((select public.is_member()) and answered_at is null and created_by = (select auth.uid()));
drop policy if exists jobs_read on public.jobs;
create policy jobs_read on public.jobs for select to authenticated using ((select public.is_member()));
drop policy if exists events_read on public.job_events;
create policy events_read on public.job_events for select to authenticated using ((select public.is_member()));
-- no insert/update/delete policies for members on jobs/events: they go through the RPCs above;
-- the worker uses the service-role key, which bypasses RLS.

-- Supabase grants ALL on new tables to anon/authenticated by default; RLS already blocks writes, this removes them outright.
revoke all on public.members, public.workers, public.pings, public.jobs, public.job_events from anon, authenticated;
grant usage on schema public to authenticated;
grant select on public.members, public.workers, public.pings, public.jobs, public.job_events to authenticated;
grant insert on public.pings to authenticated;
grant usage on sequence public.pings_id_seq to authenticated;

revoke all on function public.claim_job(text, text[], int) from public, anon, authenticated;
revoke all on function public.renew_lease(uuid, text, int) from public, anon, authenticated;
revoke all on function public.create_job(text, jsonb, uuid) from public, anon;
revoke all on function public.set_review_decision(uuid, text, text) from public, anon;
revoke all on function public.cancel_job(uuid) from public, anon;
revoke all on function public.is_member() from public, anon;
revoke all on function public.touch_updated_at() from public, anon, authenticated;
grant execute on function public.create_job(text, jsonb, uuid) to authenticated;
grant execute on function public.set_review_decision(uuid, text, text) to authenticated;
grant execute on function public.cancel_job(uuid) to authenticated;
grant execute on function public.is_member() to authenticated;

-- ---------------------------------------------------------------- realtime
do $$ begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    begin alter publication supabase_realtime add table public.jobs; exception when duplicate_object then null; end;
    begin alter publication supabase_realtime add table public.job_events; exception when duplicate_object then null; end;
    begin alter publication supabase_realtime add table public.pings; exception when duplicate_object then null; end;
    begin alter publication supabase_realtime add table public.workers; exception when duplicate_object then null; end;
  end if;
end $$;

-- ---------------------------------------------------------------- storage (private bucket; members read via signed URLs)
do $$ begin
  if exists (select 1 from information_schema.schemata where schema_name = 'storage') then
    insert into storage.buckets (id, name, public, file_size_limit)
    values ('reels', 'reels', false, 52428800)
    on conflict (id) do nothing;
    begin
      create policy reels_member_read on storage.objects for select to authenticated
        using (bucket_id = 'reels' and (select public.is_member()));
    exception when duplicate_object then null; end;
  end if;
end $$;

-- housekeeping (old events/pings) is done by the worker with the service key; no SQL function needed.

-- ---------------------------------------------------------------- first owner (add teammates the same way)
insert into public.members(email, role) values ('weoneaviation8@gmail.com', 'owner') on conflict (email) do nothing;
