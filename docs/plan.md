# TalentLens: Implementation Plan

**Version:** 1.0, 8 October 2026
**Based on:** `docs/spec.md` (v1.1)
**Deadline:** submit by **Friday 9 October, 18:00**. Demo on **Saturday 10 October, 10:30**.

The project is built in 11 phases (0 to 10) with Claude Code in VS Code. Every phase ends with an app that works and a commit, so there's always something to submit.

---

## How to run each phase

1. In VS Code, open the `talentlens` folder and start Claude Code.
2. Paste this prompt, changing the phase number:

   ```
   Read CLAUDE.md, docs/spec.md and docs/plan.md. Implement Phase N of the plan, and only Phase N.
   First show me a short plan of what you'll do and wait for my OK.
   When you're done: run the tests, then explain in simple English what you built,
   which files you changed, and exactly how I can check it myself.
   ```

3. Do the **"You check"** steps yourself. If something's wrong, tell Claude Code what you saw.
4. When everything in **"Done when"** is true, ask Claude Code to commit and push with the phase's commit message.
5. Tick the boxes in this file as you go.

If you don't understand something Claude Code built, ask it: *"Explain this like I've never written code. What does it do, and why do we need it?"* You'll need these explanations for the demo.

---

## Schedule

| When | Phase | Gate |
|---|---|---|
| **Thu 14:15 to 15:00** | 0. Setup and model check | |
| **Thu 15:00 to 16:45** | 1. Foundations (documents, guardrails, RAG, scoring, database) | |
| **Thu 16:45 to 18:45** | 2. AI layer, agents and supervisor | **Gate A:** the pipeline works from the command line |
| Thu 18:45 to 19:30 | *Break* | |
| **Thu 19:30 to 21:30** | 3. UI part 1 (shell, design, job setup, candidates, screening) | |
| **Thu 21:30 to 23:30** | 4. UI part 2 (review and decisions, shortlist report, behind the scenes) | **Gate B:** the full flow works in the app |
| **Thu 23:30 to 00:30** | 5. Evaluation and prompt refinement | |
| **Fri 08:30 to 10:00** | 6. Polish and hardening | |
| **Fri 10:00 to 11:30** | 7. Permanent database and deployment | **Gate C:** live link works and data survives a restart |
| **Fri 11:30 to 14:30** | 8. Extras (Excel, email drafts, Ask the CVs, n8n) | |
| **Fri 14:30 to 16:45** | 9. Documentation, final evaluation, evidence of testing | **Gate D:** every submission item exists |
| **Fri 16:45 to 18:00** | 10. Final check and **submit** | |
| Fri evening | Slides (you), rehearsal, backup video | |
| Sat morning | Warm the cache, reset decisions, final checks | |

**Rules if we fall behind**

- Don't start a phase until the previous gate has passed. If a gate is late, finish it first and take the time from Phase 8.
- Drop extras from the end of the list: **8d n8n → 8c Ask the CVs → 8b email drafts → 8a Excel**. Then drop the Ollama evaluation, then extra polish.
- **Never cut:**
  - the three human checkpoints
  - RAG with citations
  - quote verification
  - the tests and evaluation
  - deployment
  - the README
  - submitting by 18:00

---

## Phase 0: Setup and model check
**Thu 14:15 to 15:00 · Commit:** `Phase 0: project setup`

**Goal:** an empty app that runs, the code on GitHub, and confirmation that both AI models work.

- [ ] Create the `talentlens` folder and open it in VS Code. Add:
  - `docs/brainstorm.md`, `docs/spec.md`, `docs/plan.md`
  - `CLAUDE.md` in the root
  - the demo data, unzipped (`data/sample/` and `scripts/generate_sample_data.py`)
- [ ] Check the Python version (`python --version`; 3.11 or 3.12 is fine). Create the virtual environment (`python -m venv .venv`, then `.venv\Scripts\activate`).
  - If PowerShell blocks activation, run this once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
- [ ] Create these files:
  - `requirements.txt` (spec §14; pin exact versions)
  - `.gitignore`
  - `.env.example`
  - `.env` (you paste your Gemini key into it yourself)
