\set ON_ERROR_STOP 1
create or replace function pg_temp.ok(c boolean, n text) returns void language plpgsql as $$ begin raise notice '% %', case when c then 'PASS' else 'FAIL' end, n; end $$;
select pg_temp.ok((select value->>'mode' = 'universal' and (value->>'cap')::int = 200 and value->'public_replies'->>0 = 'Boarding pass is in your inbox 🎫'
    and value->>'message' like '%93555 66991%' and value->>'message' like '%free career counselling%' from public.settings where key = 'dm'),
  'D1 We One Aviation is universal: one message, "Boarding pass is in your inbox", 200 an hour');
select pg_temp.ok((select coalesce(value->>'mode', 'keyword') <> 'universal' from public.settings where key = 'dm@bookmycharter'), 'D2 other accounts keep keyword mode');
insert into public.dm_log (brand, comment_id, state, reason) values ('weone', 'c-skip', 'skipped', 'spam'), ('weone', 'c-pub', 'public', null);
select pg_temp.ok((select count(*) = 2 from public.dm_log where comment_id in ('c-skip', 'c-pub')), 'D3 a comment can be skipped (with the reason) or get a public reply only');
insert into public.dm_person (brand, person, username, phone, lead_state) values ('weone', '960', 'kabir', '+919811122333', 'pending');
select public.dm_lead_done('weone', '960', 'sent', 'created');
select pg_temp.ok((select lead_state = 'sent' and lead_note = 'created' from public.dm_person where person = '960'), 'D4 the Mac records that the CRM took the lead');
do $$ begin perform public.set_dm_universal('weone', '{"message": "short"}'); raise notice 'FAIL D5 a too-short message is refused';
exception when others then raise notice 'PASS D5 a too-short message is refused'; end $$;
select pg_temp.ok(not has_function_privilege('anon', 'public.set_dm_universal(text, jsonb)', 'EXECUTE')
  and not has_function_privilege('authenticated', 'public.dm_lead_done(text, text, text, text)', 'EXECUTE'), 'D6 saving needs sign-in; the lead update is for the Mac only');
