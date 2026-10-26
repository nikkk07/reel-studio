-- Reel Studio 0017: studio-wide keys and account profiles, edited on the site (works while the Mac and Claude are offline).
--  * studio_keys (settings, members read): per key {state, hint (last 4 characters), updated_at, by, note}
--  * the key itself sits in secrets (no API access); the cloud poster checks it, the Mac uses it (else its own .env)

insert into public.settings(key, value) values ('studio_keys', '{}'::jsonb) on conflict (key) do nothing;
insert into public.settings(key, value) values ('worker_settings', '{}'::jsonb) on conflict (key) do nothing;

create or replace function public.key_hint(p_value text) returns text language sql immutable set search_path = '' as $$
  select '…' || right(p_value, 4);
$$;
revoke all on function public.key_hint(text) from public, anon, authenticated;

-- p_name: pexels | pixabay. Empty value removes the cloud key (the Mac then uses the key in its own .env, if any).
create or replace function public.set_studio_key(p_name text, p_value text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v text := trim(coalesce(p_value, '')); st jsonb;
begin
  if not public.is_owner() then raise exception 'Only the studio owner can change keys.'; end if;
  if p_name not in ('pexels', 'pixabay') then raise exception 'Unknown key.'; end if;
  if v = '' then
    update public.secrets set value = null, updated_at = now() where key = 'api_' || p_name;
    st := jsonb_build_object('state', 'not_set', 'updated_at', now(), 'by', auth.jwt() ->> 'email');
  else
    if p_name = 'pexels' and v !~ '^[A-Za-z0-9]{30,100}$' then
      raise exception 'That does not look like a Pexels key. Copy it from pexels.com/api (Your API key).';
    end if;
    if p_name = 'pixabay' and v !~ '^[0-9]{4,12}-[A-Za-z0-9]{16,64}$' then
      raise exception 'That does not look like a Pixabay key (numbers, a dash, then letters and numbers). Copy it from pixabay.com/api/docs.';
    end if;
    insert into public.secrets(key, value, updated_at) values ('api_' || p_name, v, now())
    on conflict (key) do update set value = excluded.value, updated_at = now();
    st := jsonb_build_object('state', 'checking', 'hint', public.key_hint(v), 'updated_at', now(), 'by', auth.jwt() ->> 'email');
  end if;
  update public.settings set value = value || jsonb_build_object(p_name, st), updated_at = now() where key = 'studio_keys';
  return st;
end $$;

-- the Meta (Facebook) developer app: saved once, used by "Connect Facebook" of every account
create or replace function public.set_meta_app(p_app_id text, p_app_secret text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare id text := trim(coalesce(p_app_id, '')); sec text := trim(coalesce(p_app_secret, '')); v jsonb;
begin
  if not public.is_owner() then raise exception 'Only the studio owner can change the Meta app.'; end if;
  if id = '' and sec = '' then
    update public.secrets set value = null, updated_at = now() where key = 'meta_app_secret';
    v := jsonb_build_object('state', 'not_set', 'updated_at', now());
  else
    if id !~ '^[0-9]{6,20}$' then raise exception 'The App ID is the number in App settings → Basic (not the Instagram app ID).'; end if;
    if sec !~ '^[A-Fa-f0-9]{32}$' then raise exception 'The App Secret is the 32-character code in App settings → Basic (click Show).'; end if;
    insert into public.secrets(key, value, updated_at) values ('meta_app_secret', sec, now())
    on conflict (key) do update set value = excluded.value, updated_at = now();
    v := jsonb_build_object('state', 'checking', 'app_id', id, 'hint', public.key_hint(sec), 'updated_at', now());
  end if;
  insert into public.settings(key, value, updated_at) values ('meta_app', v, now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
  return v;
end $$;

-- the LinkedIn developer app (same rules as before, plus the visible hint)
create or replace function public.set_linkedin_app(p_client_id text, p_client_secret text)
returns void language plpgsql security definer set search_path = '' as $$
declare cid text := trim(coalesce(p_client_id, '')); sec text := trim(coalesce(p_client_secret, ''));
begin
  if not public.is_owner() then raise exception 'Only the studio owner can connect LinkedIn.'; end if;
  if cid !~ '^[A-Za-z0-9]{8,40}$' then raise exception 'The Client ID looks wrong. Copy it from the Auth tab of your LinkedIn app.'; end if;
  if sec !~ '^[A-Za-z0-9_.=+/-]{10,200}$' then raise exception 'The Client Secret looks wrong. Copy it with the copy button on the Auth tab.'; end if;
  insert into public.secrets(key, value, updated_at) values ('linkedin_client_secret', sec, now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
  insert into public.settings(key, value, updated_at) values ('linkedin_app', jsonb_build_object('client_id', cid, 'hint', public.key_hint(sec), 'updated_at', now()), now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
end $$;

-- an account's public profile: name, tagline, website and its social pages (members)
create or replace function public.update_brand(p_brand text, p_patch jsonb)
returns public.brands language plpgsql security definer set search_path = '' as $$
declare b public.brands; v text; k text; links jsonb; ig text;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  select * into b from public.brands where id = p_brand for update;
  if not found then raise exception 'Unknown account.'; end if;
  if p_patch ? 'name' then
    v := trim(coalesce(p_patch ->> 'name', ''));
    if length(v) < 2 or length(v) > 60 then raise exception 'The name must be 2-60 characters.'; end if;
    b.name := v;
  end if;
  if p_patch ? 'tagline' then
    v := trim(coalesce(p_patch ->> 'tagline', ''));
    if length(v) > 120 then raise exception 'Keep the tagline under 120 characters.'; end if;
    b.tagline := nullif(v, '');
  end if;
  if p_patch ? 'website' then
    v := trim(coalesce(p_patch ->> 'website', ''));
    if v !~ '^https?://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/.*)?$' or length(v) > 300 then raise exception 'Give the website address, starting with https://'; end if;
    b.website := v;
  end if;
  if p_patch ? 'links' then
    links := coalesce(b.links, '{}'::jsonb);
    foreach k in array array['instagram', 'facebook', 'linkedin', 'youtube'] loop
      if (p_patch -> 'links') ? k then
        v := trim(coalesce(p_patch -> 'links' ->> k, ''));
        if v = '' then links := links - k;
        else
          if v !~ '^https://([a-z0-9-]+\.)*(instagram\.com|facebook\.com|fb\.com|linkedin\.com|youtube\.com|youtu\.be)/[^\s<>"]*$' or length(v) > 300 then
            raise exception 'Use the full link, e.g. https://www.instagram.com/yourpage (for %).', k;
          end if;
          links := links || jsonb_build_object(k, v);
        end if;
      end if;
    end loop;
    b.links := links;
    ig := nullif(regexp_replace(coalesce(links ->> 'instagram', ''), '^https://([a-z0-9-]+\.)*instagram\.com/([A-Za-z0-9._]{1,30}).*$', '\2'), coalesce(links ->> 'instagram', ''));
    b.instagram := case when ig in ('p', 'reel', 'reels', 'stories', 'explore', 'tv') then null else ig end;
  end if;
  update public.brands set name = b.name, tagline = b.tagline, website = b.website, links = b.links, instagram = b.instagram
   where id = p_brand returning * into b;
  return b;
end $$;

-- the Mac's daily limit of new reels (it reads this on every job; Request changes still runs after the limit)
create or replace function public.set_worker_settings(p_daily_cap int)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v jsonb;
begin
  if not public.is_owner() then raise exception 'Only the studio owner can change this.'; end if;
  if p_daily_cap is null or p_daily_cap < 1 or p_daily_cap > 10 then raise exception 'Choose between 1 and 10 reels a day.'; end if;
  update public.settings set value = value || jsonb_build_object('daily_cap', p_daily_cap, 'updated_at', now()), updated_at = now()
   where key = 'worker_settings' returning value into v;
  return v;
end $$;

revoke all on function public.set_studio_key(text, text) from public, anon;
revoke all on function public.set_meta_app(text, text) from public, anon;
revoke all on function public.update_brand(text, jsonb) from public, anon;
revoke all on function public.set_worker_settings(int) from public, anon;
grant execute on function public.set_studio_key(text, text) to authenticated;
grant execute on function public.set_meta_app(text, text) to authenticated;
grant execute on function public.update_brand(text, jsonb) to authenticated;
grant execute on function public.set_worker_settings(int) to authenticated;
grant execute on function public.set_linkedin_app(text, text) to authenticated;

-- the cloud poster writes a key's check result, only if the key was not replaced meanwhile
create or replace function public.set_key_status(p_name text, p_saved_at text, p_status jsonb) returns boolean
language plpgsql security definer set search_path = '' as $$
declare n int;
begin
  update public.settings set value = value || jsonb_build_object(p_name, (value -> p_name) || p_status), updated_at = now()
   where key = 'studio_keys' and value -> p_name ->> 'updated_at' is not distinct from p_saved_at;
  get diagnostics n = row_count;
  return n > 0;
end $$;
revoke all on function public.set_key_status(text, text, jsonb) from public, anon, authenticated;
grant execute on function public.set_key_status(text, text, jsonb) to service_role;
