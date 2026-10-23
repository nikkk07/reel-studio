"""JSON schemas for every Claude decision (validated by Claude Code via --json-schema)."""

S = {"type": "string"}
N = {"type": "number"}
I = {"type": "integer"}
B = {"type": "boolean"}
SRC = {"type": "object", "properties": {"title": S, "url": S}, "required": ["title", "url"]}

TOPIC = {"type": "object", "required": ["title", "hook", "angle", "why_viral", "key_facts", "sources"],
         "properties": {"title": S, "hook": S, "angle": S, "why_viral": S, "key_facts": {"type": "array", "items": S},
                        "sources": {"type": "array", "items": SRC}, "risk_note": S, "est_seconds": I}}

SUGGEST = {"type": "object", "required": ["topics"],
           "properties": {"topics": {"type": "array", "minItems": 5, "maxItems": 5, "items": TOPIC}}}

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
CATEGORIES = ["news", "myth", "emergency", "how_it_works", "career", "numbers", "cabin", "history"]
SCOUT_TOPIC = {"type": "object", "required": TOPIC["required"] + ["category", "trend_signal", "viral_score"],
               "properties": {**TOPIC["properties"], "category": {"type": "string", "enum": CATEGORIES},
                              "trend_signal": S, "viral_score": I}}
SCOUT = {"type": "object", "required": ["topics"],
         "properties": {"topics": {"type": "array", "minItems": 4, "maxItems": 6, "items": SCOUT_TOPIC}}}
CRITERIA = ["hook", "curiosity", "shareability", "saveability", "timeliness", "audience_fit", "visual_fit", "verifiability", "novelty", "account_fit"]
JUDGE = {"type": "object", "required": ["scores", "summary"],
         "properties": {"summary": S, "scores": {"type": "array", "items": {
             "type": "object", "required": ["index", "safe", "note"] + CRITERIA,
             "properties": {"index": I, "safe": B, "note": S, "better_hook": S, "better_title": S,
                            **{c: I for c in CRITERIA}}}}}}      # 0-10, clamped in code
