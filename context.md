CONTEXT
I'm Himanshu, a student at DTU. magicpin gave me the "Vera AI Challenge" as part of placement interviews, with a 24-hour deadline. I must host a public HTTP bot that magicpin's AI judge will call. The bot is ALREADY BUILT and tested. Your job is to verify it on my Windows laptop, push it to GitHub, deploy it publicly, and help me submit. Do NOT rebuild or redesign it.

WORKSPACE
- vera-bot/ is my finished bot (Python, FastAPI). Read vera-bot/README.md and vera-bot/DEPLOY.md first. DEPLOY.md is the step-by-step plan, so follow it.
- magicpin-ai-challenge/ is magicpin's official pack. It contains challenge-brief.md, challenge-testing-brief.md (the API contract), examples/, dataset/ and judge_simulator.py (the official local judge). Read challenge-testing-brief.md so you understand the 5 endpoints: GET /v1/healthz, GET /v1/metadata, POST /v1/context, POST /v1/tick, POST /v1/reply.

HOW THE BOT WORKS (so you don't break it)
- A deterministic rules engine lives in vera/composer.py (one handler per trigger kind), vera/facts.py and vera/replies.py (the multi-turn reply engine). There's an optional LLM layer in vera/llm.py. It is OFF unless LLM_API_KEY is set, and every LLM output is validated against the input data.
- app.py is the server. vera/store.py stores versioned contexts.
- The design rules are deliberate: never invent facts, send one CTA per message, send no URLs, send at most 1 message per merchant per tick, and keep the output deterministic.

HARD RULES FOR YOU
1. Do not rewrite, refactor or "improve" the bot logic. Only fix something if a command below actually fails, and if you do, show me the error and the exact minimal diff before applying it.
2. Never put any API key in code, in the repo or in a commit. Keys go only in environment variables or in my local judge_simulator.py, and judge_simulator.py is NOT part of the vera-bot repo.
3. I'm on Windows. Use PowerShell. Before running any Python that reads the dataset, run: $env:PYTHONUTF8=1
4. Stop and ask me whenever you need a login, a key, a click I must make, or before anything irreversible, such as creating a repo, deploying, or submitting.
5. After each phase, give me a 2-3 line status: what passed and what's next.

PHASE 1: Local setup and verification
a) In magicpin-ai-challenge: python dataset\generate_dataset.py --seed-dir dataset --out expanded
b) In vera-bot: python -m venv .venv ; .venv\Scripts\activate ; pip install -r requirements.txt
c) Start the server in its own terminal: uvicorn app:app --port 8080. Check that http://localhost:8080/v1/healthz returns "status":"ok".
d) In a second terminal (in vera-bot, venv active): python tests\harness.py http://localhost:8080. The expected result is "No problems found" and all latencies under 1s.
e) python tests\dump_all.py > messages.txt. Skim it and tell me if any message looks broken (blank names, "None", bad grammar). Report only; don't edit.

PHASE 2: Official judge score (needs my Gemini key; ask me for it)
In magicpin-ai-challenge\judge_simulator.py, set only the config lines:
BOT_URL="http://localhost:8080", LLM_PROVIDER="gemini", LLM_API_KEY=<my key>, LLM_MODEL="gemini-2.5-flash" (the default 1.5-flash is retired), TEST_SCENARIO="all".
Run python judge_simulator.py. All 4 scenarios must PASS. Then set TEST_SCENARIO="full_evaluation", run it again, and show me the per-dimension averages plus the 3 weakest messages with the judge's reasons. Don't change the bot based on this without asking me.

PHASE 3: GitHub
- Check that .gitignore excludes .venv, __pycache__, vera_state.json and .env, and that no key appears anywhere in vera-bot (search for "AIza" and "sk-").
- Inside vera-bot only: git init, add, commit "Vera bot for magicpin AI challenge", branch main.
- Create a PUBLIC repo named vera-bot under my GitHub account Raxsh-0. Use the gh CLI if it's installed and logged in; otherwise ask me to create an empty repo in the browser and give you the URL. Then add the remote and push. Confirm the files are visible on github.com.

PHASE 4: Deploy on Render (free)
Guide me through, or use the browser for, render.com: log in with GitHub, then New → Web Service → the vera-bot repo. Use these settings:
Runtime Python 3; Build: pip install -r requirements.txt; Start: uvicorn app:app --host 0.0.0.0 --port $PORT; Instance: Free; Health check path: /v1/healthz.
Environment variables: CONTACT_EMAIL=<ask me for my DTU email>, PYTHON_VERSION=3.11.9. Do NOT add LLM_API_KEY unless I explicitly say so after seeing the Phase 2 results.
Wait until it's live, then open https://<my-service>.onrender.com/v1/healthz and /v1/metadata and show me both responses.

PHASE 5: Keep it awake + test live
- Render's free tier sleeps after 15 minutes idle, and the judge's healthz timeout is about 5s. Help me create a cron-job.org job that hits https://<my-service>.onrender.com/v1/healthz every 5 minutes.
- Point judge_simulator.py BOT_URL at the Render URL and run TEST_SCENARIO="all" again. All must pass.
- Then wipe the test state: Invoke-RestMethod -Method Post https://<my-service>.onrender.com/v1/teardown

PHASE 6: Submission checklist (I do the final clicks)
Give me:
(1) the exact base URL to submit (no /v1, no trailing slash)
(2) a reminder to submit on magicpin.com/vera/ai-challenge with my DTU email
(3) a 3-line reply to send on Rishav Kumar's email thread confirming submission with the URL
(4) a reminder not to delete or redeploy the service or pause the cron job until results are out.

Start with Phase 1. Tell me your plan in 3 lines, then begin.