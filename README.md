# TalentLens: AI Recruitment Assistant

**AI does the reading. You make the call.**

**Student:** _(your name)_ · Capstone project, Agentic AI Bootcamp

TalentLens helps a recruiter screen CVs against a job's requirements and the company's hiring guidelines. Specialist AI agents extract each candidate's experience, compare it with every requirement using word-for-word quotes, and draft interview questions. Code verifies every quote, calculates the scores and enforces fairness rules, and the recruiter makes every decision at three checkpoints. Protected details are redacted and hidden instructions removed before any text reaches the AI.

**Live app:** _(Streamlit Cloud link)_ · the access code is in the submission.

<!-- Screenshots: save docs/screenshots/review.png and docs/screenshots/trace.png, then delete this comment's
     opening and closing lines so the table below shows.
| Review: ranking, matrix and evidence | Behind the scenes: agent trace |
|---|---|
| ![Review page](docs/screenshots/review.png) | ![Trace](docs/screenshots/trace.png) |
-->

**Evaluation:** 7 of 7 demo CVs correct on Gemini and on a local Ollama model ([details](docs/testing.md)). **Tests:** 201 passing.

## Features

- **Job setup:** the Job Analyst agent drafts a requirements checklist from the job description, using the relevant guideline clauses and saved recruiter preferences. The recruiter edits and approves it (checkpoint 1).
- **Safe intake:** PDF, DOCX and TXT CVs. Protected details (date of birth, marital status, nationality…) are redacted, and lines that try to instruct the AI are quarantined, before any AI call. "What the AI will see" shows the exact text.
- **Multi-agent screening** with a live timeline: CV Analyst → Comparison → Guardrail Reviewer (with one self-correction loop) → Report Agent.
- **Evidence you can check:** every met or partial requirement has a quote verified against the CV, plus the guideline clauses it relied on (`§4.4`, click to read).
- **Code-calculated scores and bands** (70% must-haves, 30% nice-to-haves), flags for salary above band, outside Mauritius, career gaps, missing information and prompt injection.
- **Human decisions:** Shortlist, Hold or Reject; a rejection needs a written reason (checkpoint 2). Everything goes in an audit log.
- **Shortlist report:** an AI overview plus code-built sections for shortlisted candidates only; edit, approve (checkpoint 3), download as Markdown or HTML.
- **Behind the scenes:** the full agent trace (time, tokens, cache hits, retrieved clauses), per-agent summary, every prompt with its version, evaluation results and the audit log.
- **Extras:** Excel export, missing-information email drafts (never sent automatically), **Ask the CVs** (a tool-calling assistant with verified quotes), and an n8n workflow that emails the approved report.
- **Works with Gemini or a local Ollama model**, with caching, retries, model fallback and friendly error messages. SQLite locally, Postgres (Neon) in the cloud.

## Architecture

```
Recruiter (Streamlit) ─► Job Analyst ◄─ RAG (guideline clauses) ─► [1. approve requirements]
                       ─► Intake (code): read → redact → quarantine injections
                       ─► Supervisor (code), per CV:
                            CV Analyst → Comparison ⇄ Guardrail Reviewer (verify quotes, revise once)
                            → Scoring (code) → Report Agent → bias filter (code) → save
                       ─► [2. Shortlist / Hold / Reject + reason] ─► [3. edit + approve report]
LLM layer: Gemini 3.8 → 3.7 → 3.6 Flash, or Ollama qwen2.5:7b · JSON + Pydantic + repair · cache · trace
```

Details, data flow and key decisions: [`docs/architecture.md`](docs/architecture.md).

## AI concepts used

