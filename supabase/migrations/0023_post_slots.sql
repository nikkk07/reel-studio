-- Reel Studio 0023: Scheduled posting per account, separate from Autopilot.
--  * Autopilot (unchanged) only decides WHEN a reel is made. Scheduled posting decides WHEN an approved reel goes out.
--  * post_slots[@account] (settings): {on, times: ["19:30", ...] (India time, 1-3 a day), updated_at, by}
--  * When it is on, approving a reel (by hand or by the review timer) books the account's next free time slot: every
--    connected platform gets that time, and output.post_slot remembers it so two reels never share a slot.
--    "Post now" on a reel still posts at once. The cloud poster already waits for each platform's time, so posting
--    happens on time while the Mac is off; the video waits in storage (never deleted while a post is scheduled).

create or replace function public.next_post_slot(p_brand text) returns timestamptz
language plpgsql stable security definer set search_path = '' as $$
declare s jsonb; t text; d int; cand timestamptz; today date := (now() at time zone 'Asia/Kolkata')::date;
begin
  s := (select value from public.settings where key = public.bkey('post_slots', p_brand));
  if s is null or not coalesce((s ->> 'on')::boolean, false) or jsonb_array_length(coalesce(s -> 'times', '[]'::jsonb)) = 0 then return null; end if;
  for d in 0..20 loop
    for t in select x from jsonb_array_elements_text(s -> 'times') x order by x loop
      cand := ((today + d) + t::time) at time zone 'Asia/Kolkata';
      if cand > now() + interval '2 minutes' and not exists (
           select 1 from public.jobs where kind = 'reel' and brand = coalesce(nullif(p_brand, ''), 'weone') and status = 'done'
              and (output ->> 'post_slot')::timestamptz = cand) then
        return cand;
      end if;
    end loop;
  end loop;
  return null;
end $$;
revoke all on function public.next_post_slot(text) from public, anon;
grant execute on function public.next_post_slot(text) to authenticated;

-- what approval adds to a reel: one entry per connected platform, at the next free slot when scheduled posting is on
create or replace function public.post_targets(p_auto boolean, p_brand text) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare slot timestamptz := public.next_post_slot(p_brand); at_ timestamptz := coalesce(slot, now()); r jsonb := '{}'::jsonb;
begin
  if public.plat_on('ig', p_brand) then r := r || jsonb_build_object('ig', jsonb_build_object('state', 'scheduled', 'at', at_, 'attempts', 0, 'manual', true, 'auto', p_auto)); end if;
  if public.plat_on('li', p_brand) then r := r || jsonb_build_object('li', jsonb_build_object('state', 'scheduled', 'at', at_, 'attempts', 0, 'auto', p_auto)); end if;
  if public.plat_on('fb', p_brand) then r := r || jsonb_build_object('fb', jsonb_build_object('state', 'scheduled', 'at', at_, 'attempts', 0, 'auto', p_auto)); end if;
  if public.plat_on('yt', p_brand) then r := r || jsonb_build_object('yt', jsonb_build_object('state', 'scheduled', 'at', at_, 'attempts', 0, 'auto', p_auto)); end if;
  if r <> '{}'::jsonb and slot is not null then r := r || jsonb_build_object('post_slot', slot); end if;
  return r;
end $$;

-- the review timer: one reel at a time, so two reels approved in the same minute book two different slots
create or replace function public.auto_approve_due() returns int language plpgsql security definer set search_path = '' as $$
declare n int := 0; night boolean; j record; tg jsonb;
begin
  if not public.autopost_on() then return 0; end if;
  night := (now() at time zone 'Asia/Kolkata')::time not between time '07:30' and time '23:00';
  for j in select id, brand from public.jobs
            where kind = 'reel' and status = 'review'
              and coalesce((output ->> 'hold')::boolean, false) = false
              and coalesce((output ->> 'autopilot_hold')::boolean, false) = false
              and not (night and input ? 'autopilot')
              and (output ->> 'review_deadline')::timestamptz <= now()
            order by created_at for update skip locked loop
    tg := public.post_targets(true, j.brand);
    continue when j.brand <> 'weone' and tg = '{}'::jsonb;
    update public.jobs set status = 'done', output = output || jsonb_build_object('decision', 'auto', 'decided_at', now()) || tg where id = j.id;
    n := n + 1;
  end loop;
  return n;
end $$;
revoke all on function public.auto_approve_due() from public, anon, authenticated;

-- switch it on/off and set the daily times (members; same rules as Autopilot)
create or replace function public.set_post_slots(p_brand text, p_on boolean, p_times text[])
returns jsonb language plpgsql security definer set search_path = '' as $$
declare b text; ts text[]; t text; v jsonb;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  b := public.check_brand(p_brand);
  select coalesce(array_agg(distinct x order by x), '{}') into ts from unnest(coalesce(p_times, '{}')) x where nullif(trim(x), '') is not null;
  foreach t in array ts loop
    if t !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' then raise exception 'Times must look like 19:30 (24-hour, India time).'; end if;
  end loop;
  if coalesce(p_on, false) and (cardinality(ts) < 1 or cardinality(ts) > 3) then raise exception 'Choose 1 to 3 posting times a day.'; end if;
  v := jsonb_build_object('on', coalesce(p_on, false), 'times', to_jsonb(ts), 'updated_at', now(), 'by', auth.jwt() ->> 'email');
  insert into public.settings(key, value, updated_at) values (public.bkey('post_slots', b), v, now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
  return v;
end $$;
revoke all on function public.set_post_slots(text, boolean, text[]) from public, anon;
grant execute on function public.set_post_slots(text, boolean, text[]) to authenticated;
