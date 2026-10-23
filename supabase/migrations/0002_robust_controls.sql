-- Reel Studio 0002: reliable Stop / Resume / Request changes under real-world races.
--  * cancel_job / retry_job / set_review_decision are idempotent (double taps, two devices) and say why when they refuse
--  * request_changes rejects + queues the new version in ONE transaction (no reel can vanish between the two steps)
--  * a guard trigger stops the worker (service_role) from overwriting a user's Cancel or Approve
--  * claim_job can take revisions only (used when the daily cap is reached)

-- ---------------------------------------------------------------- guard: the user's decision always wins
create or replace function public.guard_job_status() returns trigger language plpgsql set search_path = '' as $$
begin
  if current_user = 'service_role' and old.status in ('cancelled', 'done') and new.status is distinct from old.status then
    new.status := old.status; new.error := old.error; new.not_before := old.not_before;
  end if;
  return new;
end $$;
create or replace trigger jobs_guard_status before update on public.jobs for each row execute function public.guard_job_status();
revoke all on function public.guard_job_status() from public, anon, authenticated;

-- ---------------------------------------------------------------- create (dedupe + queue limit; revisions use request_changes)
create or replace function public.create_job(p_kind text, p_input jsonb, p_parent uuid default null)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs; n int; t text;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  if p_kind not in ('suggest', 'optimize', 'reel') then raise exception 'bad kind'; end if;
  if p_kind = 'reel' then
    t := lower(trim(coalesce(p_input -> 'concept' ->> 'title', '')));
    if t <> '' and p_parent is null then
      select * into j from public.jobs
       where kind = 'reel' and status in ('queued', 'running', 'waiting_quota')
         and lower(trim(coalesce(input -> 'concept' ->> 'title', ''))) = t
       order by created_at limit 1;
      if found then return j; end if;
    end if;
    select count(*) into n from public.jobs where kind = 'reel' and status in ('queued', 'running', 'waiting_quota');
    if n >= 3 then raise exception 'Three reels are already in the queue. Wait for one to finish, then try again.'; end if;
  else
    -- one idea request at a time per kind: a second tap returns the one already running
    select * into j from public.jobs where kind = p_kind and status in ('queued', 'running', 'waiting_quota')
       and created_at > now() - interval '20 minutes' and input = coalesce(p_input, '{}'::jsonb) order by created_at limit 1;
    if found then return j; end if;
  end if;
  insert into public.jobs(kind, input, parent_id, created_by) values (p_kind, coalesce(p_input, '{}'::jsonb), p_parent, auth.uid())
  returning * into j;
  return j;
end $$;

-- ---------------------------------------------------------------- cancel (Stop)
-- stop_job returns the row (the site shows the result at once); cancel_job keeps its old signature for older pages
create or replace function public.stop_job(p_job uuid)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  select * into j from public.jobs where id = p_job for update;
  if not found then raise exception 'This job no longer exists.'; end if;
  if j.status = 'cancelled' then return j; end if;                                   -- second tap / other device
  if j.status not in ('queued', 'running', 'waiting_quota') then
    raise exception '%', case j.status when 'review' then 'It already finished; it is waiting for your review.'
                                       when 'done' then 'It is already approved.' else 'It is already stopped.' end;
  end if;
  update public.jobs
     set status = 'cancelled', not_before = null,
         -- a running job keeps its lease until the Mac confirms it stopped (the site shows "Stopping…")
         lease_owner = case when j.status = 'running' then lease_owner end,
         lease_expires_at = case when j.status = 'running' then lease_expires_at end
   where id = p_job returning * into j;
  return j;
end $$;
create or replace function public.cancel_job(p_job uuid)
returns void language sql security definer set search_path = '' as $$ select public.stop_job(p_job); $$;

-- ---------------------------------------------------------------- resume (Try again)
create or replace function public.retry_job(p_job uuid)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs; n int;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  select * into j from public.jobs where id = p_job for update;
  if not found then raise exception 'This reel no longer exists.'; end if;
  if j.status in ('queued', 'running', 'waiting_quota') then return j; end if;          -- already going (double tap)
  if j.status not in ('failed', 'cancelled') then raise exception 'This reel is not stopped, so there is nothing to resume.'; end if;
  if j.output ->> 'decision' = 'reject' then raise exception 'This version was sent back for changes; its new version is the one in production.'; end if;
  if j.lease_owner is not null and j.lease_expires_at > now() then
    raise exception 'The Mac is still stopping this reel. Resume works in a few seconds.';
  end if;
  if j.kind = 'reel' then
    select count(*) into n from public.jobs where kind = 'reel' and status in ('queued', 'running', 'waiting_quota');
    if n >= 3 then raise exception 'Three reels are already in the queue. Wait for one to finish, then resume this one.'; end if;
  end if;
  update public.jobs
     set status = 'queued', error = null, not_before = null, attempts = 0, lease_owner = null, lease_expires_at = null,
         output = output - 'fail_count'
   where id = p_job returning * into j;
  return j;
