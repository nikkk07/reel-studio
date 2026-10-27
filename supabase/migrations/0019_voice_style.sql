-- Reel Studio 0019: natural voice.
-- * The English voice may use the V3 Hindi pack ('hi'): it reads English with a natural Indian accent and says Indian
--   names (Udaipur, Kedarnath) the way the audience does.
-- * Delivery style per language: 'natural' (balanced, the default) or 'expressive' (more emotion and pitch movement,
--   slightly slower). The Mac reads both before every voice step.

create or replace function public.set_voice_models(p_hi text, p_en text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v jsonb;
begin
  if not public.is_owner() then raise exception 'Only the studio owner can change the voice model.'; end if;
  if p_hi not in ('v2', 'v3', 'hi') then raise exception 'Unknown Hinglish voice model.'; end if;
  if p_en not in ('v2', 'v3', 'hi') then raise exception 'Unknown English voice model.'; end if;
  update public.settings set value = value || jsonb_build_object('voice_hi', p_hi, 'voice_en', p_en, 'voice_at', now()), updated_at = now()
   where key = 'worker_settings' returning value into v;
  return v;
end $$;

create or replace function public.set_voice_style(p_hi text, p_en text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v jsonb;
begin
  if not public.is_owner() then raise exception 'Only the studio owner can change the voice style.'; end if;
  if p_hi not in ('natural', 'expressive') or p_en not in ('natural', 'expressive') then
    raise exception 'Choose natural or expressive.';
  end if;
  update public.settings set value = value || jsonb_build_object('style_hi', p_hi, 'style_en', p_en, 'voice_at', now()), updated_at = now()
   where key = 'worker_settings' returning value into v;
  return v;
end $$;
revoke all on function public.set_voice_style(text, text) from public, anon;
grant execute on function public.set_voice_style(text, text) to authenticated;
