-- Reel Studio 0018: the voice model for the Hinglish voice and for the English voice is chosen in Studio Settings
-- (v2 = Chatterbox Multilingual V2, v3 = V3, hi = V3 Hindi Language Pack). The Mac reads it before every voice step,
-- so switching back is instant if a new model ever sounds worse.

create or replace function public.set_voice_models(p_hi text, p_en text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare v jsonb;
begin
  if not public.is_owner() then raise exception 'Only the studio owner can change the voice model.'; end if;
  if p_hi not in ('v2', 'v3', 'hi') then raise exception 'Unknown Hinglish voice model.'; end if;
  if p_en not in ('v2', 'v3') then raise exception 'Unknown English voice model.'; end if;
  update public.settings set value = value || jsonb_build_object('voice_hi', p_hi, 'voice_en', p_en, 'voice_at', now()), updated_at = now()
   where key = 'worker_settings' returning value into v;
  return v;
end $$;
revoke all on function public.set_voice_models(text, text) from public, anon;
grant execute on function public.set_voice_models(text, text) to authenticated;
