-- Reel Studio 0008: Autopilot. Every day at a set time (default 13:30 India time) the cloud queues one "autopilot" request;
-- the Mac researches trends + our own Instagram results, picks the topic most likely to go viral, makes the reel, and the
-- existing review timer posts it. Nobody has to press anything.
--   * autopilot requests are kind 'suggest' with input.autopilot = true (no table change needed)
--   * reels made by autopilot carry input.autopilot = <request id>; they may run after the daily cap
--   * a reel the autopilot's own quality gate holds (output.autopilot_hold) is never auto-posted
--   * Instagram results (views, reach, shares, saves, watch time) are written into output.ig_stats by the cloud poster

insert into public.settings(key, value) values ('autopilot', '{"on": true, "time": "13:30", "tz": "Asia/Kolkata"}')
on conflict (key) do nothing;

-- the cloud clock: queue today's run once, within 3 hours after the set time (turning it on late never fires at night)
create or replace function public.autopilot_tick() returns uuid language plpgsql security definer set search_path = '' as $$
declare s jsonb; tz text; t time; local_now timestamp; day text; j public.jobs;
begin
  select value into s from public.settings where key = 'autopilot';
  if s is null or not coalesce((s ->> 'on')::boolean, false) then return null; end if;
  tz := coalesce(s ->> 'tz', 'Asia/Kolkata');
  t := coalesce(nullif(s ->> 'time', ''), '13:30')::time;
  local_now := now() at time zone tz;
  day := to_char(local_now, 'YYYY-MM-DD');
  if local_now < date_trunc('day', local_now) + t or local_now > date_trunc('day', local_now) + t + interval '3 hours' then return null; end if;
  perform pg_advisory_xact_lock(hashtext('reelstudio-autopilot'));
  if exists (select 1 from public.jobs where kind = 'suggest' and input ->> 'autopilot' = 'true' and input ->> 'day' = day
               and input ->> 'trigger' = 'schedule') then return null; end if;
  insert into public.jobs(kind, input) values ('suggest', jsonb_build_object('autopilot', true, 'day', day, 'trigger', 'schedule'))
  returning * into j;
  return j.id;
end $$;

-- "Run autopilot now" from the site (one at a time)
create or replace function public.run_autopilot_now() returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs; tz text;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  select * into j from public.jobs where kind = 'suggest' and input ->> 'autopilot' = 'true'
     and status in ('queued', 'running', 'waiting_quota') order by created_at limit 1;
  if found then return j; end if;
  select coalesce(value ->> 'tz', 'Asia/Kolkata') into tz from public.settings where key = 'autopilot';
  insert into public.jobs(kind, input, created_by)
  values ('suggest', jsonb_build_object('autopilot', true, 'day', to_char(now() at time zone coalesce(tz, 'Asia/Kolkata'), 'YYYY-MM-DD'), 'trigger', 'manual'), auth.uid())
  returning * into j;
  return j;
end $$;

-- on/off and time (HH:MM, India time)
create or replace function public.set_autopilot(p_on boolean, p_time text) returns jsonb language plpgsql security definer set search_path = '' as $$
declare v jsonb;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  if p_time !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' then raise exception 'Use a time like 13:30.'; end if;
  insert into public.settings(key, value) values ('autopilot', jsonb_build_object('on', coalesce(p_on, true), 'time', p_time, 'tz', 'Asia/Kolkata'))
  on conflict (key) do update set value = public.settings.value || jsonb_build_object('on', coalesce(p_on, true), 'time', p_time), updated_at = now()
  returning value into v;
  return v;
end $$;

-- autopilot reels may start after the daily cap (the cap is for manual reels); everything else unchanged
create or replace function public.claim_next(p_worker text, p_kinds text[], p_lease_seconds int default 90, p_revisions_only boolean default false)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs;
begin
  select * into j from public.jobs
   where kind = any(p_kinds)
     and (not p_revisions_only or parent_id is not null or (kind = 'reel' and input ? 'autopilot'))
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

-- the review timer never auto-posts a reel the autopilot's quality gate held back
create or replace function public.auto_approve_due() returns int language plpgsql security definer set search_path = '' as $$
declare n int;
begin
  if not public.autopost_on() then return 0; end if;
  update public.jobs
     set status = 'done',
         output = output || jsonb_build_object('decision', 'auto', 'decided_at', now()) || public.post_targets(true)
   where kind = 'reel' and status = 'review' and coalesce((output ->> 'hold')::boolean, false) = false
     and coalesce((output ->> 'autopilot_hold')::boolean, false) = false
     and (output ->> 'review_deadline')::timestamptz <= now();
  get diagnostics n = row_count;
  return n;
end $$;

-- the cloud poster writes Instagram results without touching anything else in the reel (atomic merge)
create or replace function public.set_reel_stats(p_job uuid, p_stats jsonb) returns void language sql security definer set search_path = '' as $$
  update public.jobs set output = output || jsonb_build_object('ig_stats', p_stats) where id = p_job and kind = 'reel';
$$;

revoke all on function public.autopilot_tick() from public, anon, authenticated;
revoke all on function public.set_reel_stats(uuid, jsonb) from public, anon, authenticated;
grant execute on function public.set_reel_stats(uuid, jsonb) to service_role;
revoke all on function public.run_autopilot_now() from public, anon;
revoke all on function public.set_autopilot(boolean, text) from public, anon;
grant execute on function public.run_autopilot_now() to authenticated;
grant execute on function public.set_autopilot(boolean, text) to authenticated;

select cron.schedule('reelstudio-autopilot', '*/5 * * * *', $$select public.autopilot_tick()$$);
