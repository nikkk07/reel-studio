-- Reel Studio 0020: Autopilot for every account, and "Re-check" for each connection.
--  * Autopilot settings are per account: 'autopilot' for We One Aviation (unchanged), 'autopilot@<account>' for the others
--    (off until switched on). The cloud clock queues each account's run at its own time; limits are per account.
--  * request_recheck(account, platform) asks the cloud poster to check Instagram / Facebook / LinkedIn / the website now
--    (within a minute). It only sets a flag; the check itself runs in the cloud, so it works while the Mac is offline.

create or replace function public.autopilot_tick() returns uuid language plpgsql security definer set search_path = '' as $$
declare b record; s jsonb; tz text; t time; local_now timestamp; day text; last uuid;
begin
  perform pg_advisory_xact_lock(hashtext('reelstudio-autopilot'));
  for b in select id from public.brands where status = 'live' order by sort loop
    select value into s from public.settings where key = public.bkey('autopilot', b.id);
    continue when s is null or not coalesce((s ->> 'on')::boolean, false);
    tz := coalesce(s ->> 'tz', 'Asia/Kolkata');
    t := coalesce(nullif(s ->> 'time', ''), '13:30')::time;
    local_now := now() at time zone tz;
    day := to_char(local_now, 'YYYY-MM-DD');
    continue when local_now < date_trunc('day', local_now) + t or local_now > date_trunc('day', local_now) + t + interval '3 hours';
    continue when exists (select 1 from public.jobs where kind = 'suggest' and brand = b.id and input ->> 'autopilot' = 'true'
                            and input ->> 'day' = day and input ->> 'trigger' = 'schedule');
    insert into public.jobs(kind, input) values ('suggest', jsonb_build_object('autopilot', true, 'day', day, 'trigger', 'schedule', 'brand', b.id))
    returning id into last;
  end loop;
  return last;
end $$;
revoke all on function public.autopilot_tick() from public, anon, authenticated;

create or replace function public.run_autopilot_now(p_brand text) returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs; tz text; day text; n int; b text;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  b := public.check_brand(p_brand);
  if (select status from public.brands where id = b) <> 'live' then raise exception 'This account is still being set up.'; end if;
  select * into j from public.jobs where kind = 'suggest' and brand = b and input ->> 'autopilot' = 'true'
     and status in ('queued', 'running', 'waiting_quota') order by created_at limit 1;
  if found then return j; end if;
  select coalesce(value ->> 'tz', 'Asia/Kolkata') into tz from public.settings where key = public.bkey('autopilot', b);
  day := to_char(now() at time zone coalesce(tz, 'Asia/Kolkata'), 'YYYY-MM-DD');
  select count(*) into n from public.jobs where kind = 'suggest' and brand = b and input ->> 'autopilot' = 'true' and input ->> 'day' = day
     and status not in ('cancelled', 'failed');
  if n >= 3 then raise exception 'Autopilot already made 3 reels for this account today. It runs again tomorrow.'; end if;
  insert into public.jobs(kind, input, created_by)
  values ('suggest', jsonb_build_object('autopilot', true, 'day', day, 'trigger', 'manual', 'brand', b), auth.uid())
  returning * into j;
  return j;
end $$;
create or replace function public.run_autopilot_now() returns public.jobs language sql security definer set search_path = '' as $$
  select public.run_autopilot_now('weone');
$$;

create or replace function public.set_autopilot(p_on boolean, p_time text, p_brand text) returns jsonb language plpgsql security definer set search_path = '' as $$
declare v jsonb; b text;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  b := public.check_brand(p_brand);
  if p_time !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' then raise exception 'Use a time like 13:30.'; end if;
  if coalesce(p_on, true) and (select status from public.brands where id = b) <> 'live' then raise exception 'This account is still being set up.'; end if;
  insert into public.settings(key, value) values (public.bkey('autopilot', b), jsonb_build_object('on', coalesce(p_on, true), 'time', p_time, 'tz', 'Asia/Kolkata'))
  on conflict (key) do update set value = public.settings.value || jsonb_build_object('on', coalesce(p_on, true), 'time', p_time), updated_at = now()
  returning value into v;
  return v;
end $$;
create or replace function public.set_autopilot(p_on boolean, p_time text) returns jsonb language sql security definer set search_path = '' as $$
  select public.set_autopilot(p_on, p_time, 'weone');
$$;

revoke all on function public.run_autopilot_now(text) from public, anon;
revoke all on function public.set_autopilot(boolean, text, text) from public, anon;
grant execute on function public.run_autopilot_now(text) to authenticated;
grant execute on function public.set_autopilot(boolean, text, text) to authenticated;

-- "Re-check" a connection: the cloud poster checks it on its next run (every minute) and writes the fresh status
create or replace function public.request_recheck(p_brand text, p_platform text) returns void language plpgsql security definer set search_path = '' as $$
declare b text; k text;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  b := public.check_brand(p_brand);
  k := case p_platform when 'ig' then 'instagram_status' when 'fb' then 'facebook_status' when 'li' then 'linkedin_status'
                       when 'web' then 'website_status' end;
  if k is null then raise exception 'Unknown connection.'; end if;
  insert into public.settings(key, value, updated_at) values (public.bkey(k, b), jsonb_build_object('recheck_at', now()), now())
  on conflict (key) do update set value = public.settings.value || jsonb_build_object('recheck_at', now()), updated_at = now();
end $$;
revoke all on function public.request_recheck(text, text) from public, anon;
grant execute on function public.request_recheck(text, text) to authenticated;
