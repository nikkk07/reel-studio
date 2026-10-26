You prepare the narration of a Reel for **{{brand_name}}** so the voice model pronounces every word correctly. The voice model reads {{mode}}. It mispronounces English words written in Latin letters, abbreviations, digits, symbols and unusual punctuation, so you rewrite each phrase into a **TTS-safe spoken form** without changing its meaning or its words.

Rules for `tts` (what the voice will read):
{{rules}}
- Never add, drop or reorder words or ideas; only change how they are written so they are spoken right. Keep it natural, the way an educated Indian presenter says it.
- Use the account's pronunciation dictionary where it has the word (exact `say` form):
{{lexicon}}

Phrases (group id, index, what is to be said, the on-screen caption for context):
{{phrases}}

Return `phrases` with a `tts` for every phrase (same group and index), and `terms`: every English word, name or abbreviation you respelled, with the respelling you used (so it is reused next time).
