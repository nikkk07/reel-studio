"""JSON schemas for every Claude decision (validated by Claude Code via --json-schema)."""

S = {"type": "string"}
N = {"type": "number"}
I = {"type": "integer"}
B = {"type": "boolean"}
SRC = {"type": "object", "properties": {"title": S, "url": S}, "required": ["title", "url"]}

TOPIC = {"type": "object", "required": ["title", "hook", "angle", "why_viral", "key_facts", "sources"],
         "properties": {"title": S, "hook": S, "angle": S, "why_viral": S, "key_facts": {"type": "array", "items": S},
                        "sources": {"type": "array", "items": SRC}, "risk_note": S, "est_seconds": I,
                        "lane": {"type": "string", "enum": ["core", "trending"]}}}

SUGGEST = {"type": "object", "required": ["topics"],
           "properties": {"topics": {"type": "array", "minItems": 5, "maxItems": 6, "items": TOPIC}}}

OPTIMIZE = {"type": "object", "required": ["title", "hook", "concept", "outline", "key_facts", "sources"],
            "properties": {"title": S, "hook": S, "concept": S, "outline": {"type": "array", "items": S},
                           "key_facts": {"type": "array", "items": S}, "sources": {"type": "array", "items": SRC},
                           "notes_for_user": S}}

GRAPHIC_TYPES = ["chip", "big_text", "counter", "list_card", "quote_card", "compare", "timer", "flash", "dim", "shake", "plane_path"]

SPEC = {"type": "object",
        "required": ["title", "voice", "mood", "groups", "shots", "graphics", "claims"],
        "properties": {
            "title": S,
            "voice": {"type": "string", "enum": ["female", "male"]},
            "mood": {"type": "string", "enum": ["tense", "calm", "curious", "dramatic", "uplifting"]},
            "groups": {"type": "array", "minItems": 3, "maxItems": 6, "items": {
                "type": "object", "required": ["id", "phrases"],
                "properties": {"id": S, "gap_after": N, "phrases": {"type": "array", "minItems": 1, "maxItems": 6, "items": {
                    "type": "object", "required": ["say", "caption"], "properties": {"say": S, "caption": S}}}}}},
            "shots": {"type": "array", "minItems": 4, "maxItems": 14, "items": {
                "type": "object", "required": ["id", "query", "at_group", "at_phrase"],
                "properties": {"id": S, "query": S, "at_group": S, "at_phrase": I, "note": S}}},
            "graphics": {"type": "array", "items": {
                "type": "object", "required": ["type", "at_group", "at_phrase", "params"],
                "properties": {"type": {"type": "string", "enum": GRAPHIC_TYPES}, "at_group": S, "at_phrase": I,
                               "until_group": S, "until_phrase": I, "params": {"type": "object"}}}},
            "claims": {"type": "array", "minItems": 1, "items": {
                "type": "object", "required": ["claim", "value_used", "source_url", "status"],
                "properties": {"claim": S, "value_used": S, "source_title": S, "source_url": S, "group": S,
                               "status": {"type": "string", "enum": ["verified", "estimate"]}}}},
        }}

FOOTAGE = {"type": "object", "required": ["picks"],
           "properties": {"picks": {"type": "array", "items": {
               "type": "object", "required": ["shot_id", "candidate"],
               "properties": {"shot_id": S, "candidate": S, "alternates": {"type": "array", "items": S},
                              "focus_x": N, "in_point": N, "reason": S, "new_query": S}}}}}

QC = {"type": "object", "required": ["pass", "score", "issues", "summary"],
      "properties": {"pass": B, "score": I, "summary": S, "issues": {"type": "array", "items": {
          "type": "object", "required": ["t", "problem", "fix"],
          "properties": {"t": N, "problem": S, "fix": {"type": "string", "enum": ["repick_shot", "none"]}, "shot_id": S}}}}}

CAPTION = {"type": "object", "required": ["caption", "hashtags"],
           "properties": {"caption": S, "hashtags": {"type": "array", "minItems": 3, "maxItems": 5, "items": S}}}

# ---- autopilot (daily, no human): scout proposes, an independent judge scores, code picks the winner
CATEGORIES = ["news", "myth", "emergency", "how_it_works", "career", "numbers", "cabin", "history", "syllabus", "exam"]
SCOUT_TOPIC = {"type": "object", "required": TOPIC["required"] + ["category", "trend_signal", "viral_score"],
               "properties": {**TOPIC["properties"], "category": {"type": "string", "enum": CATEGORIES},
                              "trend_signal": S, "viral_score": I}}
SCOUT = {"type": "object", "required": ["topics"],
         "properties": {"topics": {"type": "array", "minItems": 4, "maxItems": 6, "items": SCOUT_TOPIC}}}
# every other account: its own content pillars name the category (free text)
SCOUT_BRAND = {"type": "object", "required": ["topics"],
               "properties": {"topics": {"type": "array", "minItems": 4, "maxItems": 6, "items": {
                   **SCOUT_TOPIC, "properties": {**SCOUT_TOPIC["properties"], "category": S}}}}}
CRITERIA = ["hook", "curiosity", "shareability", "saveability", "timeliness", "audience_fit", "visual_fit", "verifiability", "novelty", "account_fit"]
JUDGE = {"type": "object", "required": ["scores", "summary"],
         "properties": {"summary": S, "scores": {"type": "array", "items": {
             "type": "object", "required": ["index", "safe", "note"] + CRITERIA,
             "properties": {"index": I, "safe": B, "note": S, "better_hook": S, "better_title": S,
                            **{c: I for c in CRITERIA}}}}}}      # 0-10, clamped in code

