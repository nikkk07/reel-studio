-- Reel Studio 0003: automatic Instagram posting of approved reels.
--  * settings: shared, readable by members (posting schedule, Instagram connection status written by the Mac)
--  * secrets: the Instagram token; nobody can read it through the API (no policies, no grants); only the Mac (service role) uses it
--  * approve = queue for the next posting slot; Post now / Don't post / Schedule from the site

create table if not exists public.settings (
  key text primary key,
  value jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
alter table public.settings enable row level security;
create table if not exists public.secrets (
  key text primary key,
  value text,
  updated_at timestamptz not null default now()
);
alter table public.secrets enable row level security;

revoke all on public.settings, public.secrets from anon, authenticated;
grant select on public.settings to authenticated;
do $$ begin
  create policy settings_read on public.settings for select to authenticated using ((select public.is_member()));
exception when duplicate_object then null; end $$;

insert into public.settings(key, value) values
  ('post_schedule', '{"slots": ["19:30"], "auto": true, "tz": "Asia/Kolkata"}'),
  ('instagram_status', '{"state": "not_connected"}')
on conflict (key) do nothing;

create or replace function public.is_owner() returns boolean language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.members m where lower(m.email) = lower(coalesce(auth.jwt() ->> 'email', '')) and m.role = 'owner');
$$;

-- ---------------------------------------------------------------- connection (token goes in, never comes out)
create or replace function public.set_instagram_token(p_token text)
returns void language plpgsql security definer set search_path = '' as $$
declare t text := trim(coalesce(p_token, ''));
begin
  if not public.is_owner() then raise exception 'Only the studio owner can connect Instagram.'; end if;
  if length(t) < 40 or length(t) > 2000 or t !~ '^[A-Za-z0-9_.|-]+$' then
    raise exception 'That does not look like an Instagram access token. Copy the whole token from the Meta app dashboard.';
  end if;
  insert into public.secrets(key, value, updated_at) values ('instagram_token', t, now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
  insert into public.settings(key, value, updated_at) values ('instagram_status', jsonb_build_object('state', 'checking', 'at', now()), now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
end $$;

create or replace function public.disconnect_instagram()
returns void language plpgsql security definer set search_path = '' as $$
begin
  if not public.is_owner() then raise exception 'Only the studio owner can disconnect Instagram.'; end if;
  update public.secrets set value = null, updated_at = now() where key = 'instagram_token';
  update public.settings set value = jsonb_build_object('state', 'not_connected'), updated_at = now() where key = 'instagram_status';
end $$;

-- ---------------------------------------------------------------- schedule
create or replace function public.set_post_schedule(p_slots text[], p_auto boolean)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare s text; clean text[] := '{}'; v jsonb;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  foreach s in array coalesce(p_slots, '{}') loop
    s := trim(s);
    if s !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' then raise exception 'Times must look like 19:30.'; end if;
    if not s = any(clean) then clean := clean || s; end if;
  end loop;
  if cardinality(clean) = 0 or cardinality(clean) > 4 then raise exception 'Choose between 1 and 4 posting times a day.'; end if;
  select array_agg(x order by x) into clean from unnest(clean) x;
  v := jsonb_build_object('slots', to_jsonb(clean), 'auto', coalesce(p_auto, true), 'tz', 'Asia/Kolkata');
  insert into public.settings(key, value, updated_at) values ('post_schedule', v, now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
  return v;
end $$;

-- per reel: p_action = 'now' | 'queue' | 'off'
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
      case p_action when 'now' then jsonb_build_object('state', 'scheduled', 'at', now(), 'attempts', 0, 'manual', true)
                    when 'queue' then jsonb_build_object('state', 'queued', 'queued_at', now(), 'attempts', 0)
                    else jsonb_build_object('state', 'off') end)
   where id = p_job returning * into j;
  return j;
end $$;

-- approving a reel queues it for the next posting slot (when auto-posting is on)
create or replace function public.set_review_decision(p_job uuid, p_decision text, p_notes text default null)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs; auto boolean;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  if p_decision not in ('approve', 'reject') then raise exception 'bad decision'; end if;
  select * into j from public.jobs where id = p_job and kind = 'reel' for update;
  if not found then raise exception 'This reel no longer exists.'; end if;
  if j.status = 'done' and p_decision = 'approve' then return j; end if;
  if j.status <> 'review' then raise exception 'This reel is no longer waiting for review.'; end if;
  select coalesce((value ->> 'auto')::boolean, true) into auto from public.settings where key = 'post_schedule';
  update public.jobs
     set status = case when p_decision = 'approve' then 'done' else 'cancelled' end,
         output = output || jsonb_build_object('decision', p_decision, 'notes', p_notes, 'decided_at', now())
                         || case when p_decision = 'approve' and coalesce(auto, true)
                                 then jsonb_build_object('ig', jsonb_build_object('state', 'queued', 'queued_at', now(), 'attempts', 0))
                                 else '{}'::jsonb end
   where id = p_job returning * into j;
  return j;
end $$;

revoke all on function public.is_owner() from public, anon;
revoke all on function public.set_instagram_token(text) from public, anon;
revoke all on function public.disconnect_instagram() from public, anon;
revoke all on function public.set_post_schedule(text[], boolean) from public, anon;
revoke all on function public.set_reel_posting(uuid, text) from public, anon;
grant execute on function public.is_owner() to authenticated;
grant execute on function public.set_instagram_token(text) to authenticated;
grant execute on function public.disconnect_instagram() to authenticated;
grant execute on function public.set_post_schedule(text[], boolean) to authenticated;
grant execute on function public.set_reel_posting(uuid, text) to authenticated;
grant all on public.settings, public.secrets to service_role;