- [ ] Create `core/config.py`. It reads `.env` locally and Streamlit secrets when deployed.
- [ ] Create a minimal `app.py`: page title "TalentLens", the tagline, a sidebar placeholder.
- [ ] Create `scripts/check_models.py`. It sends a tiny JSON request to `gemini-3.8-flash`, `gemini-3.7-flash`, `gemini-3.6-flash` and Ollama `qwen2.5:7b`, then prints OK or FAIL, the time taken, and whether the reply was valid JSON.
- [ ] `git init`, make the first commit, create a **public** GitHub repo called `talentlens`, and push.

**Done when**
- `streamlit run app.py` opens the TalentLens page in the browser.
- `python scripts/check_models.py` shows OK for at least `gemini-3.8-flash` and `qwen2.5:7b`.
- The GitHub repo has no `.env` file.

**You check**
- [ ] Open the app.
- [ ] Read the model check output.
- [ ] Open the repo on github.com and confirm `.env` isn't there.

**If Gemini fails:** read the error message. A 404 means the model name is wrong. A 400 about JSON means we need to change how JSON mode is requested. Fix it now, before anything else is built on top.

---

## Phase 1: Foundations
**Thu 15:00 to 16:45 · Commit:** `Phase 1: documents, guardrails, RAG, scoring, database`
**Spec:** §6, §7, §8, §9, §11 · FR-C2, FR-C3, FR-M4 · AC2 to AC7, AC10

**Goal:** all the parts that don't use AI, built and tested.

- [ ] `core/schemas.py`: every data format in spec §6.
- [ ] `core/documents.py`: read PDF, DOCX and TXT into clean text, with a friendly error for empty or scanned files.
- [ ] `core/guardrails.py`, implementing spec §8:
  - redaction and injection quarantine
  - quote verification
  - salary parsing and the band check
  - outside-Mauritius check
  - years of experience and career gaps from role dates
  - missing-information check
  - bias filter
- [ ] `core/rag.py`, implementing spec §7: split the guidelines into clauses (`§4.4`), BM25 search, and format clauses for prompts.
- [ ] `core/scoring.py`, implementing spec §9: the rubric as named constants, the score, the band and must-have gaps.
- [ ] `core/memory.py`, implementing spec §11 (v1.1):
  - SQLAlchemy, using `DATABASE_URL` (default: SQLite file `data/talentlens.db`)
  - tables are created automatically
  - functions for preferences, jobs, documents, runs, decisions (a rejection needs a reason), reports, LLM cache and the audit log
- [ ] Tests: `test_documents.py`, `test_guardrails.py`, `test_rag.py`, `test_scoring.py`, `test_memory.py` (spec §15.1).
- [ ] `scripts/intake_preview.py`: for each sample CV, prints the word count, what was redacted and what was quarantined.

**Done when**
- `pytest -v` passes.
- The intake preview shows Jean-Marc with 3 redactions, Ryan with 1 quarantined line, and nothing for the other 5 CVs.

**You check**
- [ ] Run `pytest -v` and see everything pass.
- [ ] Run the intake preview and read Ryan's quarantined line.
- [ ] Ask Claude Code to explain each `core` file in two sentences. Save those explanations; they become `docs/how_it_works.md` later.

---

## Phase 2: AI layer, agents and supervisor
**Thu 16:45 to 18:45 · Commit:** `Phase 2: LLM client, agents, supervisor`
**Spec:** §5, §10 · FR-S1, S3, S4, S5, FR-L4, L5 · AC1 to AC7

**Goal:** the whole screening pipeline works from the command line on the sample CVs.

- [ ] `core/llm.py`, implementing spec §10:
  - Gemini and Ollama
  - JSON extraction, Pydantic validation and up to 2 repairs
  - call spacing, retry with backoff, and the Gemini model fallback chain
  - cache stored in the database (`llm_cache`)
  - token, time and cache metadata
  - plain-English error messages
- [ ] `core/prompts.py`: the five prompts, each with a version (`job_analyst@v1` and so on), following spec §5 and the shared rules in §5.1.
- [ ] `core/agents.py`:
  - Job Analyst
  - CV Analyst (plus the code fixes: email/phone fallback, years recalculated from dates)
  - Comparison Agent (including revision mode)
  - Guardrail Reviewer
  - Report Agent (candidate mode and shortlist mode, plus the bias filter and required questions)
