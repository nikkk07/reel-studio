-- Reel Studio 0024: more free services, saved like the Pexels/Pixabay keys (owner only, never readable from the site).
--  * groq:     backup brain for text-only steps when Claude is limited or down, and Whisper large-v3 voice checks
--  * coverr:   a third free stock-video source
--  * thumbapi: optional AI thumbnails for YouTube Shorts (free plan: 50 credits a month = 5 thumbnails, watermarked)
-- thumbapi_use (settings): {on} - thumbnails are only made when the owner switches this on.

create or replace function public.set_studio_key(p_name text, p_value text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v text := trim(coalesce(p_value, '')); st jsonb;
begin
  if not public.is_owner() then raise exception 'Only the studio owner can change keys.'; end if;
  if p_name not in ('pexels', 'pixabay', 'groq', 'coverr', 'thumbapi') then raise exception 'Unknown key.'; end if;
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
    if p_name = 'groq' and v !~ '^gsk_[A-Za-z0-9]{20,100}$' then
      raise exception 'That does not look like a Groq key (it starts with gsk_). Create one at console.groq.com/keys.';
    end if;
    if p_name in ('coverr', 'thumbapi') and v !~ '^[A-Za-z0-9._-]{16,255}$' then
      raise exception 'That does not look like an API key. Copy the whole key from the service''s dashboard.';
    end if;
    insert into public.secrets(key, value, updated_at) values ('api_' || p_name, v, now())
    on conflict (key) do update set value = excluded.value, updated_at = now();
    st := jsonb_build_object('state', 'checking', 'hint', public.key_hint(v), 'updated_at', now(), 'by', auth.jwt() ->> 'email');
  end if;
  insert into public.settings(key, value, updated_at) values ('studio_keys', jsonb_build_object(p_name, st), now())
  on conflict (key) do update set value = public.settings.value || jsonb_build_object(p_name, st), updated_at = now();
  return st;
end $$;

create or replace function public.set_thumbapi_use(p_on boolean)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v jsonb;
begin
  if not public.is_owner() then raise exception 'Only the studio owner can change this.'; end if;
  v := jsonb_build_object('on', coalesce(p_on, false), 'updated_at', now(), 'by', auth.jwt() ->> 'email');
  insert into public.settings(key, value, updated_at) values ('thumbapi_use', v, now())
  on conflict (key) do update set value = excluded.value, updated_at = now();
  return v;
end $$;
revoke all on function public.set_thumbapi_use(boolean) from public, anon;
grant execute on function public.set_thumbapi_use(boolean) to authenticated;