end $$;

-- ---------------------------------------------------------------- review
create or replace function public.set_review_decision(p_job uuid, p_decision text, p_notes text default null)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  if p_decision not in ('approve', 'reject') then raise exception 'bad decision'; end if;
  select * into j from public.jobs where id = p_job and kind = 'reel' for update;
  if not found then raise exception 'This reel no longer exists.'; end if;
  if j.status = 'done' and p_decision = 'approve' then return j; end if;               -- double tap
  if j.status <> 'review' then raise exception 'This reel is no longer waiting for review.'; end if;
  update public.jobs
     set status = case when p_decision = 'approve' then 'done' else 'cancelled' end,
         output = output || jsonb_build_object('decision', p_decision, 'notes', p_notes, 'decided_at', now())
   where id = p_job returning * into j;
  return j;
end $$;

create or replace function public.request_changes(p_job uuid, p_notes text)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs; c public.jobs; n int;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  if length(trim(coalesce(p_notes, ''))) = 0 then raise exception 'Write what should change first.'; end if;
  select * into j from public.jobs where id = p_job and kind = 'reel' for update;
  if not found then raise exception 'This reel no longer exists.'; end if;
  if j.status <> 'review' then
    select * into c from public.jobs where parent_id = p_job and kind = 'reel' order by created_at desc limit 1;
    if found then return c; end if;                                                    -- double tap: the new version exists
    raise exception 'This reel is no longer waiting for review.';
  end if;
  select count(*) into n from public.jobs where kind = 'reel' and status in ('queued', 'running', 'waiting_quota');
  if n >= 5 then raise exception 'Five reels are already in the queue. Wait for one to finish, then send the changes.'; end if;
  update public.jobs set status = 'cancelled',
         output = output || jsonb_build_object('decision', 'reject', 'notes', trim(p_notes), 'decided_at', now())
   where id = p_job;
  insert into public.jobs(kind, input, parent_id, created_by)
  values ('reel', j.input || jsonb_build_object('notes', trim(p_notes), 'previous_title', coalesce(j.output ->> 'title', j.input -> 'concept' ->> 'title')),
          p_job, auth.uid())
  returning * into c;
  return c;
end $$;

-- ---------------------------------------------------------------- worker queue
create or replace function public.claim_next(p_worker text, p_kinds text[], p_lease_seconds int default 90, p_revisions_only boolean default false)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs;
begin
  select * into j from public.jobs
   where kind = any(p_kinds)
     and (not p_revisions_only or parent_id is not null)
     and ((status in ('queued', 'waiting_quota') and coalesce(not_before, now()) <= now())
       or (status = 'running' and lease_expires_at < now()))
   order by created_at limit 1
   for update skip locked;
  if not found then return null; end if;
  update public.jobs
     set status = 'running', lease_owner = p_worker, lease_expires_at = now() + make_interval(secs => p_lease_seconds),
         attempts = attempts + 1, error = null
   where id = j.id returning * into j;
  return j;
end $$;
create or replace function public.claim_job(p_worker text, p_kinds text[], p_lease_seconds int default 90)       -- older workers
returns public.jobs language sql security definer set search_path = '' as $$ select * from public.claim_next(p_worker, p_kinds, p_lease_seconds, false); $$;

revoke all on function public.claim_next(text, text[], int, boolean) from public, anon, authenticated;
revoke all on function public.claim_job(text, text[], int) from public, anon, authenticated;
revoke all on function public.create_job(text, jsonb, uuid) from public, anon;
revoke all on function public.cancel_job(uuid) from public, anon;
revoke all on function public.retry_job(uuid) from public, anon;
revoke all on function public.set_review_decision(uuid, text, text) from public, anon;
revoke all on function public.request_changes(uuid, text) from public, anon;
grant execute on function public.claim_next(text, text[], int, boolean) to service_role;
grant execute on function public.claim_job(text, text[], int) to service_role;
revoke all on function public.stop_job(uuid) from public, anon;
grant execute on function public.stop_job(uuid) to authenticated;
grant execute on function public.create_job(text, jsonb, uuid) to authenticated;
grant execute on function public.cancel_job(uuid) to authenticated;
grant execute on function public.retry_job(uuid) to authenticated;
grant execute on function public.set_review_decision(uuid, text, text) to authenticated;
grant execute on function public.request_changes(uuid, text) to authenticated;
