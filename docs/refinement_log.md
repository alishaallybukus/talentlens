# Refinement log

Every prompt or design change made after testing, with what we saw and what changed (spec §15.3).
Prompt versions live in `core/prompts.py`.

| Date | What we saw | Change (prompt version) | Result |
|---|---|---|---|
| 8 Oct 2026 | Ollama run (Aisha, Jean-Marc): asked to fix only N2's quote, the revision rewrote every result and dropped good quotes, so Jean-Marc fell to 0 | Design (code, prompts unchanged): `merge_revision` accepts revised answers only for the failing ids and keeps every result that already passed | Test added; a bad revision can no longer damage verified results |
| 8 Oct 2026 | `gemini-3.6-flash` hung for 49 minutes during a 503 overload, despite a 120 s timeout | Design (code): `post_with_deadline` enforces a hard total deadline on every model call | Test added; a stuck call now gives up on time |
| 9 Oct 2026 | The free daily Gemini quota ran out on all 3 models. During Google's 503 overload, every retry counted as a request | Design (code): a 429 that names a per-day quota now skips retries and moves to the next model; when every model is out, a clear message says when it resets | Tests added; no requests wasted once the daily limit is reached |
| 9 Oct 2026 | Ollama run for the shortlist report: Kevin's CV has no gap, but the CV Analyst invented a "Career break" role (Apr 2022 to Sep 2020, impossible dates). Code couldn't read the dates, so it kept the AI's gap and Kevin got a career-gap interview question | Design (code, prompts unchanged): `fix_profile` keeps a career break only if the CV text mentions one, and drops the AI's gaps when the dates can't be checked | 3 tests added; Kevin has no gap and Jean-Marc's real break is still found |
| 8 Oct 2026 | Starting point | First versions: `job_analyst@v1`, `cv_analyst@v1`, `comparison@v1`, `report_candidate@v1`, `report_shortlist@v1` | Baseline for the first command-line run |