- [ ] `core/supervisor.py`:
  - `run_screening` and `screen_new_cvs`
  - the revise loop (once at most)
  - errors isolated per candidate
  - ranking
  - saving after each candidate
  - trace events through an `on_event` callback
- [ ] Tests: `tests/fake_llm.py`, `test_llm.py`, `test_supervisor.py`. These include the important checks that **Ryan's hidden text and Jean-Marc's protected details never appear in any prompt**.
- [ ] `scripts/run_cli.py`: runs the full pipeline (sample job + 6 CVs) on Gemini and prints a ranking table with bands, missing information and flags.

**Done when**
- The tests pass.
- The command-line run finishes all 6 CVs on Gemini.
- Sarah, Kevin and Aisha rank high, Ryan is "Not a match" with an injection flag, and Jean-Marc has the salary, gap and redaction flags.
- Running it a second time finishes in seconds, because of the cache.

**You check**
- [ ] Run `python scripts/run_cli.py` and compare the output with `ground_truth.json`.
- [ ] Write down anything odd. It becomes your first refinement log entry.

**⛳ Gate A:** if the command-line pipeline doesn't work, don't start the UI. Fix this first.

---

## Phase 3: UI part 1 (shell, design, job setup, candidates, screening)
**Thu 19:30 to 21:30 · Commit:** `Phase 3: app shell, job setup, candidates, screening`
**Spec:** §12 · FR-J1 to J7, C1 to C5, S1 to S6, M1, M2, M5, L1 to L3, L5

**Goal:** a recruiter can go from the job description to screened candidates in the app.

- [ ] Design:
  - `.streamlit/config.toml` (theme)
  - `ui/styles.css` (tokens from spec §12.3)
  - `ui/components.py`: candidate card, band pill, status chips (✔ ◐ ✖), clause chip, score ring, stat tile, timeline step chips
- [ ] `app.py` sidebar:
  - logo and tagline
  - step navigation with ✓ next to finished steps
  - model settings, **Test connection**, cache switch
  - reviewer name
  - recruiter preferences (memory)
  - **New screening**
- [ ] `ui/page_job_setup.py`:
  - load the sample, an upload or pasted text
  - **Extract requirements**
  - an editable checklist table and salary band
  - preference notes
  - **Approve requirements**, which shows an "Approved by … at …" chip
- [ ] `ui/page_candidates.py`:
  - upload or **Use sample CVs**
  - intake badges and a "What the AI will see" preview
  - remove a CV
- [ ] `ui/page_screening.py`:
  - **Run screening** and **Screen new CVs**
  - the live timeline, updated through `on_event`
  - statistics tiles
  - error rows with **Retry**
- [ ] On start-up, load the latest run from the database (FR-M2).

**Done when**
- From a fresh start you can, in the app:
  - load the sample job
  - extract, edit and approve the requirements
  - load the sample CVs (Jean-Marc and Ryan badges visible)
  - run the screening with the live timeline
- After closing and restarting the app, everything is still there.

**You check**
- [ ] Do that whole flow.
- [ ] Restart the app (Ctrl+C in the terminal, then `streamlit run app.py`).
- [ ] Zoom the browser to 125% and check the app is still easy to read.

---

## Phase 4: UI part 2 (review, decisions, report, behind the scenes)
**Thu 21:30 to 23:30 · Commit:** `Phase 4: review, decisions, shortlist report, behind the scenes`
**Spec:** §12 · FR-R1 to R6, D1 to D5, P1 to P5, O1 to O5 · AC8, AC9, AC14

**Goal:** the full recruiter journey works from start to finish.

- [ ] `ui/page_review.py`:
  - summary tiles and ranked cards
  - the comparison matrix
  - candidate detail in the brief's format (Candidate, Relevant Experience, Key Skills, Missing Information, Questions for Interview)
  - the evidence table with clause chips and verified badges
  - score breakdown, flags and intake notes
  - the decision panel (a rejection needs a reason)
  - **Reset decisions**
  - "AI recommendation, not a decision" shown on every card and the detail view
