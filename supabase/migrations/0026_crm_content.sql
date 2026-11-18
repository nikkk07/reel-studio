-- Reel Studio 0026: the CRM's weekly content plan (We One CRM > Growth > Content) made here.
--  * The Mac asks the CRM for due items (outbound only, token in ~/ReelStudio/.env) and turns each into a job:
--      reel      -> kind 'reel'  input {concept, source:'crm', crm_item, post_at}
--      carousel  -> kind 'reel'  input {..., format:'carousel'}  (same review, 5-minute rule, Hold and Approve as a reel;
--                   Instagram only: Facebook/LinkedIn/YouTube targets are removed on approval)
--      ad video  -> kind 'reel'  input {..., ad:true}            (never auto-posted: it is for Meta Ads Manager)
--      ad picture-> kind 'content'                               (pictures go back to the CRM; nothing is posted)
--  * A planned item posts at the CRM's planned time: approving it (by hand or by the review timer) books that time
--    instead of the next free posting slot. "Post now" still posts at once.
--  * Planned items are not blocked by the daily reel cap (the plan already caps them: 1 reel and 1 carousel a day).

alter table public.jobs drop constraint if exists jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check check (kind in ('suggest', 'optimize', 'reel', 'onboard', 'learn', 'content'));
create index if not exists jobs_crm_item_idx on public.jobs ((input ->> 'crm_item')) where input ? 'crm_item';

insert into public.agents(id, name, role, team, sort) values
  ('carousel_designer', 'Carousel Designer', 'Turns a planned carousel into 5-10 swipeable slides: a scroll-stopping cover, one idea per slide from verified facts, and a save/send ending.', 'production', 11),
  ('ad_designer', 'Ad Creative Designer', 'Writes the words on a Meta ad picture from the approved strategy''s brief: one promise, one proof, one clear action, within Meta''s text rules.', 'production', 12)
on conflict (id) do update set name = excluded.name, role = excluded.role, team = excluded.team, sort = excluded.sort;

create or replace function public.claim_next(p_worker text, p_kinds text[], p_lease_seconds int default 90, p_revisions_only boolean default false)
returns public.jobs language plpgsql security definer set search_path = '' as $$
declare j public.jobs;
begin
  select * into j from public.jobs
   where kind = any(p_kinds)
     and (not p_revisions_only or parent_id is not null or (kind = 'reel' and (input ? 'autopilot' or input ? 'crm_item')))
     and ((status in ('queued', 'waiting_quota') and coalesce(not_before, now()) <= now())
       or (status = 'running' and lease_expires_at < now()))
   order by case when kind in ('suggest', 'optimize') then 0 when kind = 'reel' then 1 when kind = 'content' then 2 when kind = 'onboard' then 3 else 4 end,
            (input ->> 'post_at') nulls last, created_at
   limit 1
   for update skip locked;
  if not found then return null; end if;
  update public.jobs
     set status = 'running', lease_owner = p_worker, lease_expires_at = now() + make_interval(secs => p_lease_seconds),
         attempts = attempts + 1, error = null
   where id = j.id returning * into j;
  return j;
end $$;
revoke all on function public.claim_next(text, text[], int, boolean) from public, anon, authenticated;
grant execute on function public.claim_next(text, text[], int, boolean) to service_role;

-- approval of a planned item: its own time, and carousels go to Instagram only
create or replace function public.crm_post_time() returns trigger language plpgsql set search_path = '' as $$
declare at_ timestamptz; k text;
begin
  if new.kind = 'reel' and new.status = 'done' and old.status = 'review' and new.input ? 'crm_item' then
    if new.input ->> 'format' = 'carousel' then
      new.output := new.output - 'li' - 'fb' - 'yt';
    end if;
    begin at_ := greatest((new.input ->> 'post_at')::timestamptz, now()); exception when others then at_ := null; end;
    if at_ is not null then
      foreach k in array array['ig', 'li', 'fb', 'yt'] loop
        if new.output -> k ->> 'state' = 'scheduled' then
          new.output := jsonb_set(new.output, array[k, 'at'], to_jsonb(at_));
        end if;
      end loop;
      new.output := new.output - 'post_slot';        -- the planned time is not one of the account's daily slots
    end if;
  end if;
  return new;
end $$;
drop trigger if exists jobs_crm_post_time on public.jobs;
create trigger jobs_crm_post_time before update of status on public.jobs for each row execute function public.crm_post_time();
