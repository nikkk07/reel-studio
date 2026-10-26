-- Reel Studio 0015: a reel the Voice Inspector held (a word still mispronounced) keeps its hold when it reaches Review,
-- so it is never auto-posted; a person can still release it (hold_auto_post false).
create or replace function public.set_review_deadline() returns trigger language plpgsql set search_path = '' as $$
declare voice_held boolean;
begin
  if new.kind = 'reel' and new.status = 'review' and (tg_op = 'INSERT' or old.status is distinct from 'review') then
    voice_held := jsonb_typeof(new.output -> 'voice_issues') = 'object' and (new.output -> 'voice_issues') <> '{}'::jsonb;
    new.output := (case when voice_held then coalesce(new.output, '{}'::jsonb) || '{"hold": true}'::jsonb else coalesce(new.output, '{}'::jsonb) - 'hold' end)
      || jsonb_build_object('review_deadline', now() + make_interval(mins => public.review_minutes()));
  end if;
  return new;
end $$;
