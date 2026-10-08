I want to develop a web app that helps a recruiter review the candidates for a job opening and prepare a shortlist for human review. For the demo the job is a Marketing Executive role. The AI should do the reading, comparing and summarising of the CVs, but the recruiter makes every decision. Name: TalentLens. Tagline: "AI does the reading. You make the call."

This is my capstone project for the Agentic AI Bootcamp. It needs to show prompt engineering, agentic coding with a working app, a few advanced concepts done properly, and clear testing and refinement. Deployment is optional.

It will do as follows:
1. The recruiter loads the job description and the company's hiring guidelines, either the sample files or an upload.
2. The AI turns the job description into a checklist of requirements: must-haves, nice-to-haves, minimum years and the salary band. The recruiter checks it and can edit it before screening starts.
3. The recruiter uploads the candidates' CVs (PDF and Word).
4. The agents go through each CV. They pull out the facts, compare them to the checklist and write a summary. The recruiter can watch each agent's progress while it runs.
5. For each candidate the app shows the relevant experience, key skills, missing information, flags and 3 to 5 interview questions, like the example in the brief. It also shows a score and a recommendation: Strong match, Possible match, or Not a match for this role. Each requirement is marked met, partial or missing, with the quote from the CV it's based on.
6. All candidates can be compared side by side, with a ranking and a table of candidates against requirements.
7. The recruiter decides for each candidate: shortlist, hold or reject. A rejection needs a written reason. Every decision is saved with the time.
8. The app drafts a shortlist report for the hiring manager that includes only the candidates the recruiter shortlisted. The recruiter can edit it, approve it and download it.
9. The recruiter can save preferences, for example "retail experience matters a lot". They are remembered and used in the next screening.
10. A "behind the scenes" page shows what each agent did, how long it took, the tokens used, and the test and evaluation results.

Agents:
Instead of one big prompt, separate agents each do one job, and a supervisor written in plain Python runs them in order. I want a controlled workflow rather than one fully autonomous agent, because in hiring it's more important to be predictable and to see what happened at each step.
- Job Analyst: makes the requirements checklist from the job description, using my saved preferences
- CV Analyst: pulls the facts out of one CV in a fixed format, with no opinions
- Comparison Agent: marks each requirement met, partial or missing, with a quote from the CV
- Guardrail Reviewer: mostly normal code that checks the AI's work. It checks that the quotes are really in the CV, what information is missing, whether the salary is above the band, and that interview questions avoid personal topics. If a quote doesn't check out, it sends the work back to the Comparison Agent once.
- Report Agent: writes the candidate summaries and interview questions, and drafts the shortlist report

AI concepts to show:
- prompt engineering and structured outputs: every agent returns JSON in a fixed format, and broken JSON gets sent back to be fixed
- multi-agent, with a review step
- RAG over the hiring guidelines, so the agents follow and cite the company rules. Start with simple keyword search because the document is small and it works offline; use embeddings later if there's time.
- human in the loop: approving the checklist, every decision, and approving the report
- memory: preferences, saved runs and decisions
- guardrails: personal details are removed before the AI sees a CV, and hidden instructions in CVs are caught
- testing and evaluation

If there's time:
- a small "ask the CVs" chat using tool calling
- an n8n workflow that emails the hiring manager when the shortlist is approved
- deployment on Streamlit Community Cloud

Not doing: MCP, browser automation, logins or multiple users, real candidate data.

Users:
- the recruiter (main user; single user, no login needed)
- the hiring manager, who receives the shortlist report

Type: web app running on my laptop, with optional deployment

Tech Stack:
- Language: Python
- UI: Streamlit
- AI: Gemini API free tier as the main model (model to confirm from my key), and Ollama on my laptop as the backup (model to confirm)
- Database: SQLite
- Other: Pydantic for the JSON formats, pypdf and python-docx to read CVs, pytest for tests
- No LangChain, CrewAI or similar. Plain Python, so I can explain every step.
- GitHub public repo. The API key stays in .env and never gets committed.
- Built with Claude Code in VS Code

UI/UX:
It should look modern, clean and professional, like a real HR product. Light theme with a teal accent, a card for each candidate, score badges, colour-coded met/partial/missing, and a live progress timeline while the agents run. It must be easy to read on a projector. Suggest more here.

Processing:
Everything runs in the same app, so there's only one thing to run and deploy. The score is calculated by code from the rubric in the hiring guidelines, not by the AI. AI calls are spaced out and retried because of the Gemini free-tier limits. Results are cached so a run can be replayed instantly without internet.

Data (already created in data/sample/):
- fictional company Corallia Living Ltd (a retailer in Mauritius), Marketing Executive role, MUR 45,000 to 60,000 per month
- job_description.md and hiring_guidelines.md (scoring rubric, required information, fairness rules, rules for using AI)
- 6 CVs, each testing something:
  - Sarah Moutou: strong and complete
  - Kevin Ramdin: strong, but no salary, notice period or right to work (Word file)
  - Priya Doorgakant: junior, no paid ads or analytics
  - Jean-Marc Lebrun: personal details to remove, a 2-year career gap, salary 25% above the band
  - Aisha Patel: lives in South Africa (work permit question), salary "open to discussion", basic French
  - Ryan Chen: hidden white text telling the AI to rank him first
- live_demo/Nadia Ramsamy: strong, but no notice period. Uploaded live in the demo.
- ground_truth.json with what I expect for each CV

Acceptance criteria:
- every candidate gets experience, key skills, missing information, flags, interview questions, a score and a recommendation
- every met or partial requirement has a quote that really appears in the CV
- the same input always gives the same score, using the rubric from the guidelines: 70% must-haves and 30% nice-to-haves; 75 and above is a strong match, 55 to 74 a possible match, below 55 not a match
- a missing email, phone number, salary expectation, notice period or right to work is flagged
- date of birth, marital status, nationality and similar details are removed before the AI sees the CV, and never show up in summaries or questions
- the hidden instructions in Ryan's CV are flagged and don't change his result
- the career gap becomes a neutral interview question, not a negative
- nothing is shortlisted or rejected without my decision, and rejecting without a reason is blocked
- the shortlist report only includes candidates I shortlisted
- preferences and results are still there after closing and reopening the app
- the results for the demo CVs match ground_truth.json
- it works with Gemini, can switch to Ollama, and a cached run works without internet
- tests pass

to keep in mind:
- the demo is 10 minutes in total: 2 to 3 minutes of slides, then 7 to 8 minutes of live demo f
-  The code must be simple, readable and commented, and each part explained to me as it's built, so I can explain it in the demo.
- free models only (the Gemini free tier has rate limits)
- English only, and only text-based PDF or Word CVs (no scanned images)
- fictional data only, because Google can use free-tier requests to improve its products


Submission needs: a working prototype, the source code (GitHub link), the project link if deployed, a short explanation of the architecture, the key prompts, evidence of testing, the project title, my name and a short description.

Todo:
Help me prepare the spec document that will be used to make an implementation plan. Include all relevant details.