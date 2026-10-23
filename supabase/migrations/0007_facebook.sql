-- Reel Studio 0007: the same reel also goes to the We One Aviation Facebook Page (Page Reels API).
-- Page token + app secret live in public.secrets (no API access); status in settings; approval queues jobs.output.fb.

insert into public.settings(key, value) values ('facebook_status', '{"state": "not_connected"}') on conflict (key) do nothing;

create or replace function public.facebook_on() returns boolean language sql stable security definer set search_path = '' as $$
  select coalesce((select value ->> 'state' from public.settings where key = 'facebook_status'), 'not_connected') in ('connected', 'expired', 'error');
$$;

create or replace function public.post_targets(p_auto boolean) returns jsonb language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('ig', jsonb_build_object('state', 'scheduled', 'at', now(), 'attempts', 0, 'manual', true, 'auto', p_auto))
      || case when public.linkedin_on() then jsonb_build_object('li', jsonb_build_object('state', 'scheduled', 'at', now(), 'attempts', 0, 'auto', p_auto)) else '{}'::jsonb end
      || case when public.facebook_on() then jsonb_build_object('fb', jsonb_build_object('state', 'scheduled', 'at', now(), 'attempts', 0, 'auto', p_auto)) else '{}'::jsonb end;
$$;

create or replace function public.disconnect_facebook()
returns void language plpgsql security definer set search_path = '' as $$
begin
  if not public.is_owner() then raise exception 'Only the studio owner can disconnect Facebook.'; end if;
  update public.secrets set value = null, updated_at = now() where key = 'facebook_page_token';
  update public.settings set value = jsonb_build_object('state', 'not_connected'), updated_at = now() where key = 'facebook_status';
end $$;

create or replace function public.set_post(p_job uuid, p_platform text, p_action text)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs; st text; label text;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  if p_platform not in ('ig', 'li', 'fb') or p_action not in ('now', 'off') then raise exception 'bad request'; end if;
  label := case p_platform when 'ig' then 'Instagram' when 'li' then 'LinkedIn' else 'Facebook' end;
  select * into j from public.jobs where id = p_job and kind = 'reel' for update;
  if not found then raise exception 'This reel no longer exists.'; end if;
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

revoke all on function public.facebook_on() from public, anon;
revoke all on function public.disconnect_facebook() from public, anon;
grant execute on function public.facebook_on() to authenticated;
grant execute on function public.disconnect_facebook() to authenticated;
