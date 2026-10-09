# TalentLens: architecture

TalentLens screens CVs against a job's requirements and the company's hiring guidelines. Specialist AI agents read and compare; code verifies, scores and enforces the rules; the recruiter makes every decision at three checkpoints.

## Diagram

```
 Recruiter (Streamlit UI)
   │
   ├─ 1. Job setup ──► Job Analyst Agent ◄── RAG: guideline clauses (BM25)
   │                        │             ◄── Memory: recruiter preferences
   │                        ▼
   │              [Checkpoint 1: edit + approve requirements] ──► database
   │
   ├─ 2. CVs ──► Intake (code): read PDF/DOCX/TXT → redact protected details → quarantine injection lines
   │
   ├─ 3. Run ──► Supervisor (code), for each CV:
   │               CV Analyst Agent ──► code fixes (contact details, labelled fields, years and gaps from dates)
   │                     │
   │                     ▼
   │               Comparison Agent ◄── RAG: guideline clauses
   │                     │      ▲
   │                     ▼      │ revise once (quotes not found in the CV)
   │               Guardrail Reviewer (code): verify quotes, minimum years, flags
   │                     │
   │                     ▼
   │               Scoring (code) ──► Report Agent ◄── RAG ──► bias filter (code)
   │                                                            │
   │                                                     save result ──► database
   │
   ├─ 4. Review ──► [Checkpoint 2: Shortlist / Hold / Reject + written reason] ──► database + audit log
   │                 extras: Excel export · missing-information email drafts · Ask the CVs (tool calling)
   │
   └─ 5. Report ──► Report Agent (overview) + code (one section per shortlisted candidate)
                         │
                         ▼
                  [Checkpoint 3: edit + approve] ──► download .md / .html  (extra: n8n email)

 LLM layer: Gemini (3.8-flash → 3.7-flash → 3.6-flash) or Ollama (qwen2.5:7b, local)
            JSON output → Pydantic validation → repair (up to 2x) · cache · spacing · retries · fallback · trace
 Database:  SQLAlchemy → SQLite locally, Neon Postgres in the cloud (DATABASE_URL)
```

## Components

| Module | What it does |
|---|---|
| `app.py` | Streamlit entry point: access code, sidebar, navigation |
| `ui/` | One file per page, plus `components.py` (chips, score ring, tables, logo) and `styles.css` |
| `core/config.py` | Settings from `.env` locally, or Streamlit secrets when deployed |
| `core/schemas.py` | Every AI output and data format as a Pydantic model |
| `core/llm.py` | One client for Gemini and Ollama: JSON mode, validation, repair, spacing, retries, model fallback, cache, token counts, friendly errors |
| `core/prompts.py` | Every prompt, each with a version string |
| `core/documents.py` | Reads PDF, DOCX and TXT into clean text; refuses empty or scanned files kindly |
| `core/guardrails.py` | Redaction, injection quarantine, quote verification, salary, location, years, gaps, missing information, bias filter |
| `core/rag.py` | Splits the guidelines into numbered clauses (`§4.4`) and searches them with BM25 |
| `core/scoring.py` | The rubric: statuses → score → band. Pure code |
| `core/agents.py` | The AI agents and the Guardrail Reviewer's code checks |
| `core/supervisor.py` | Runs the workflow per CV, the revise loop, error isolation, ranking, saving, trace events |
| `core/report.py` | Builds the shortlist report (Markdown and HTML) from shortlisted candidates only |
| `core/excel.py`, `core/assistant.py` | Extras: Excel export; the Ask-the-CVs tool-calling assistant |
| `core/memory.py` | The database: preferences, jobs, documents, runs, decisions, reports, audit log, LLM cache |
| `eval/run_eval.py` | Runs the real pipeline on the 7 demo CVs and compares with `ground_truth.json` |
| `tests/` | 200+ pytest tests with a fake model (no internet, no API calls) |

## Data flow for one CV

1. **Intake (code, before any AI):** the file becomes text. Protected details (date of birth, marital status, nationality and so on) are replaced by `[REDACTED]`; only the labels are kept. Lines that look like instructions to an AI ("ignore previous instructions…") are removed and kept aside for the recruiter to see. Only this cleaned text is stored and sent to a model.
2. **CV Analyst:** extracts facts (roles, dates, skills, salary, notice period…). Code then fills gaps from the CV itself (email, phone, labelled lines like "Availability: 2 weeks") and recalculates years and career gaps from the dates.
3. **Comparison:** for each requirement, met / partial / missing with a word-for-word quote and cited guideline clauses, retrieved by RAG.
4. **Guardrail Reviewer (code):** every met or partial quote must be found in the CV. If some aren't, the Comparison Agent gets one chance to fix them; anything still unverified becomes missing and is flagged. Minimum years are enforced from the dates in both directions (§4.4). Flags are added: salary above band, outside Mauritius, career gap, missing information, prompt injection, redaction.
5. **Scoring (code):** 70% must-haves and 30% nice-to-haves, weighted; bands at 75 and 55.
6. **Report Agent:** a brief-format summary and 3 to 5 behavioural interview questions. Code removes anything mentioning a protected characteristic, and adds a neutral question for each career gap and a work-permit question for candidates abroad.
7. **Save:** the result is saved straight away, so a crash keeps finished work. The trace records every step's time, tokens, cache use, model, prompt version and retrieved clauses.

## Key decisions

| Decision | Why |
|---|---|
| No agent framework; a plain-Python supervisor | Every step is visible and testable, and easy to explain. The workflow is fixed, so a framework adds little |
| Scores computed by code, never the AI | The same input always gives the same score, and the rubric is auditable |
| Quotes verified against the CV | Stops the model inventing evidence. Unverified becomes missing, never met |
| Redaction and injection quarantine before the AI | The model can't be biased by details it never sees, or hijacked by text it never receives |
| Code overrides the model where facts are mechanical | Dates, years, gaps and labelled fields are computed by code. The evaluation showed a 7B model gets these wrong (see the refinement log) |
| BM25 for RAG, not embeddings | A small knowledge base (about 30 clauses): keyword search is enough, offline, free, and every hit is explainable |
| JSON + Pydantic + repair loop for every AI output | Typed, validated results from both providers, with automatic recovery from bad JSON |
| Cache in the database | Repeat runs are instant, free and identical; the demo works offline |
| SQLAlchemy with SQL that works on SQLite and Postgres | The same code runs locally (SQLite) and in the cloud (Neon), so data survives restarts |
| Gemini model fallback chain, plus Ollama locally | Free-tier rate limits and quotas don't stop the work |
| Three human checkpoints | The AI recommends; only a recorded human decision shortlists or rejects, and a rejection needs a reason |
