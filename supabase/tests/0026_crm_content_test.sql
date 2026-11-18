\set ON_ERROR_STOP 1
create or replace function pg_temp.ok(c boolean, n text) returns void language plpgsql as $$ begin raise notice '% %', case when c then 'PASS' else 'FAIL' end, n; end $$;
-- connect Instagram + Facebook for weone so approval adds targets
insert into public.settings(key, value) values ('autopost', '{"on": true}'), ('instagram_status', '{"state":"connected"}'), ('facebook_status', '{"state":"connected"}')
  on conflict (key) do update set value = excluded.value;
select pg_temp.ok(public.plat_on('ig', 'weone') and public.plat_on('fb', 'weone'), 'S0 fixture: Instagram and Facebook connected');
insert into public.jobs(id, kind, status, brand, input, output) values
 ('00000000-0000-0000-0000-000000000001', 'reel', 'review', 'weone', jsonb_build_object('crm_item', 11, 'post_at', now() + interval '2 days'), '{}'),
 ('00000000-0000-0000-0000-000000000002', 'reel', 'review', 'weone', jsonb_build_object('crm_item', 12, 'format', 'carousel', 'post_at', now() + interval '1 day'), '{"format":"carousel"}'),
 ('00000000-0000-0000-0000-000000000003', 'reel', 'review', 'weone', '{"concept": {"title": "own"}}', '{}'),
 ('00000000-0000-0000-0000-000000000004', 'reel', 'review', 'weone', jsonb_build_object('crm_item', 13, 'post_at', now() - interval '1 hour'), '{}');
update public.jobs set output = output || jsonb_build_object('review_deadline', now() - interval '1 minute');
select public.auto_approve_due() as approved \gset
select pg_temp.ok(:approved = 4, 'S1 the review timer approves CRM items like any reel');
select pg_temp.ok((select (output->'ig'->>'at')::timestamptz between now() + interval '47 hours' and now() + interval '49 hours' and (output->'fb'->>'at')::timestamptz = (output->'ig'->>'at')::timestamptz and not output ? 'post_slot' from public.jobs where input->>'crm_item' = '11'),
  'S2 a planned reel posts at the CRM''s planned time on every platform');
select pg_temp.ok((select output ? 'ig' and not output ? 'fb' and not output ? 'li' and not output ? 'yt' from public.jobs where input->>'crm_item' = '12'), 'S3 a carousel goes to Instagram only');
select pg_temp.ok((select (output->'ig'->>'at')::timestamptz <= now() + interval '3 minutes' from public.jobs where input->>'crm_item' = '13'), 'S4 a planned time already passed posts now, never in the past');
select pg_temp.ok((select output ? 'ig' and output ? 'fb' and (output->'ig'->>'at')::timestamptz <= now() + interval '1 minute' from public.jobs where id = '00000000-0000-0000-0000-000000000003'), 'S5 Reel Studio''s own reels are unchanged');
-- held ad video is never auto-approved
insert into public.jobs(id, kind, status, brand, input, output) values ('00000000-0000-0000-0000-000000000005', 'reel', 'review', 'weone', '{"crm_item": 14, "ad": true}', '{}');
update public.jobs set output = output || jsonb_build_object('autopilot_hold', true, 'review_deadline', now() - interval '1 minute') where id = '00000000-0000-0000-0000-000000000005';
select public.auto_approve_due() as a2 \gset
select pg_temp.ok(:a2 = 0 and (select status from public.jobs where id = '00000000-0000-0000-0000-000000000005') = 'review', 'S6 an ad video is never posted by the review timer');
-- claim: content kind and the daily cap
insert into public.jobs(kind, status, brand, input) values ('content', 'queued', 'weone', '{"crm_item": 15, "make": "ad_image"}');
insert into public.jobs(kind, status, brand, input) values ('reel', 'queued', 'weone', '{"crm_item": 16}'), ('reel', 'queued', 'weone', '{"concept": {"title": "x"}}');
select pg_temp.ok((select (public.claim_next('mac', array['suggest','optimize','onboard','learn','content'], 90, false)).kind) = 'content', 'S7 lane A takes ad picture jobs');
select pg_temp.ok((select (public.claim_next('mac', array['reel'], 90, true)).input->>'crm_item') = '16', 'S8 after the daily cap a planned item still runs');
select pg_temp.ok((public.claim_next('mac', array['reel'], 90, true)).id is null, 'S9 after the daily cap an unplanned new reel still waits');
select pg_temp.ok(exists (select 1 from public.agents where id = 'carousel_designer') and exists (select 1 from public.agents where id = 'ad_designer'), 'S10 the two new agents are on the team');