| Concept | Where |
|---|---|
| Multi-agent workflow with a supervisor | `core/agents.py`, `core/supervisor.py` |
| RAG with citations (BM25 over guideline clauses) | `core/rag.py`, clause chips on the Review page |
| Structured output (JSON → Pydantic, with a repair loop) | `core/llm.py`, `core/schemas.py` |
| Guardrails before and after the AI | `core/guardrails.py` |
| Self-correction (revise loop when quotes aren't found) | `core/supervisor.py` |
| Tool calling (Ask the CVs) | `core/assistant.py`, `ui/page_ask.py` |
| Memory (preferences, jobs, runs, decisions, cache) | `core/memory.py` |
| Human in the loop (three checkpoints) | pages 1, 4 and 5 |
| Prompt versioning and refinement | `core/prompts.py`, [`docs/prompts.md`](docs/prompts.md), [`docs/refinement_log.md`](docs/refinement_log.md) |
| Evaluation against ground truth | `eval/run_eval.py`, [`docs/testing.md`](docs/testing.md) |
| Observability (trace, tokens, cache) | page 6, Behind the scenes |

## Run it locally (Windows PowerShell)

Needs Python 3.11 or 3.12, and either a free Gemini API key or Ollama (see below).

```
git clone https://github.com/alishaallybukus/talentlens.git
cd talentlens
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # then add your GEMINI_API_KEY (from aistudio.google.com)
streamlit run app.py
```

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

### Run it with Ollama (local model, no API key needed)

1. Install [Ollama](https://ollama.com/download), open it, then in PowerShell run `ollama pull qwen2.5:7b` (about 4.7 GB, once).
2. In `.env`, set `LLM_PROVIDER=ollama` (leave `GEMINI_API_KEY` empty).
3. `streamlit run app.py`, then check **⚙ Settings** shows *Ollama (local)* and click **Test connection**.

Expect about 40 seconds per CV on a laptop CPU. The online demo uses Gemini, because a cloud server can't reach a model running on someone's laptop.

### Using the app

**Job setup** → load the sample job → **Extract requirements** → **Approve requirements**. **Candidates** → **Use sample CVs**. **Screening** → **Run screening**. Then **Review** (decide for each candidate), **Report** (draft, approve, download) and **Behind the scenes**.

Other commands:

```
python scripts/check_models.py              # are the AI models reachable?
python scripts/run_cli.py --provider ollama # the whole pipeline from the command line
python scripts/export_prompts.py            # regenerate docs/prompts.md
```

## Tests and evaluation

```
pytest -v                                   # 200+ tests, offline, with a fake model
python -m eval.run_eval --provider gemini   # the real model on 7 CVs vs ground_truth.json
python -m eval.run_eval --provider ollama --limit 3
```

Results, the evaluation tables and how the prompts were refined: [`docs/testing.md`](docs/testing.md).

## Configuration

All settings are in `.env` (locally) or Streamlit secrets (deployed); see `.env.example`. `DATABASE_URL` empty means SQLite at `data/talentlens.db`; a `postgresql://` URL uses Postgres (Neon). `APP_ACCESS_CODE` protects the public app. `DEPLOYED=true` hides Ollama.

## Responsible AI and limitations

- **The AI never decides.** It recommends a band, labelled "AI recommendation, not a decision". Only the recruiter's recorded decision shortlists or rejects, and a rejection needs a job-related reason.
- **Fairness:** protected details are removed before the AI sees a CV; prompts forbid using protected characteristics; a bias filter removes any summary sentence or question that mentions them; career gaps become neutral questions, never a weakness. "Not a match" is shown in grey, not red.
- **Security:** CV text is treated as data, never instructions; suspected prompt-injection lines are quarantined and shown to the recruiter. API keys live only in `.env` or secrets and are never logged.
- **Limitations:** redaction and injection detection are pattern-based and can miss unusual wording, so human review remains essential. Scanned (image-only) CVs aren't read. The project was built and tested on fictional data for a single job, so results on real CVs need their own evaluation. A small local model is slower and less accurate than Gemini; for example, Ask the CVs on qwen2.5:7b sometimes names an extra candidate, which is why every answer shows its tool calls and verified quotes.
- **Data:** all sample CVs and the company are fictional.

## Documentation

[Architecture](docs/architecture.md) · [Key prompts](docs/prompts.md) · [Testing](docs/testing.md) · [Refinement log](docs/refinement_log.md) · [How it works](docs/how_it_works.md) · [Demo script](docs/demo_script.md) · [Specification](docs/spec.md) · [Plan](docs/plan.md)
