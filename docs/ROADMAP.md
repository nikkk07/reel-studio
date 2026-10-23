# Roadmap and self-review

## Phases

| Phase | Deliverable | Done when |
|---|---|---|
| P0 Accounts (owner) | Supabase project, Claude Code logged in on the Mac, GitHub repo, Cloudflare Pages | `setup_mac.sh` self-test all PASS (keys in `~/ReelStudio/.env`, Claude Code signed in) |
| P1 Database | `supabase/migrations/0001_init.sql` | migration applies cleanly; RLS denies anonymous access |
| P2 Worker core | heartbeat, ping, lease queue, two lanes, checkpoints, launchd | kill -9 during a stage → job resumes at that stage |
| P3 Brain | `claude -p` adapter, 5 JSON schemas, prompts | schema-valid output for suggest / optimize / script / footage / QC |
| P4 Media engine | spec compositor + component library, voice, align, mix, render, QC | sample spec renders a Reel identical in quality to the hand-made ones |
| P5 Web app | Connect, Topic, Production, Review, Library | full flow works against the live DB |
| P6 E2E | mock-brain test in CI, then real run on the Mac | one Reel produced from the website end to end |
| P7 Go-live | deploy, invite members, alerts | first scheduled/production Reel approved |
| Later | daily scheduled suggestions, direct Instagram posting after approval, more graphics components | |

## Self-review (what changed after re-evaluating v0)

1. **Static SPA instead of Next.js server functions** – nothing secret in the cloud, free hosting anywhere, one less
   moving part. All logic lives in the DB (RLS + RPC) and on the Mac.
2. **Outbound-only worker + lease queue** instead of exposing the Mac (tunnel/port) – survives restarts, Wi-Fi
   changes and power cuts; no security surface on the office network.
3. **Ping round-trip** for "Connect to Server" instead of trusting the last heartbeat – proves the Mac is alive now.
4. **Spec-driven compositor** instead of Claude writing new render code each time – Claude spends tokens on
   decisions (story, shots, graphics plan), rendering is deterministic and testable, 3 Claude calls per Reel.
5. **Two lanes** – suggestions and prompt optimisation stay instant even while a Reel renders for 10+ minutes.
6. **Free-tier fit** – 50 MB upload limit → ≤ 45 MB delivery encode; 1 GB storage → 30-day prune; archive on Mac.
7. **Claude usage guard** – Pro limits are shared with chat; daily cap, `waiting_quota` state, auto-resume.
8. **Least-privilege Claude** – read-only / web-only tools per step; no shell, so injected web text cannot act.
9. **Quality gates are automatic and block publishing** – the same checks used by hand today (key-word clarity,
   UTMOS, loudness/true peak, frame review) plus one automatic fix round.
10. **Pronunciation dictionary is shared state** – approved spellings live in one JSON on the Mac and every job
    uses it; unknown English words are flagged in the review screen.

## Known risks and how they are handled

| Risk | Mitigation |
|---|---|
| Chatterbox on MPS unstable | automatic CPU fallback (M4 CPU is still several times the cloud box) |
| Pro usage limit reached mid-job | `waiting_quota`, resume after reset, daily cap |
| Supabase free project paused | heartbeat traffic; Connect screen shows a clear error if the API is unreachable |
| Stock footage mismatch | Claude picks from contact sheets; QC review can request a re-pick once |
| New mispronounced word | flagged in review; one click adds a respelling to the dictionary (later phase) |
