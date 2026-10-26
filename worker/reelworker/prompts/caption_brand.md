You write the Instagram caption for a finished **{{brand_name}}** reel.

Account (use only its facts and tone):
{{brand_brief}}

The reel (title, narration, verified facts with sources):
{{reel}}

Instagram account: {{handle}}

How Instagram ranks Reels in 2026: reach comes mainly from watch time, sends (DM shares) per reach and saves per reach; Instagram search reads the caption, so plain, specific keywords people search for help discovery; hashtags are capped at 5 and only describe the topic.

Write:
- `caption` in premium, plain {{language}} (350–900 characters):
  1. Line 1 (under 100 characters): a curiosity hook containing the main search keyword (e.g. "private jet charter cost in India"). No hashtags, no emoji at the start.
  2. Two to four short lines with the key facts, naturally including 2–3 more search phrases. Use ONLY facts from the reel above; never add numbers, prices or claims.
  3. "Source: " plus publisher names, no URLs.
  4. A send prompt naming who to send it to (e.g. "Send this to the friend planning a destination wedding").
  5. A short question that invites a comment.
  6. "Follow {{handle}} for more." (adapt the words to the account, keep it short)
  Short lines, a blank line between blocks, at most 2 emojis, no hashtags inside the text, no @mentions other than {{handle}}, no phone numbers or links.
- `hashtags`: exactly 5, each starting with #: 2 specific to this reel's topic, 2 for the field, and #{{brand_tag}}.

Return only the JSON object.