- [ ] `core/report.py` and `ui/page_report.py`:
  - counts by decision
  - **Draft report**: an AI overview plus code-built sections, shortlisted candidates only
  - a Markdown editor with live preview
  - **Approve report**
  - .md and .html downloads, with the required footer line
- [ ] `ui/page_behind_the_scenes.py`, with tabs: Agent trace, Per-agent summary, Prompts, Evaluation (reads `eval/results/`), Audit log.
- [ ] `tests/test_report.py`.

**Done when**
- The whole journey works in the app: decisions → report → download.
- Uploading Nadia's CV and clicking **Screen new CVs** adds her to the ranking.
- A rejection without a reason is blocked.
- The report contains only shortlisted candidates.

**You check**
- [ ] Follow the demo script (spec §17.2) once from start to finish, timing it.
- [ ] Try to reject Ryan without a reason.
- [ ] Open the downloaded HTML report in your browser.

**⛳ Gate B:** the full flow works in the app. If it doesn't by 23:30, finish it first thing on Friday and drop one extra for every hour lost.

---

## Phase 5: Evaluation and prompt refinement
**Thu 23:30 to 00:30 · Commit:** `Phase 5: evaluation harness and prompt refinements`
**Spec:** §15.2, §15.3 · AC11

**Goal:** proof that the results are correct, plus a real "testing and refinement" story.

- [ ] `eval/run_eval.py`:
  - flags: `--provider gemini|ollama`, `--model`, `--limit N`
  - uses the latest approved requirements for the sample job from the database (or `data/sample/approved_requirements.json` if present)
  - compares the results with `ground_truth.json` (spec §15.2)
  - writes `eval/results/eval_{provider}_{timestamp}.json` and a Markdown summary table
- [ ] Run it on Gemini. For each failure: find out why, change the prompt (and bump its version, e.g. `comparison@v2`), and write an entry in `docs/refinement_log.md`. Re-run.
- [ ] Leave the cache warm with the final prompts.

**Done when**
- The evaluation passes for all 7 CVs on Gemini, or any remaining failure is explained in the refinement log.
- The refinement log has at least 2 real entries.

**You check**
- [ ] Read the evaluation table.
- [ ] Pick one refinement story you can tell in the presentation ("At first the agent did X; we changed Y; now Z").

---

## Phase 6: Polish and hardening
**Fri 08:30 to 10:00 · Commit:** `Phase 6: polish and hardening`
**Spec:** §12.3, §13 · FR-L5 · AC12

**Goal:** it looks professional and nothing breaks in front of an audience.

- [ ] A visual pass on every page:
  - spacing and alignment
  - empty states that tell the user what to do
  - disabled buttons that say why ("Approve the requirements first")
  - toasts when something is saved
  - gentle fade-in on cards
  - a simple TalentLens logo (SVG) in the sidebar
- [ ] Error tests, checking each one shows a friendly message, never a raw Python error:
  - wrong API key
  - Ollama stopped
  - simulated rate limit
  - empty PDF
- [ ] Backup check: switch to Ollama and screen Nadia only.
- [ ] Offline check: turn Wi-Fi off and open the existing run (the cache and database still work).
- [ ] Fix every bug found.

**Done when**
- You can do the demo flow twice in a row without problems.
- No raw error appears anywhere.

**You check**
- [ ] Run the demo flow twice, once at 125% zoom.
- [ ] Do the Wi-Fi-off check yourself.

---

## Phase 7: Permanent database and deployment
**Fri 10:00 to 11:30 · Commit:** `Phase 7: Postgres support and Streamlit Cloud deployment`
**Spec:** FR-X3 (v1.1), §11, §14

**Goal:** a live public link with an access code, where data survives restarts.

