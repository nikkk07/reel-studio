-- 0027 Universal Comment-to-DM and the Instagram inbox.
--  * dm.mode = 'universal': every comment counts (no keyword); one DM per person (dm_person), a public reply under every comment;
--    spam gets nothing and angry comments are left for a person (dm_log state 'skipped' with the reason). 'public' = a public
--    reply only, because the person already had their DM.
--  * The inbox: someone who messages the account gets the same message once, then the PDF guide (dm.pdf_url); a commenter who
--    replies gets the PDF; a phone number in a reply is kept (lead_state 'pending') and the Mac sends it to the CRM.
--  * dm_send: one row per message or reply sent, for the hourly cap (dm.cap, at most 200).
--  * set_dm_universal(): saves the universal settings. Keyword mode (set_dm) is unchanged for every other account.

alter table public.dm_log drop constraint if exists dm_log_state_check;
alter table public.dm_log add constraint dm_log_state_check check (state in ('sending', 'sent', 'failed', 'public', 'skipped'));
alter table public.dm_log add column if not exists person text;
alter table public.dm_log add column if not exists reason text;

create table if not exists public.dm_person (
  brand text not null references public.brands(id),
  person text not null,                       -- the Instagram-scoped id, or 'u:<username>' until it is known
  igsid text, username text,
  source text not null default 'comment' check (source in ('comment', 'dm')),
  dm_at timestamptz, pdf_at timestamptz,
  last_in_at timestamptz,                     -- their newest message already handled
  phone text, lead_state text check (lead_state in ('pending', 'sent', 'failed')), lead_note text, lead_at timestamptz,
  note text,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  primary key (brand, person));
create index if not exists dm_person_user_idx on public.dm_person (brand, username);
create index if not exists dm_person_lead_idx on public.dm_person (lead_state) where lead_state = 'pending';
alter table public.dm_person enable row level security;
drop policy if exists dm_person_read on public.dm_person;
create policy dm_person_read on public.dm_person for select to authenticated using ((select public.is_member()));
grant select on public.dm_person to authenticated;

create table if not exists public.dm_send (
  id bigint generated always as identity primary key,
  brand text not null references public.brands(id),
  kind text not null,
  created_at timestamptz not null default now());
create index if not exists dm_send_brand_idx on public.dm_send (brand, created_at desc);
alter table public.dm_send enable row level security;
drop policy if exists dm_send_read on public.dm_send;
create policy dm_send_read on public.dm_send for select to authenticated using ((select public.is_member()));
grant select on public.dm_send to authenticated;

create or replace function public.set_dm_universal(p_brand text, p jsonb) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare b text; msg text := btrim(coalesce(p->>'message', '')); thanks text := nullif(btrim(coalesce(p->>'thanks', '')), '');
        pdf text := nullif(btrim(coalesce(p->>'pdf_url', '')), ''); cap int := coalesce(nullif(p->>'cap', '')::int, 200);
        pubs jsonb := coalesce(p->'public_replies', '[]'); x text; v jsonb;
begin
  if not public.is_member() then raise exception 'Your account is not on the members list.'; end if;
  b := public.check_brand(p_brand);
  if length(msg) < 20 or length(msg) > 900 then raise exception 'Write a message of 20 to 900 characters.'; end if;
  if thanks is not null and length(thanks) > 400 then raise exception 'Keep the thank-you under 400 characters.'; end if;
  if pdf is not null and pdf !~ '^https://[^\s]+$' then raise exception 'The PDF needs a public https link.'; end if;
  if cap < 1 or cap > 200 then raise exception 'The hourly limit is 1 to 200 (Meta''s safe limit).'; end if;
  if jsonb_typeof(pubs) <> 'array' or jsonb_array_length(pubs) > 8 then raise exception 'Up to 8 public replies.'; end if;
  for x in select jsonb_array_elements_text(pubs) loop
    if length(btrim(x)) < 2 or length(x) > 100 then raise exception 'Each public reply is 2 to 100 characters.'; end if;
  end loop;
  insert into public.settings(key, value, updated_at)
  values (public.bkey('dm', b), jsonb_build_object('on', coalesce((p->>'on')::boolean, false), 'mode', 'universal', 'message', msg,
          'public_reply', coalesce((p->>'public_reply')::boolean, true), 'public_replies', pubs, 'pdf_url', pdf,
          'pdf_name', nullif(btrim(coalesce(p->>'pdf_name', '')), ''), 'thanks', thanks, 'inbox', coalesce((p->>'inbox')::boolean, true),
          'crm', coalesce((p->>'crm')::boolean, true), 'cap', cap, 'state', 'checking', 'updated_at', now(), 'by', auth.jwt() ->> 'email'), now())
  on conflict (key) do update set value = public.settings.value || excluded.value, updated_at = now()
  returning value into v;
  return v;
end $$;
revoke all on function public.set_dm_universal(text, jsonb) from public, anon;
grant execute on function public.set_dm_universal(text, jsonb) to authenticated;

-- the Mac sends kept phone numbers to the CRM and records the answer
create or replace function public.dm_lead_done(p_brand text, p_person text, p_state text, p_note text) returns void
language sql security definer set search_path = '' as $$
  update public.dm_person set lead_state = case when p_state in ('sent', 'failed') then p_state else lead_state end,
    lead_note = left(p_note, 300), lead_at = now(), updated_at = now() where brand = p_brand and person = p_person $$;
revoke all on function public.dm_lead_done(text, text, text, text) from public, anon, authenticated;
grant execute on function public.dm_lead_done(text, text, text, text) to service_role;

-- We One Aviation: universal, owner-approved (20 Nov 2026): same message for every comment and DM, "Boarding pass is in your
-- inbox" under every comment, at most 200 sends an hour. The PDF is added in Settings once it is ready.
update public.settings set value = value || jsonb_build_object('on', true, 'mode', 'universal', 'state', 'checking', 'inbox', true, 'crm', true, 'cap', 200,
  'public_reply', true, 'public_replies', '["Boarding pass is in your inbox 🎫", "Boarding pass is in your inbox ✈️", "Boarding pass is in your inbox 🛫"]'::jsonb,
  'message', E'Namaste {name}! We One Aviation, Dwarka ✈️\nCPL ka raasta: 10+2 Physics-Maths → DGCA Class 2 medical → DGCA exams + RTR(A) → 200 ghante flying.\nCPL training kam se kam ₹40 lakh ki hoti hai, isliye pehle free career counselling call lijiye.\nApna naam, city aur phone number yahan bhejiye. Counsellor call karke (10am–7pm) current fees WhatsApp pe likhit mein bhejenge.\nYa seedha call: 93555 66991',
  'thanks', E'Thank you {name}! Hamare counsellor 93555 66991 se call karenge (10am–7pm). Number save kar lijiye ✈️')
 where key = 'dm' and coalesce(value->>'mode', '') <> 'universal';