# ---- agent team
FACTSHEET = {"type": "object", "required": ["facts", "angle", "do_not_say"],
             "properties": {"facts": {"type": "array", "minItems": 3, "items": {"type": "object", "required": ["fact", "source_url", "source_title", "confidence"],
                                      "properties": {"fact": S, "source_url": S, "source_title": S, "quote": S, "confidence": {"type": "string", "enum": ["high", "medium"]}}}},
                            "angle": S, "do_not_say": {"type": "array", "items": S}, "corrections": {"type": "array", "items": S}}}
CRITIC_KEYS = ["hook", "open_loop", "clarity", "pacing", "payoff", "shareability", "saveability", "fact_fidelity", "brand_fit", "ending"]
CRITIC = {"type": "object", "required": CRITIC_KEYS + ["total", "pass", "fixes"],
          "properties": {**{k: I for k in CRITIC_KEYS}, "total": I, "pass": B, "fixes": {"type": "array", "items": S}, "better_hook": S, "summary": S}}

STRS = {"type": "array", "items": S}
PROFILE = {"type": "object",
           "required": ["business", "niche", "tone", "language", "services", "usp", "audience", "rules", "market_facts", "competitors", "competitor_gap",
                        "pillars", "hook_patterns", "topic_bank", "visual_style", "hashtags", "lanes", "lane_names", "core_keywords", "strategy", "sources", "end_sub"],
           "properties": {"business": S, "niche": S, "tone": S, "language": {"type": "string", "enum": ["english", "hinglish"]},
                          "services": STRS, "usp": STRS, "audience": STRS, "rules": STRS,
                          "market_facts": {"type": "array", "items": {"type": "object", "required": ["fact", "source"], "properties": {"fact": S, "source": S, "date": S}}},
                          "competitors": {"type": "array", "items": {"type": "object", "required": ["name", "note"], "properties": {"name": S, "note": S}}},
                          "competitor_gap": S,
                          "pillars": {"type": "array", "items": {"type": "object", "required": ["id", "name", "why", "lane"],
                                                                 "properties": {"id": S, "name": S, "why": S, "lane": {"type": "string", "enum": ["core", "trending"]}}}},
                          "hook_patterns": STRS,
                          "topic_bank": {"type": "array", "minItems": 16, "items": {"type": "object", "required": ["title", "hook", "lane", "source"],
                                         "properties": {"title": S, "hook": S, "pillar": S, "lane": {"type": "string", "enum": ["core", "trending"]}, "facts": STRS, "source": S}}},
                          "visual_style": {"type": "object", "required": ["look", "footage_keywords", "avoid", "graphics"],
                                           "properties": {"look": S, "footage_keywords": STRS, "footage_rules": STRS, "avoid": STRS, "mood": S, "graphics": S}},
                          "hashtags": STRS, "lanes": {"type": "object", "required": ["core", "trending"], "properties": {"core": S, "trending": S}},
                          "lane_names": {"type": "object", "required": ["core", "trending"], "properties": {"core": S, "trending": S}},
                          "core_keywords": STRS, "strategy": S, "sources": STRS, "end_sub": {"type": "string", "maxLength": 32}}}

TEAM = ["strategist", "trend_scout", "viral_editor", "fact_checker", "scriptwriter", "hook_critic", "footage_picker", "quality_inspector", "caption_writer", "account_intel", "pronunciation_editor", "voice_inspector"]
LESSON = {"type": "object", "required": ["agent", "lesson", "why"],
          "properties": {"agent": {"type": "string", "enum": TEAM}, "lesson": S, "why": S, "metric": {"type": "string", "enum": ["engagement", "critic", "qc", "success"]}}}
ANALYST = {"type": "object", "required": ["insights", "lessons", "retire"],
           "properties": {"insights": S, "lessons": {"type": "array", "maxItems": 4, "items": LESSON}, "retire": {"type": "array", "items": I}}}
RESOURCES = {"type": "object", "required": ["health", "lessons", "usage_note"],
             "properties": {"health": {"type": "array", "items": {"type": "object", "required": ["agent", "status", "note"],
                                                                  "properties": {"agent": S, "status": {"type": "string", "enum": ["ok", "watch", "bad"]}, "note": S}}},
                            "lessons": {"type": "array", "maxItems": 4, "items": LESSON}, "usage_note": S}}

PRONOUNCE = {"type": "object", "required": ["phrases", "terms"],
             "properties": {"phrases": {"type": "array", "items": {"type": "object", "required": ["group", "index", "tts"],
                                                                  "properties": {"group": S, "index": I, "tts": S}}},
                            "terms": {"type": "array", "items": {"type": "object", "required": ["term", "say"], "properties": {"term": S, "say": S}}}}}
PRONCHECK = {"type": "object", "required": ["parts"],
             "properties": {"parts": {"type": "array", "items": {"type": "object", "required": ["id", "best_take", "ok", "problems"],
                            "properties": {"id": S, "best_take": S, "ok": B, "retry_text": S,
                                           "problems": {"type": "array", "items": {"type": "object", "required": ["intended", "heard"],
                                                        "properties": {"intended": S, "heard": S, "fix_say": S}}}}}}}}