- [ ] Create a free **Neon** account and project (no card needed), and copy its Postgres connection string.
- [ ] `core/memory.py`:
  - when `DATABASE_URL` starts with `postgresql://`, use Postgres (with the `psycopg` driver and `pool_pre_ping`, because Neon's database sleeps after 5 minutes idle)
  - otherwise use SQLite
- [ ] Test locally against Neon: put `DATABASE_URL` in `.env`, run the app, and check the tables appear in Neon's table view.
- [ ] Access code gate: when `APP_ACCESS_CODE` is set, the app asks for it before showing anything.
- [ ] When deployed (`DEPLOYED=true`), hide Ollama.
- [ ] Recommended: create a **second Gemini API key in a separate AI Studio project** for the deployed app, so visitors can't use up your demo quota.
- [ ] On share.streamlit.io:
  1. Create app → repo `talentlens`, branch `main`, file `app.py`.
  2. Advanced settings: Python 3.12.
  3. Secrets: `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_FALLBACK_MODELS`, `DATABASE_URL`, `APP_ACCESS_CODE`, `REVIEWER_NAME`, `DEPLOYED="true"`.
  4. Deploy.
- [ ] Run the sample screening once on the live app, so whoever opens it sees results straight away.

**Done when**
- The link works on your phone and in a private browser window.
- The access code is required.
- After **Reboot app** from the Streamlit dashboard, the run and decisions are still there.

**You check**
- [ ] Open the link on your phone.
- [ ] Reboot the app and check the data is still there.

**⛳ Gate C:** the live link works and data survives a restart.

---

## Phase 8: Extras
**Fri 11:30 to 14:30 · One commit per extra**

Build these in order. **At 14:30, stop**, whatever is finished. Each extra must leave the app working.

### 8a. Excel export (FR-X7), about 30 minutes
- [ ] A **Download Excel** button on the Review page, using `openpyxl`. The file has three sheets:
  - **Ranking:** name, score, band, must%, nice%, missing information, flags, decision
  - **Comparison:** candidates against requirements, with coloured met, partial and missing cells
  - **Evidence:** candidate, requirement, status, quote, verified
- [ ] Bold headers, sensible column widths, frozen top row.
- **Done when:** the file opens in Excel with all three sheets coloured.

### 8b. Missing-information email drafts (FR-X6), about 45 minutes
- [ ] On the candidate detail view, when information is missing: **Draft email** asks the Report Agent (email mode, prompt `email@v1`) for a polite subject and body asking for exactly the missing items.
- [ ] The recruiter can edit it and then **Copy**, or **Approve** it (approval is recorded in the audit log). It is **never sent automatically**.
- **Done when:** Kevin's draft asks for his salary expectation, notice period and right to work, and nothing else.

### 8c. Ask the CVs (FR-X1), about 75 minutes
- [ ] A new page, **Ask the CVs**, with a chat interface.
- [ ] A tool-calling agent (spec §5.8):
  - the model replies with JSON `call_tool` or `answer`
  - tools: `search_cvs`, `get_candidate`, `list_candidates`
  - at most 4 tool calls
  - quoted citations are verified against the CVs
- [ ] Show each tool call in an expander ("🔧 search_cvs('TikTok')").
- [ ] Test with: "Who has TikTok experience?", "Who manages an ads budget above MUR 100,000?", "Which candidates are missing their notice period?"
- **Done when:** these questions get correct answers with verified quotes, and an unanswerable question gets "I couldn't find that in the CVs".

### 8d. n8n email on approval (FR-X2), about 45 minutes, local only
- [ ] In n8n:
  - create the workflow: **Webhook (POST)** → **Send email** (Gmail or SMTP) to the hiring manager's address (use your own for the demo), with the subject "Shortlist: Marketing Executive" and the report as the body
  - activate the workflow and copy its **production** webhook URL
  - export the workflow JSON to `n8n/shortlist_email_workflow.json`
- [ ] In the app: when `N8N_WEBHOOK_URL` is set, an approved report shows **Send to hiring manager (n8n)**. It posts the report as JSON and shows the response.
- **Done when:** clicking the button delivers the email to your inbox.

---

## Phase 9: Documentation, final evaluation, evidence
**Fri 14:30 to 16:45 · Commit:** `Phase 9: documentation and testing evidence`
**Spec:** §15.4, §18

**Goal:** every submission item exists and a stranger could understand and run the project.

- [ ] Final evaluation:
  - Gemini on all 7 CVs
  - Ollama on at least 3 CVs (`--limit 3`), since it's slower
  - a comparison table: accuracy, time and tokens per CV
- [ ] Save `pytest -v` to `docs/test_results.txt`, and take screenshots of it and of the evaluation table.
- [ ] `scripts/export_prompts.py` → `docs/prompts.md` (the key prompts, with versions).
- [ ] `README.md`:
  - title, your name (blank for you to fill), one-paragraph description
  - screenshots, live link (the access code goes in the submission, not the README)
  - features, a short architecture section
  - a table of the AI concepts used and where to find them
  - how to run it locally, how to run tests and the evaluation
  - limitations and responsible AI notes
- [ ] `docs/architecture.md`: the diagram, the components, the data flow, and the key decisions with their reasons.
- [ ] `docs/testing.md`: the test list, results, evaluation tables and links to the refinement log.
- [ ] `docs/how_it_works.md`: a plain-English walkthrough, to help you explain the project.
- [ ] `docs/demo_script.md`: the timed demo (spec §17.2, updated for the extras), a **slide content outline** for your 4 to 5 slides, and **likely questions with short answers**.
- [ ] If there's time: FR-X5, the guardrail experiment (`--no-guardrails` on Ryan and Jean-Marc), for the learnings slide.

**Done when**
- Every item in the submission checklist (Phase 10) exists.
- The README reads well on GitHub.

**⛳ Gate D:** all submission items exist.

---

## Phase 10: Final check and submit
**Fri 16:45 to 18:00 · Commit:** `Phase 10: release v1.0` (also add a git tag `v1.0`)

- [ ] **Fresh-clone test:** clone the repo into a new folder, create a new virtual environment, install, add a `.env`, and run. This catches forgotten files.
- [ ] **Secrets check:** search the repo for `AIza` (how Google API keys start) and for `postgresql://`. Neither should appear anywhere except in `.env.example` placeholders.
- [ ] Open the live link and enter the access code. The sample run should be visible.
- [ ] Make a zip of the repo as a backup (no `.venv`, `.env` or database file).
- [ ] **Submit on Google Classroom:**
  - **Project title:** TalentLens: AI Recruitment Assistant
  - **Student name:** *(yours)*
  - **Short description:** 2 or 3 sentences from the README
  - **GitHub link** (and the zip)
  - **Live link and access code**
  - **Architecture:** link to `docs/architecture.md`
  - **Key prompts:** link to `docs/prompts.md`
  - **Evidence of testing:** links to `docs/testing.md`, `docs/test_results.txt` and the refinement log, plus screenshots

**Done when:** submitted before 18:00 ✅

---

## After submitting (Friday evening to Saturday 10:30)

- [ ] Make your 4 to 5 slides from the outline in `docs/demo_script.md`: problem, solution, architecture, AI concepts, then learnings and challenges after the demo.
- [ ] Rehearse the full 10 minutes twice with a timer.
- [ ] Record a backup video of the demo, for example with Xbox Game Bar (`Win + Alt + R`) recording the browser.
- [ ] **Saturday morning:**
  - run the sample screening once (warms the cache) and click **Reset decisions**
  - **Test connection**; make sure Ollama is running
  - open the live link once to wake it up
  - put Nadia's CV on the desktop
  - set browser zoom to 125%, turn off notifications, charge the laptop

---

## Build risks

| Risk | What to do |
|---|---|
| Gemini 3.x behaves unexpectedly (JSON, speed) | Caught in Phase 0 by `check_models.py`. The fallback models are already configured |
| The free Gemini quota runs out while building | Keep the cache on. Use the fake model in tests. Run the evaluation only when needed. Use the separate key for the deployed app |
| PowerShell won't activate the virtual environment | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` (once) |
| Clicking around during screening interrupts it (that's how Streamlit works) | The Run button disables the page while it runs. Don't click elsewhere during a live run |
| The Neon database is asleep (first request is slow) | `pool_pre_ping`. Open the live link a minute before the demo |
| Claude Code builds more than the current phase | The prompt says "only Phase N". Ask it to undo anything outside the phase |
| You can't explain something | Ask Claude Code for a plain-English explanation and add it to `docs/how_it_works.md` |
