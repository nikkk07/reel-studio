-- Reel Studio 0016: every account has its own connections; studio-wide keys are managed from the site.
--  * per-account settings/secrets keys: '<base>' for We One Aviation (unchanged), '<base>@<account>' for every other account
--  * Instagram / Facebook / LinkedIn: connect, disconnect, auto-post and Post now work per account. One Instagram account,
--    Facebook Page or LinkedIn page can belong to one account only (the cloud poster refuses a duplicate).
--  * studio keys (Pexels, Pixabay, Meta app, LinkedIn app): pasted on the site, never readable back (only the last 4
--    characters are shown), checked in the cloud; the Mac reads them from here and falls back to its own .env
--  * an account's profile (name, tagline, website, social links) and the Mac's daily reel limit are editable on the site
-- Everything here works while the Mac and Claude are offline.

create or replace function public.bkey(p_base text, p_brand text) returns text language sql immutable set search_path = '' as $$
  select case when coalesce(nullif(p_brand, ''), 'weone') = 'weone' then p_base else p_base || '@' || p_brand end;
$$;
revoke all on function public.bkey(text, text) from public, anon;
grant execute on function public.bkey(text, text) to authenticated, service_role;

create or replace function public.plat_state(p_platform text, p_brand text) returns text language sql stable security definer set search_path = '' as $$
  select coalesce((select value ->> 'state' from public.settings where key = public.bkey(
    case p_platform when 'ig' then 'instagram_status' when 'fb' then 'facebook_status' else 'linkedin_status' end, p_brand)), 'not_connected');
$$;
-- a platform takes posts when it is connected or temporarily failing (posts wait for it), never when it was never connected.
-- We One Aviation's Instagram keeps its original behaviour: always a target (posts wait if the connection drops).
create or replace function public.plat_on(p_platform text, p_brand text) returns boolean language sql stable security definer set search_path = '' as $$
  select case
    when p_platform = 'ig' and coalesce(nullif(p_brand, ''), 'weone') = 'weone' then true
    when p_platform = 'ig' then public.plat_state('ig', p_brand) in ('connected', 'expired', 'error', 'checking')
    when p_platform = 'li' then public.plat_state('li', p_brand) in ('connected', 'expired', 'error')
         and coalesce((select (value ->> 'linkedin')::boolean from public.settings where key = 'post_schedule'), true)
    when p_platform = 'fb' then public.plat_state('fb', p_brand) in ('connected', 'expired', 'error')
    else false end;
$$;
revoke all on function public.plat_state(text, text) from public, anon;
revoke all on function public.plat_on(text, text) from public, anon;
grant execute on function public.plat_state(text, text) to authenticated;
grant execute on function public.plat_on(text, text) to authenticated;

-- what approval adds to a reel: one entry per platform this account has connected
create or replace function public.post_targets(p_auto boolean, p_brand text) returns jsonb language sql stable security definer set search_path = '' as $$
  select case when public.plat_on('ig', p_brand) then jsonb_build_object('ig', jsonb_build_object('state', 'scheduled', 'at', now(), 'attempts', 0, 'manual', true, 'auto', p_auto)) else '{}'::jsonb end
      || case when public.plat_on('li', p_brand) then jsonb_build_object('li', jsonb_build_object('state', 'scheduled', 'at', now(), 'attempts', 0, 'auto', p_auto)) else '{}'::jsonb end
      || case when public.plat_on('fb', p_brand) then jsonb_build_object('fb', jsonb_build_object('state', 'scheduled', 'at', now(), 'attempts', 0, 'auto', p_auto)) else '{}'::jsonb end;
$$;
create or replace function public.post_targets(p_auto boolean) returns jsonb language sql stable security definer set search_path = '' as $$
  select public.post_targets(p_auto, 'weone');
$$;
revoke all on function public.post_targets(boolean, text) from public, anon, authenticated;
revoke all on function public.post_targets(boolean) from public, anon, authenticated;

-- the review timer auto-posts reels of every account that has its own connected platform (nothing else is ever touched)
create or replace function public.auto_approve_due() returns int language plpgsql security definer set search_path = '' as $$
declare n int; night boolean;
begin
  if not public.autopost_on() then return 0; end if;
  night := (now() at time zone 'Asia/Kolkata')::time not between time '07:30' and time '23:00';
  update public.jobs
     set status = 'done',
         output = output || jsonb_build_object('decision', 'auto', 'decided_at', now()) || public.post_targets(true, brand)
   where kind = 'reel' and status = 'review'
     and (brand = 'weone' or public.post_targets(true, brand) <> '{}'::jsonb)
     and coalesce((output ->> 'hold')::boolean, false) = false
     and coalesce((output ->> 'autopilot_hold')::boolean, false) = false
     and not (night and input ? 'autopilot')
     and (output ->> 'review_deadline')::timestamptz <= now();
  get diagnostics n = row_count;
  return n;
end $$;

