-- Reel Studio 0022: YouTube Shorts. Each account connects its own YouTube channel (Google sign-in, done server side by
-- the yt-auth function); approved reels are uploaded there as Shorts by the cloud poster, like Instagram/Facebook/LinkedIn.
--  * youtube_app (settings): {client_id, hint}; the Client Secret sits in secrets (no API access)
--  * youtube_status[@account] (settings): {state, channel_id, channel_title, handle, checked_at, note}
--  * youtube_refresh[@account] (secrets): the long-lived Google refresh token
--  * jobs.output.yt: scheduled -> uploading -> processing -> posted (same rules as the other platforms)

create or replace function public.set_youtube_app(p_client_id text, p_client_secret text)
returns void language plpgsql security definer set search_path = '' as $$
declare cid text := trim(coalesce(p_client_id, '')); sec text := trim(coalesce(p_client_secret, ''));
begin
  if not public.is_owner() then raise exception 'Only the studio owner can set up YouTube.'; end if;
  if cid !~ '^[0-9]{6,20}-[a-z0-9]{20,40}\.apps\.googleusercontent\.com$' then
    raise exception 'The Client ID looks wrong. Copy the whole ID ending in .apps.googleusercontent.com (Google Cloud → Clients).';
  end if;
  if sec !~ '^[A-Za-z0-9_-]{20,64}$' then raise exception 'The Client Secret looks wrong. Copy it from the same OAuth client (it usually starts with GOCSPX-).'; end if;
  insert into public.secrets(key, value, updated_at) values ('youtube_client_secret', sec, now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
  insert into public.settings(key, value, updated_at) values ('youtube_app', jsonb_build_object('client_id', cid, 'hint', public.key_hint(sec), 'updated_at', now()), now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
end $$;
revoke all on function public.set_youtube_app(text, text) from public, anon;
grant execute on function public.set_youtube_app(text, text) to authenticated;

create or replace function public.disconnect_youtube(p_brand text)
returns void language plpgsql security definer set search_path = '' as $$
declare b text;
begin
  if not public.is_owner() then raise exception 'Only the studio owner can disconnect YouTube.'; end if;
  b := public.check_brand(p_brand);
  update public.secrets set value = null, updated_at = now() where key = public.bkey('youtube_refresh', b);
  insert into public.settings(key, value, updated_at) values (public.bkey('youtube_status', b), '{"state": "not_connected"}', now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
end $$;
revoke all on function public.disconnect_youtube(text) from public, anon;
grant execute on function public.disconnect_youtube(text) to authenticated;

-- the platform helpers learn 'yt' (everything else unchanged)
create or replace function public.plat_state(p_platform text, p_brand text) returns text language sql stable security definer set search_path = '' as $$
  select coalesce((select value ->> 'state' from public.settings where key = public.bkey(
    case p_platform when 'ig' then 'instagram_status' when 'fb' then 'facebook_status' when 'yt' then 'youtube_status' else 'linkedin_status' end, p_brand)), 'not_connected');
$$;
create or replace function public.plat_on(p_platform text, p_brand text) returns boolean language sql stable security definer set search_path = '' as $$
  select case
    when p_platform = 'ig' and coalesce(nullif(p_brand, ''), 'weone') = 'weone' then true
    when p_platform = 'ig' then public.plat_state('ig', p_brand) in ('connected', 'expired', 'error', 'checking')
    when p_platform = 'li' then public.plat_state('li', p_brand) in ('connected', 'expired', 'error')
         and coalesce((select (value ->> 'linkedin')::boolean from public.settings where key = 'post_schedule'), true)
    when p_platform = 'fb' then public.plat_state('fb', p_brand) in ('connected', 'expired', 'error')
    when p_platform = 'yt' then public.plat_state('yt', p_brand) in ('connected', 'expired', 'error')
    else false end;
$$;
create or replace function public.post_targets(p_auto boolean, p_brand text) returns jsonb language sql stable security definer set search_path = '' as $$
  select case when public.plat_on('ig', p_brand) then jsonb_build_object('ig', jsonb_build_object('state', 'scheduled', 'at', now(), 'attempts', 0, 'manual', true, 'auto', p_auto)) else '{}'::jsonb end
      || case when public.plat_on('li', p_brand) then jsonb_build_object('li', jsonb_build_object('state', 'scheduled', 'at', now(), 'attempts', 0, 'auto', p_auto)) else '{}'::jsonb end
      || case when public.plat_on('fb', p_brand) then jsonb_build_object('fb', jsonb_build_object('state', 'scheduled', 'at', now(), 'attempts', 0, 'auto', p_auto)) else '{}'::jsonb end
      || case when public.plat_on('yt', p_brand) then jsonb_build_object('yt', jsonb_build_object('state', 'scheduled', 'at', now(), 'attempts', 0, 'auto', p_auto)) else '{}'::jsonb end;
$$;

create or replace function public.set_post(p_job uuid, p_platform text, p_action text)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs; st text; label text;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  if p_platform not in ('ig', 'li', 'fb', 'yt') or p_action not in ('now', 'off') then raise exception 'bad request'; end if;
  label := case p_platform when 'ig' then 'Instagram' when 'li' then 'LinkedIn' when 'yt' then 'YouTube' else 'Facebook' end;
  select * into j from public.jobs where id = p_job and kind = 'reel' for update;
  if not found then raise exception 'This reel no longer exists.'; end if;
  if p_action = 'now' and not public.plat_on(p_platform, j.brand) then
    raise exception 'Connect this account''s own % first (account Settings). Reels never post to another account.', label;
  end if;
  if j.status <> 'done' then raise exception 'Approve the reel first.'; end if;
  if p_action = 'now' and coalesce((j.output ->> 'pruned')::boolean, false) then
    raise exception 'This reel''s video was removed after 3 days, so it cannot be posted any more.';
  end if;
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

create or replace function public.request_recheck(p_brand text, p_platform text) returns void language plpgsql security definer set search_path = '' as $$
declare b text; k text;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  b := public.check_brand(p_brand);
  k := case p_platform when 'ig' then 'instagram_status' when 'fb' then 'facebook_status' when 'li' then 'linkedin_status'
                       when 'yt' then 'youtube_status' when 'web' then 'website_status' end;
  if k is null then raise exception 'Unknown connection.'; end if;
  insert into public.settings(key, value, updated_at) values (public.bkey(k, b), jsonb_build_object('recheck_at', now()), now())
  on conflict (key) do update set value = public.settings.value || jsonb_build_object('recheck_at', now()), updated_at = now();
end $$;
