-- Reel Studio 0025: the CTA Strategist joins the agent team. For every reel it picks the topic's own comment keyword
-- and writes the useful DM (key points from the verified facts + the right page), so Comment-to-DM differs per reel.
insert into public.agents(id, name, role, team, sort) values
  ('cta_strategist', 'CTA Strategist', 'Picks each reel''s own comment keyword and writes the DM a commenter receives: the reel''s key points from verified facts, the right page and one next step.', 'production', 10)
on conflict (id) do update set name = excluded.name, role = excluded.role, team = excluded.team, sort = excluded.sort;
