-- Reel Studio 0004: posting runs in the cloud (works while the Mac is off) + 5-minute review window.
--  * a reel that reaches Review gets review_deadline = now + 5 min (setting)
--  * Approve -> posts now; no answer by the deadline -> auto-approved and posted; Request changes / Hold -> not posted
--  * pg_cron runs auto_approve_due() every minute and calls the ig-poster Edge Function every minute

-- ---------------------------------------------------------------- settings
update public.settings set value = value || '{"mode": "instant", "review_minutes": 5}'::jsonb, updated_at = now() where key = 'post_schedule';
insert into public.settings(key, value) values ('post_schedule', '{"slots": ["19:30"], "auto": true, "tz": "Asia/Kolkata", "mode": "instant", "review_minutes": 5}')
on conflict (key) do nothing;
insert into public.secrets(key, value) values ('cron_key', encode(extensions.gen_random_bytes(24), 'hex')) on conflict (key) do nothing;

create or replace function public.review_minutes() returns int language sql stable security definer set search_path = '' as $$
  select coalesce((select (value ->> 'review_minutes')::int from public.settings where key = 'post_schedule'), 5);
$$;
create or replace function public.autopost_on() returns boolean language sql stable security definer set search_path = '' as $$
  select coalesce((select (value ->> 'auto')::boolean from public.settings where key = 'post_schedule'), true);
$$;

-- ---------------------------------------------------------------- the review window starts when a reel reaches Review
create or replace function public.set_review_deadline() returns trigger language plpgsql set search_path = '' as $$
begin
  if new.kind = 'reel' and new.status = 'review' and (tg_op = 'INSERT' or old.status is distinct from 'review') then
    new.output := coalesce(new.output, '{}'::jsonb) - 'hold'
      || jsonb_build_object('review_deadline', now() + make_interval(mins => public.review_minutes()));
  end if;
  return new;
end $$;
create or replace trigger jobs_review_deadline before insert or update on public.jobs for each row execute function public.set_review_deadline();
revoke all on function public.set_review_deadline() from public, anon, authenticated;

-- ---------------------------------------------------------------- approve = post now
create or replace function public.set_review_decision(p_job uuid, p_decision text, p_notes text default null)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  if p_decision not in ('approve', 'reject') then raise exception 'bad decision'; end if;
  select * into j from public.jobs where id = p_job and kind = 'reel' for update;
  if not found then raise exception 'This reel no longer exists.'; end if;
  if j.status = 'done' and p_decision = 'approve' then return j; end if;
  if j.status <> 'review' then raise exception 'This reel is no longer waiting for review.'; end if;
  update public.jobs
     set status = case when p_decision = 'approve' then 'done' else 'cancelled' end,
         output = output || jsonb_build_object('decision', p_decision, 'notes', p_notes, 'decided_at', now())
                         || case when p_decision = 'approve' and public.autopost_on()
                                 then jsonb_build_object('ig', jsonb_build_object('state', 'scheduled', 'at', now(), 'attempts', 0, 'manual', true))
                                 else '{}'::jsonb end
   where id = p_job returning * into j;
  return j;
end $$;

-- "Wait, I'm checking": pause / restart the 5-minute timer
create or replace function public.hold_auto_post(p_job uuid, p_hold boolean)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  select * into j from public.jobs where id = p_job and kind = 'reel' for update;
  if not found then raise exception 'This reel no longer exists.'; end if;
  if j.status <> 'review' then raise exception 'It is no longer waiting for review.'; end if;
  update public.jobs set output = case when p_hold then output || '{"hold": true}'::jsonb
                                      else (output - 'hold') || jsonb_build_object('review_deadline', now() + make_interval(mins => public.review_minutes())) end
   where id = p_job returning * into j;
  return j;
end $$;

-- the timer (pg_cron, every minute)
create or replace function public.auto_approve_due() returns int language plpgsql security definer set search_path = '' as $$
declare n int;
begin
  if not public.autopost_on() then return 0; end if;
  update public.jobs
     set status = 'done',
         output = output || jsonb_build_object('decision', 'auto', 'decided_at', now(),
                  'ig', jsonb_build_object('state', 'scheduled', 'at', now(), 'attempts', 0, 'manual', true, 'auto', true))
   where kind = 'reel' and status = 'review' and coalesce((output ->> 'hold')::boolean, false) = false
     and (output ->> 'review_deadline')::timestamptz <= now();
  get diagnostics n = row_count;
  return n;
end $$;

-- settings from the site: auto-posting on/off and the review window (1 - 60 min)
create or replace function public.set_autopost(p_auto boolean, p_minutes int)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v jsonb;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  if p_minutes is null or p_minutes < 1 or p_minutes > 60 then raise exception 'The review window must be between 1 and 60 minutes.'; end if;
  update public.settings set value = value || jsonb_build_object('auto', coalesce(p_auto, true), 'review_minutes', p_minutes, 'mode', 'instant'), updated_at = now()
   where key = 'post_schedule' returning value into v;
  return v;
end $$;

-- Post now / Don't post / Retry (a queued reel also posts now)
create or replace function public.set_reel_posting(p_job uuid, p_action text)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs; st text;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  if p_action not in ('now', 'queue', 'off') then raise exception 'bad action'; end if;
  select * into j from public.jobs where id = p_job and kind = 'reel' for update;
  if not found then raise exception 'This reel no longer exists.'; end if;
  if j.status <> 'done' then raise exception 'Approve the reel first.'; end if;
  st := j.output -> 'ig' ->> 'state';
  if st = 'posted' then raise exception 'It is already on Instagram.'; end if;
  if st = 'posting' then raise exception 'It is being posted right now.'; end if;
  update public.jobs set output = output || jsonb_build_object('ig',
      coalesce(output -> 'ig', '{}'::jsonb) - 'error' - 'at' ||
      case when p_action = 'off' then jsonb_build_object('state', 'off')
           else jsonb_build_object('state', 'scheduled', 'at', now(), 'attempts', 0, 'manual', true) end)
   where id = p_job returning * into j;
  return j;
end $$;

revoke all on function public.review_minutes() from public, anon;
revoke all on function public.autopost_on() from public, anon;
revoke all on function public.hold_auto_post(uuid, boolean) from public, anon;
revoke all on function public.auto_approve_due() from public, anon, authenticated;
revoke all on function public.set_autopost(boolean, int) from public, anon;
grant execute on function public.review_minutes() to authenticated;
grant execute on function public.autopost_on() to authenticated;
grant execute on function public.hold_auto_post(uuid, boolean) to authenticated;
grant execute on function public.set_autopost(boolean, int) to authenticated;
grant execute on function public.auto_approve_due() to service_role;
