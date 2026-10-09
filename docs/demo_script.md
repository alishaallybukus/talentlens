# Demo script

**Format:** 10 minutes. About 2 to 3 minutes of slides, 7 minutes of live demo, then a 30-second learnings slide.

## Before you start (Saturday morning)

- Run `streamlit run app.py`. The saved run loads automatically.
- Page 4: **Reset decisions** (confirm), so you can make them live.
- Sidebar → Model settings → **Test connection**. Use Ollama if Gemini is slow or out of quota (the saved run is cached either way).
- Have `data/sample/live_demo/Nadia_Ramsamy_CV.pdf` ready to upload (e.g. copied to the desktop).
- Open the live link once to wake it up, if deployed.
- Browser zoom 125%, notifications off, laptop charging.

## Slides (2 to 3 minutes)

1. **The problem:** a recruiter screening dozens of CVs by hand is slow and inconsistent, and AI screening tools can be biased, invent things and make decisions nobody can explain.
2. **The solution:** TalentLens. "AI does the reading. You make the call." Three principles: the AI never decides; no evidence, no credit (every claim is a verified quote); the AI only sees what it should (redaction and injection quarantine first).
3. **Architecture:** the diagram from `docs/architecture.md`: five agents and a code supervisor, RAG over the hiring guidelines, guardrails before and after the AI, three human checkpoints, one LLM layer for Gemini and Ollama.
4. **AI concepts used:** multi-agent workflow, RAG with citations, structured JSON output with repair, guardrails, self-correction (revise loop), tool calling (Ask the CVs), memory, human in the loop, evaluation, observability.

## Live demo (7 to 8 minutes)

| Time | Page | What to show and say |
|---|---|---|
| 0:00 | 1 Job setup | The sample job and guidelines. A saved preference in the sidebar. The approved checklist: show one edit (e.g. raise a weight). "The Job Analyst drafted this; I approved it. Screening can't start until I do." |
| 0:45 | 2 Candidates | Intake badges. **Jean-Marc:** "3 details redacted": open "What the AI will see" and point to `[REDACTED]`. **Ryan:** "⚠ Suspicious text removed": his CV hides an instruction to the AI. "The model never sees it." |
| 1:15 | 3 Screening | The completed timeline. Upload **Nadia** on page 2, then **Screen new CVs**. Explain each chip as it lights up: CV Analyst, Comparison, Reviewer, Report. (About 30 to 90 seconds.) |
| 2:45 | 4 Review | Ranking and matrix. **Sarah:** quotes with ✔ verified badges and § clause chips (click one). **Kevin:** strong, but missing salary, notice period and right to work; draft the email. **Jean-Marc:** redaction, neutral career-gap question, salary-above-band flag. **Ryan:** injection caught, still "Not a match". **Nadia:** just added. |
| 5:00 | 4 Review | Decisions: shortlist Sarah, Kevin and Nadia; hold Aisha. Reject Ryan **without a reason**: blocked. Then with a reason. |
| 6:00 | 5 Report | **Draft report**, make a small edit, **Approve report**, download the HTML. "Only shortlisted candidates are named." |
| 6:45 | 6 Behind the scenes | Trace (tokens, time, cache hits, retrieved clauses), the evaluation table (7 of 7), the audit log of my decisions. Optional: 💬 Ask the CVs, "Who has TikTok experience?" |
| 7:30 | | Wrap up |

## Learnings slide (30 seconds)

- **Let the AI read; let code decide anything mechanical.** The first Ollama evaluation got 3 of 6. Every failure was a mechanical fact (dates, labelled fields) or the model not reading the whole CV. Code checks and two prompt revisions took it to 7 of 7.
- **Guardrails need to be layered.** Redaction and quarantine before the AI, quote verification and the bias filter after it, and a human at the end.
- **Free-tier limits are real.** Caching, spacing, model fallback and a local model kept the project moving.

## Likely questions, with short answers

- **Why not let the AI give the score?** Scores must be consistent and explainable. Code applies the guidelines' rubric, so the same evidence always gives the same score.
- **How do you stop the AI inventing evidence?** Every met or partial needs a quote that code finds in the CV. If it isn't found, the agent gets one retry, then it counts as missing and is flagged.
- **What stops bias?** Protected details are removed before the AI sees the CV; the prompts forbid using them; a bias filter removes any question or sentence that mentions them; and only a human decides.
- **What's the prompt injection defence?** Lines that look like instructions are removed at intake and shown to the recruiter; CV text sits inside `<cv>` tags and the prompt says it's data, not instructions. Ryan's hidden line never reaches the model and doesn't change his result.
- **Why BM25 and not embeddings?** About 30 short clauses: keyword search finds the right ones, works offline, costs nothing and is fully explainable.
- **Why no LangChain or CrewAI?** The workflow is fixed and small. Plain Python makes every step visible, testable and easy to explain.
- **How do you know it works?** 200+ automated tests with a fake model, plus an evaluation of the real model on 7 CVs against known answers, with every fix logged in the refinement log.
- **What if Gemini is down?** Retries, a fallback across three Gemini models, the cache, and a local Ollama model.
- **Limitations?** Pattern-based redaction can miss unusual wording; scanned CVs aren't read; tested on fictional data for one job. The recruiter's review is the final safeguard.