-- Post now / Don't post / Retry: only to the reel's own account's connected platform
create or replace function public.set_post(p_job uuid, p_platform text, p_action text)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs; st text; label text;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  if p_platform not in ('ig', 'li', 'fb') or p_action not in ('now', 'off') then raise exception 'bad request'; end if;
  label := case p_platform when 'ig' then 'Instagram' when 'li' then 'LinkedIn' else 'Facebook' end;
  select * into j from public.jobs where id = p_job and kind = 'reel' for update;
  if not found then raise exception 'This reel no longer exists.'; end if;
  if p_action = 'now' and not public.plat_on(p_platform, j.brand) then
    raise exception 'Connect this account''s own % first (account Settings). Reels never post to another account.', label;
  end if;
  if j.status <> 'done' then raise exception 'Approve the reel first.'; end if;
  st := j.output -> p_platform ->> 'state';
  if st = 'posted' then raise exception 'It is already on %.', label; end if;
  if st in ('posting', 'uploading', 'processing') then raise exception 'It is being posted to % right now.', label; end if;
  update public.jobs set output = output || jsonb_build_object(p_platform,
      coalesce(output -> p_platform, '{}'::jsonb) - 'error' - 'at' ||
      case when p_action = 'off' then jsonb_build_object('state', 'off')
           else jsonb_build_object('state', 'scheduled', 'at', now(), 'attempts', 0, 'manual', true) end)
   where id = p_job returning * into j;
  return j;
end $$;

-- ---------------------------------------------------------------- per-account connect / disconnect (owner only; tokens go in, never come out)
-- (the original one-argument / no-argument versions stay for We One Aviation; these take the account explicitly)
create or replace function public.check_brand(p_brand text) returns text language plpgsql stable security definer set search_path = '' as $$
declare b text := coalesce(nullif(p_brand, ''), 'weone');
begin
  if not exists (select 1 from public.brands where id = b) then raise exception 'Unknown account.'; end if;
  return b;
end $$;
revoke all on function public.check_brand(text) from public, anon, authenticated;

create or replace function public.set_instagram_token(p_token text, p_brand text)
returns void language plpgsql security definer set search_path = '' as $$
declare t text := trim(coalesce(p_token, '')); b text;
begin
  if not public.is_owner() then raise exception 'Only the studio owner can connect Instagram.'; end if;
  b := public.check_brand(p_brand);
  if length(t) < 40 or length(t) > 2000 or t !~ '^[A-Za-z0-9_.|-]+$' then
    raise exception 'That does not look like an Instagram access token. Copy the whole token from the Meta app dashboard.';
  end if;
  insert into public.secrets(key, value, updated_at) values (public.bkey('instagram_token', b), t, now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
  insert into public.settings(key, value, updated_at) values (public.bkey('instagram_status', b), jsonb_build_object('state', 'checking', 'at', now()), now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
end $$;

create or replace function public.disconnect_instagram(p_brand text)
returns void language plpgsql security definer set search_path = '' as $$
declare b text;
begin
  if not public.is_owner() then raise exception 'Only the studio owner can disconnect Instagram.'; end if;
  b := public.check_brand(p_brand);
  update public.secrets set value = null, updated_at = now() where key = public.bkey('instagram_token', b);
  insert into public.settings(key, value, updated_at) values (public.bkey('instagram_status', b), '{"state": "not_connected"}', now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
end $$;

create or replace function public.disconnect_facebook(p_brand text)
returns void language plpgsql security definer set search_path = '' as $$
declare b text;
begin
  if not public.is_owner() then raise exception 'Only the studio owner can disconnect Facebook.'; end if;
  b := public.check_brand(p_brand);
  update public.secrets set value = null, updated_at = now() where key = public.bkey('facebook_page_token', b);
  insert into public.settings(key, value, updated_at) values (public.bkey('facebook_status', b), '{"state": "not_connected"}', now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
end $$;

create or replace function public.disconnect_linkedin(p_brand text)
returns void language plpgsql security definer set search_path = '' as $$
declare b text;
begin
  if not public.is_owner() then raise exception 'Only the studio owner can disconnect LinkedIn.'; end if;
  b := public.check_brand(p_brand);
  update public.secrets set value = null, updated_at = now() where key in (public.bkey('linkedin_token', b), public.bkey('linkedin_refresh', b));
  insert into public.settings(key, value, updated_at) values (public.bkey('linkedin_status', b), '{"state": "not_connected"}', now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
end $$;

revoke all on function public.set_instagram_token(text, text) from public, anon;
revoke all on function public.disconnect_instagram(text) from public, anon;
revoke all on function public.disconnect_facebook(text) from public, anon;
revoke all on function public.disconnect_linkedin(text) from public, anon;
grant execute on function public.set_instagram_token(text, text) to authenticated;
grant execute on function public.disconnect_instagram(text) to authenticated;
grant execute on function public.disconnect_facebook(text) to authenticated;
grant execute on function public.disconnect_linkedin(text) to authenticated;
