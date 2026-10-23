-- Reel Studio 0006: the same reel also goes to the We One Aviation LinkedIn Company Page.
--  * LinkedIn app credentials (client secret) and the page token live in public.secrets (no API access), status in settings
--  * once LinkedIn is connected, Approve / auto-approve queues the reel for Instagram AND LinkedIn (jobs.output.li)

insert into public.settings(key, value) values ('linkedin_status', '{"state": "not_connected"}') on conflict (key) do nothing;
update public.settings set value = value || '{"linkedin": true}'::jsonb where key = 'post_schedule' and not (value ? 'linkedin');

create or replace function public.linkedin_on() returns boolean language sql stable security definer set search_path = '' as $$
  select coalesce((select (value ->> 'linkedin')::boolean from public.settings where key = 'post_schedule'), true)
     and coalesce((select value ->> 'state' from public.settings where key = 'linkedin_status'), 'not_connected') in ('connected', 'expired', 'error');
$$;

-- what approval adds to output: an Instagram post and (when LinkedIn is set up) a LinkedIn post, both due now
create or replace function public.post_targets(p_auto boolean) returns jsonb language sql stable security definer set search_path = '' as $$
  select jsonb_build_object('ig', jsonb_build_object('state', 'scheduled', 'at', now(), 'attempts', 0, 'manual', true, 'auto', p_auto))
      || case when public.linkedin_on() then jsonb_build_object('li', jsonb_build_object('state', 'scheduled', 'at', now(), 'attempts', 0, 'auto', p_auto)) else '{}'::jsonb end;
$$;

-- ---------------------------------------------------------------- LinkedIn app + connection
create or replace function public.set_linkedin_app(p_client_id text, p_client_secret text)
returns void language plpgsql security definer set search_path = '' as $$
declare cid text := trim(coalesce(p_client_id, '')); sec text := trim(coalesce(p_client_secret, ''));
begin
  if not public.is_owner() then raise exception 'Only the studio owner can connect LinkedIn.'; end if;
  if cid !~ '^[A-Za-z0-9]{8,40}$' then raise exception 'The Client ID looks wrong. Copy it from the Auth tab of your LinkedIn app.'; end if;
  if sec !~ '^[A-Za-z0-9_.=+/-]{10,200}$' then raise exception 'The Client Secret looks wrong. Copy it with the copy button on the Auth tab.'; end if;
  insert into public.secrets(key, value, updated_at) values ('linkedin_client_secret', sec, now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
  insert into public.settings(key, value, updated_at) values ('linkedin_app', jsonb_build_object('client_id', cid), now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
end $$;

create or replace function public.disconnect_linkedin()
returns void language plpgsql security definer set search_path = '' as $$
begin
  if not public.is_owner() then raise exception 'Only the studio owner can disconnect LinkedIn.'; end if;
  update public.secrets set value = null, updated_at = now() where key = 'linkedin_token';
  update public.settings set value = jsonb_build_object('state', 'not_connected'), updated_at = now() where key = 'linkedin_status';
end $$;

-- ---------------------------------------------------------------- approval queues both platforms
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
                         || case when p_decision = 'approve' and public.autopost_on() then public.post_targets(false) else '{}'::jsonb end
   where id = p_job returning * into j;
  return j;
end $$;

create or replace function public.auto_approve_due() returns int language plpgsql security definer set search_path = '' as $$
declare n int;
begin
  if not public.autopost_on() then return 0; end if;
  update public.jobs
     set status = 'done',
         output = output || jsonb_build_object('decision', 'auto', 'decided_at', now()) || public.post_targets(true)
   where kind = 'reel' and status = 'review' and coalesce((output ->> 'hold')::boolean, false) = false
     and (output ->> 'review_deadline')::timestamptz <= now();
  get diagnostics n = row_count;
  return n;
end $$;

-- Post now / Don't post / Retry, per platform ('ig' | 'li')
create or replace function public.set_post(p_job uuid, p_platform text, p_action text)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs; st text; label text;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  if p_platform not in ('ig', 'li') or p_action not in ('now', 'off') then raise exception 'bad request'; end if;
  label := case p_platform when 'ig' then 'Instagram' else 'LinkedIn' end;
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

revoke all on function public.linkedin_on() from public, anon;
revoke all on function public.post_targets(boolean) from public, anon, authenticated;
revoke all on function public.set_linkedin_app(text, text) from public, anon;
revoke all on function public.disconnect_linkedin() from public, anon;
revoke all on function public.set_post(uuid, text, text) from public, anon;
grant execute on function public.linkedin_on() to authenticated;
grant execute on function public.set_linkedin_app(text, text) to authenticated;
grant execute on function public.disconnect_linkedin() to authenticated;
grant execute on function public.set_post(uuid, text, text) to authenticated;
