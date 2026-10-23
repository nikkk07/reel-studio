-- Reel Studio 0009: hardening found by the abuse checklist.
--   * oversized requests and request floods (Claude quota burn) are refused
--   * autopilot can run at most 3 times a day (scheduled + Run now), so Instagram is never flooded
--   * an autopilot reel that finishes at night waits until 07:30 India time before it auto-posts
--   * two legacy functions the site no longer uses are closed

create or replace function public.create_job(p_kind text, p_input jsonb, p_parent uuid default null)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs; n int; t text;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  if p_kind not in ('suggest', 'optimize', 'reel') then raise exception 'bad kind'; end if;
  if octet_length(coalesce(p_input, '{}'::jsonb)::text) > 20000 then raise exception 'That request is too long. Keep it under a page of text.'; end if;
  if coalesce(p_input, '{}'::jsonb) ? 'autopilot' then raise exception 'Use Run now to start Autopilot.'; end if;
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
    select count(*) into n from public.jobs where kind in ('suggest', 'optimize') and not (input ? 'autopilot')
       and status in ('queued', 'running', 'waiting_quota');
    if n >= 3 then raise exception 'Three idea requests are already running. Wait for one to finish.'; end if;
  end if;
  insert into public.jobs(kind, input, parent_id, created_by) values (p_kind, coalesce(p_input, '{}'::jsonb), p_parent, auth.uid())
  returning * into j;
  return j;
end $$;

create or replace function public.request_changes(p_job uuid, p_notes text)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs; c public.jobs; n int;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  if length(trim(coalesce(p_notes, ''))) = 0 then raise exception 'Write what should change first.'; end if;
  if length(p_notes) > 2000 then raise exception 'Keep the changes under 2,000 characters.'; end if;
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

create or replace function public.run_autopilot_now() returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs; tz text; day text; n int;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  select * into j from public.jobs where kind = 'suggest' and input ->> 'autopilot' = 'true'
     and status in ('queued', 'running', 'waiting_quota') order by created_at limit 1;
  if found then return j; end if;
  select coalesce(value ->> 'tz', 'Asia/Kolkata') into tz from public.settings where key = 'autopilot';
  day := to_char(now() at time zone coalesce(tz, 'Asia/Kolkata'), 'YYYY-MM-DD');
  select count(*) into n from public.jobs where kind = 'suggest' and input ->> 'autopilot' = 'true' and input ->> 'day' = day
     and status not in ('cancelled', 'failed');
  if n >= 3 then raise exception 'Autopilot already made 3 reels today. It runs again tomorrow.'; end if;
  insert into public.jobs(kind, input, created_by)
  values ('suggest', jsonb_build_object('autopilot', true, 'day', day, 'trigger', 'manual'), auth.uid())
  returning * into j;
  return j;
end $$;

-- autopilot reels never go out in the middle of the night (bad reach, nobody awake to notice a problem)
create or replace function public.auto_approve_due() returns int language plpgsql security definer set search_path = '' as $$
declare n int; night boolean;
begin
  if not public.autopost_on() then return 0; end if;
  night := (now() at time zone 'Asia/Kolkata')::time not between time '07:30' and time '23:00';
  update public.jobs
     set status = 'done',
         output = output || jsonb_build_object('decision', 'auto', 'decided_at', now()) || public.post_targets(true)
   where kind = 'reel' and status = 'review' and coalesce((output ->> 'hold')::boolean, false) = false
     and coalesce((output ->> 'autopilot_hold')::boolean, false) = false
     and not (night and input ? 'autopilot')
     and (output ->> 'review_deadline')::timestamptz <= now();
  get diagnostics n = row_count;
  return n;
end $$;

-- legacy (replaced by set_post / set_autopost); not used by the site any more
revoke execute on function public.set_reel_posting(uuid, text) from authenticated;
revoke execute on function public.set_post_schedule(text[], boolean) from authenticated;
