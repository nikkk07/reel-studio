-- Reel Studio 0021: Cloudflare R2 storage, short retention, Comment-to-DM, weekly digest.
--  * r2 (settings, members read): {account_id, bucket, hint, state, note, updated_at}; the two keys sit in secrets
--    (no API access). The cloud checks them; until then, and if R2 fails, reels are stored in Supabase as before.
--  * merge_reel_output: the cloud poster's atomic merge into a reel's output (retention marks, never touching posting state)
--  * Comment-to-DM per account: settings 'dm' (We One) / 'dm@<account>' {on, keywords, message, public_reply, state, note};
--    dm_log keeps one row per comment (unique), so nobody is messaged twice
--  * digest@<account>: the weekly digest written by the learning run (best/worst reel, lessons, next experiment)

create or replace function public.set_r2(p_account text, p_bucket text, p_key_id text, p_secret text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare a text := lower(trim(coalesce(p_account, ''))); b text := trim(coalesce(p_bucket, ''));
        k text := trim(coalesce(p_key_id, '')); s text := trim(coalesce(p_secret, '')); v jsonb;
begin
  if not public.is_owner() then raise exception 'Only the studio owner can change storage.'; end if;
  if a = '' and b = '' and k = '' and s = '' then
    update public.secrets set value = null, updated_at = now() where key in ('r2_access_key_id', 'r2_secret_access_key');
    v := jsonb_build_object('state', 'not_set', 'updated_at', now());
  else
    if a !~ '^[0-9a-f]{32}$' then raise exception 'The Account ID is the 32-character code on the R2 overview page (right side).'; end if;
    if b !~ '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$' then raise exception 'Use the bucket name exactly as created (lowercase letters, numbers and dashes).'; end if;
    if k !~ '^[0-9a-f]{32}$' then raise exception 'The Access Key ID is the 32-character code shown once when you create the R2 API token.'; end if;
    if s !~ '^[0-9a-f]{64}$' then raise exception 'The Secret Access Key is the 64-character code shown once when you create the R2 API token.'; end if;
    insert into public.secrets(key, value, updated_at) values ('r2_access_key_id', k, now()), ('r2_secret_access_key', s, now())
    on conflict (key) do update set value = excluded.value, updated_at = now();
    v := jsonb_build_object('state', 'checking', 'account_id', a, 'bucket', b, 'hint', public.key_hint(s), 'updated_at', now());
  end if;
  insert into public.settings(key, value, updated_at) values ('r2', v, now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
  return v;
end $$;
revoke all on function public.set_r2(text, text, text, text) from public, anon;
grant execute on function public.set_r2(text, text, text, text) to authenticated;

create or replace function public.merge_reel_output(p_job uuid, p_patch jsonb) returns void language sql security definer set search_path = '' as $$
  update public.jobs set output = output || p_patch where id = p_job and kind = 'reel';
$$;
revoke all on function public.merge_reel_output(uuid, jsonb) from public, anon, authenticated;
grant execute on function public.merge_reel_output(uuid, jsonb) to service_role;

create table if not exists public.dm_log (
  id bigint generated always as identity primary key,
  brand text not null references public.brands(id),
  comment_id text not null unique,
  media_id text, username text, keyword text, text text,
  state text not null default 'sending' check (state in ('sending', 'sent', 'failed')),
  error text, sent_at timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists dm_log_brand_idx on public.dm_log (brand, created_at desc);
alter table public.dm_log enable row level security;
drop policy if exists dm_log_read on public.dm_log;
create policy dm_log_read on public.dm_log for select to authenticated using ((select public.is_member()));
grant select on public.dm_log to authenticated;

create or replace function public.set_dm(p_brand text, p_on boolean, p_keyword text, p_message text, p_public_reply boolean)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare b text; kw text := upper(trim(coalesce(p_keyword, ''))); msg text := trim(coalesce(p_message, '')); v jsonb;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  b := public.check_brand(p_brand);
  if kw !~ '^[A-Z0-9]{2,15}$' then raise exception 'Use one keyword of 2 to 15 letters or numbers, like QUOTE.'; end if;
  if length(msg) < 20 or length(msg) > 900 then raise exception 'Write a message of 20 to 900 characters.'; end if;
  insert into public.settings(key, value, updated_at)
  values (public.bkey('dm', b), jsonb_build_object('on', coalesce(p_on, false), 'keywords', jsonb_build_array(kw), 'message', msg,
          'public_reply', coalesce(p_public_reply, true), 'state', 'checking', 'updated_at', now(), 'by', auth.jwt() ->> 'email'), now())
  on conflict (key) do update set value = public.settings.value || excluded.value, updated_at = now()
  returning value into v;
  return v;
end $$;
revoke all on function public.set_dm(text, boolean, text, text, boolean) from public, anon;
grant execute on function public.set_dm(text, boolean, text, text, boolean) to authenticated;

-- starting messages, written from each account's own published pages (editable in the account's Settings).
-- Legal Group: information only (advocates may not solicit work in India), so its message points to free explainers.
insert into public.settings(key, value) values
 ('dm', jsonb_build_object('on', true, 'state', 'checking', 'public_reply', true, 'keywords', '["PILOT"]'::jsonb, 'message',
   E'Hi {name}! Thanks for your comment.\nDGCA CPL ground classes (Dwarka + online) and the full CPL pathway: https://weoneaviation.in\nFree career counselling: call or WhatsApp +91 96673 70747.\nReply here with your questions. - We One Aviation')),
 ('dm@bookmycharter', jsonb_build_object('on', true, 'state', 'checking', 'public_reply', true, 'keywords', '["QUOTE"]'::jsonb, 'message',
   E'Hi {name}! Thanks for your comment.\nFor an itemised charter quote, reply here with your route, travel date and group size.\nHow charter pricing works: https://bookmycharter.in/how-it-works\nPlan your trip: https://bookmycharter.in - Book My Charter')),
 ('dm@flystar', jsonb_build_object('on', true, 'state', 'checking', 'public_reply', true, 'keywords', '["CPL"]'::jsonb, 'message',
   E'Hi {name}! Thanks for your comment.\nCPL course details: https://www.flystar.co.in/courses/cpl\nFree counselling: reply here or write to info@flyingstaraviator.com. - Flying Star Aviator')),
 ('dm@legalgroup', jsonb_build_object('on', true, 'state', 'checking', 'public_reply', true, 'keywords', '["GUIDE"]'::jsonb, 'message',
   E'Hi {name}! Thanks for your comment.\nOur free plain-language legal explainers: https://www.legalgroup.in/blogs\nThis is general information, not legal advice. - Legal Group'))
on conflict (key) do nothing;
