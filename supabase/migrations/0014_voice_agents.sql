-- Reel Studio 0014: two pronunciation agents join the team.
insert into public.agents(id, name, role, team, sort) values
  ('pronunciation_editor', 'Pronunciation Editor', 'Rewrites every narration line into the exact spoken form the voice model pronounces correctly (respellings, letter-by-letter abbreviations, numbers as words); keeps each account''s own pronunciation dictionary growing.', 'production', 7),
  ('voice_inspector', 'Voice Inspector', 'Checks every recorded take word by word against what should be said, picks the take said right, has wrong parts recorded again with better spellings, and holds the reel if a word is still wrong.', 'quality', 8)
on conflict (id) do update set name = excluded.name, role = excluded.role, team = excluded.team, sort = excluded.sort;
