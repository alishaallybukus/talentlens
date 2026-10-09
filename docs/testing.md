# Testing and evaluation

TalentLens is tested in two ways:
1. **Automated tests (pytest):** 201 tests that run offline in about 30 seconds, using a fake model (`tests/fake_llm.py`) that never calls a real API.
2. **Evaluation with real models (`eval/run_eval.py`):** the full pipeline on the 7 fictional demo CVs, compared with the expected results in `data/sample/ground_truth.json`.

Every change made because of a test or evaluation result is in the [refinement log](refinement_log.md).

## 1. Automated tests

Run with `pytest -v`. Full output: [`test_results.txt`](test_results.txt). **Result: 201 passed.**

| File | Tests | What it checks |
|---|---|---|
| `test_documents.py` | 16 | All 7 sample CVs (PDF and DOCX) are read; empty, scanned, damaged and unsupported files give a friendly error |
| `test_guardrails.py` | 51 | Jean-Marc's protected details redacted; Ryan's hidden instruction quarantined; no false positives on other CVs; quote verification (exact, small changes, invented); salary parsing and band check; outside Mauritius; years and career gaps from dates; missing information; bias filter |
| `test_rag.py` | 8 | Guidelines split into numbered clauses; "salary above band" finds §6.1, "career gap" finds §2.3, "interview questions" finds §7.x |
| `test_scoring.py` | 11 | All met = 100, all missing = 0, the worked example = 84.3; band edges at 54.9 / 55 / 74.9 / 75; weights matter |
| `test_llm.py` | 18 | JSON from fenced or chatty replies; repair loop; retries, model fallback and daily-quota handling; hard deadline on stuck calls; call spacing; the cache returns identical results |
| `test_memory.py` | 23 | Preferences, jobs, documents, runs, decisions and reports saved and reloaded; **a rejection without a reason is blocked**; audit log written; Postgres URLs and table definitions for Neon |
| `test_supervisor.py` | 22 | Full runs with the fake model; exactly one revision for unverified quotes; still unverified becomes missing plus a flag; one failing CV doesn't stop the others; **no prompt ever contains Ryan's hidden text or Jean-Marc's protected details**; minimum years in both directions; career gaps; the code fixes found by the evaluation |
| `test_report.py` | 8 | **Only shortlisted candidates appear in the report**; brief format; footer line; the overview prompt never mentions other candidates; HTML is escaped |
| `test_eval.py` | 6 | The evaluation's checks: bands, exact missing information, expected flags, no false injection flags |
| `test_extras.py` | 9 | Excel sheets and colours; email asks for exactly the missing items; Ask-the-CVs tool loop, the 4-call limit, verified and invented quotes, the unanswerable question, quarantined text not searchable |
| `test_errors.py` | 9 | Wrong API key, rate limit on every model, no internet, Ollama stopped, model not downloaded, empty PDF: each gives a friendly message, in code and in the app |
| `test_app.py` | 16 | Every page opens; the full journey (job → CVs → screening → decisions → report → download) in the app with the fake model; Nadia added with **Screen new CVs**; reset decisions; access code; Ollama hidden when deployed; extras in the app |
| `test_config.py` | 4 | Settings from `.env` and Streamlit secrets; secrets never shown |

## 2. Evaluation with real models

Run with `python -m eval.run_eval --provider gemini` (or `--provider ollama`). For each CV it checks:
- the band is one of the acceptable bands in the ground truth
- the missing information matches exactly
- every expected flag is present, and there's no false prompt-injection flag

It also measures quotes verified on the first try, revisions, JSON repairs, time and tokens per CV. Results are saved in [`eval/results/`](../eval/results/) and shown on page 6, Behind the scenes.

### Gemini (`gemini-3.8-flash`): 7 of 7 pass

From [`eval_gemini_20261009_172519.md`](../eval/results/eval_gemini_20261009_172519.md), with the final prompts:

| CV | Band | Expected | Missing info | Flags | Result |
|---|---|---|---|---|---|
| Nadia Ramsamy | Strong match | Strong match | notice_period | missing_info | ✅ pass |
| Sarah Moutou | Strong match | Strong match | - | - | ✅ pass |
| Kevin Ramdin | Strong match | Strong match | salary_expectation, notice_period, right_to_work | missing_info | ✅ pass |
| Aisha Patel | Strong match | Strong or Possible | salary_expectation, right_to_work | missing_info, references_later, outside_mauritius | ✅ pass |
| Priya Doorgakant | Possible match | Possible or Not a match | - | references_later | ✅ pass |
| Jean-Marc Lebrun | Not a match | Not a match or Possible | - | protected_info_redacted, references_later, salary_above_band, career_gap | ✅ pass |
| Ryan Chen | Not a match | Not a match | - | prompt_injection, references_later | ✅ pass |

### Ollama (`qwen2.5:7b`, local): 7 of 7 pass, after refinement

| Run | Prompts | Passed | Main problems |
|---|---|---|---|
| [`083922`](../eval/results/eval_ollama_20261009_083922.md) | `comparison@v2`, labelled-field and date fixes | 6 of 7 | Kevin: quoted code's computed years instead of the CV |
| [`084504`](../eval/results/eval_ollama_20261009_084504.md) | `comparison@v3` | 6 of 7 | Kevin: 3-years must-have still marked missing despite 6.2 computed years |
| [`115229`](../eval/results/eval_ollama_20261009_115229.md) | + minimum years decided by code from the dates | **7 of 7** | none |

Before any of these fixes, a first Ollama command-line run got only 3 of 6 right (Sarah, Priya and Ryan wrong). The fixes are explained in the [refinement log](refinement_log.md).

### Gemini compared with Ollama

| Metric | Gemini 3.8 Flash | Ollama qwen2.5:7b |
|---|---|---|
| CVs passed | 7 of 7 | 7 of 7 (after refinement) |
| Band accuracy | 100% | 100% |
| Missing information, precision / recall | 100% / 100% | 100% / 100% |
| Quotes verified on the first try | 100% | 93% |
| Revisions needed (quotes not found) | 0 | 2 |
| JSON repairs | 0 | 0 |
| Average time per CV (not cached) | 110 s* | 40 s |
| Average tokens per CV | 9,300 | 9,700 |

\* Gemini was overloaded during the run and answered "busy" (503) several times per call; each call waited and retried, which inflated its times. Without retries a CV takes roughly 20 to 40 seconds. The Ollama times are from run `084504`, on a laptop CPU.

**Takeaway:** Gemini was right first time. The small local model needed code to take over the mechanical facts (dates, years, labelled fields) and clearer prompt rules, after which it matched Gemini on every pass/fail check.

## 3. Manual checks

Besides the fake-model app tests, the full journey was driven through the real app with Streamlit's test runner on a copy of the real database and a real model (Ollama): job setup, every candidate's detail view, decisions (a rejection without a reason was blocked), report draft, approval and both downloads, with no errors on any page. Ask the CVs was tried with the three example questions and an unanswerable one. On Ollama, TikTok and the unanswerable question were right, but the notice-period answer added an extra name and the budget answer missed Sarah. On Gemini, the notice-period question was answered correctly (Kevin only). The demo flow, browser zoom, offline (Wi-Fi off) and deployed-app checks are done by hand before the demo.

## 4. Refinement

See [`refinement_log.md`](refinement_log.md): every prompt and design change after testing, with what we saw, what changed (with the prompt version) and the result.
