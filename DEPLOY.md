# Deploy & submit — step by step (Windows)

The build takes about 45 minutes end to end. Follow the steps in order.

---
## 1. Run it on your laptop (5 min)
Open **PowerShell** inside the `vera-bot` folder:
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --port 8080
```
Leave that window open. In Brave, open `http://localhost:8080/v1/healthz`. You should see `"status":"ok"`.

## 2. Score it with magicpin's official judge (10 min, needs a free Gemini key)
1. Get a key at **aistudio.google.com**: sign in, click *Get API key*, then *Create API key*.
2. Open `magicpin-ai-challenge\judge_simulator.py` in Notepad and edit the top lines:
   ```python
   BOT_URL = "http://localhost:8080"
   LLM_PROVIDER = "gemini"
   LLM_API_KEY = "PASTE_YOUR_KEY"
   LLM_MODEL = "gemini-2.5-flash"      # the default 1.5-flash is retired
   TEST_SCENARIO = "all"               # then run again with "full_evaluation"
   ```
3. Open a **second** PowerShell window in the challenge folder and run:
   ```powershell
   $env:PYTHONUTF8=1          # stops Windows from garbling ₹ and emoji in the dataset
   python judge_simulator.py
   ```
   - `all` checks the auto-reply, intent and hostile scenarios. All of them should say PASS.
   - `full_evaluation` scores about 14 messages on the 5 dimensions.

Optional: the full phase 1–4 simulation over all 100 triggers.
```powershell
python dataset\generate_dataset.py --seed-dir dataset --out expanded      # in the challenge folder
python tests\harness.py http://localhost:8080 ..\magicpin-ai-challenge\expanded   # in vera-bot
```

## 3. Put the code on GitHub (5 min)
Create an **empty** repo on github.com called `vera-bot` (Public, no README). Then, in PowerShell inside `vera-bot`:
```powershell
git init
git add .
git commit -m "Vera bot for magicpin AI challenge"
git branch -M main
git remote add origin https://github.com/Raxsh-0/vera-bot.git
git push -u origin main
```

## 4. Deploy on Render (10 min, free)
1. Go to **render.com** and sign in with GitHub.
2. Click **New +**, then **Web Service**, then pick the `vera-bot` repo.
3. Fill in the settings:
   - Runtime: **Python 3**
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
   - Instance type: **Free**
4. Under **Environment Variables**, add:
   - `CONTACT_EMAIL` = your **DTU email**
   - `PYTHON_VERSION` = `3.11.9`
   - (optional, only after step 2 shows it helps) `LLM_API_KEY` = your Gemini key, and `LLM_MODE` = `smart`
5. Click **Create Web Service** and wait for the log to show "Your service is live". Your URL looks like `https://vera-bot-xxxx.onrender.com`.
6. Test it: open `https://vera-bot-xxxx.onrender.com/v1/healthz` in the browser.

## 5. Keep it awake (critical, 3 min)
Render's free tier sleeps after 15 minutes of no traffic. Waking up takes about 50 s, and the judge's health check only waits about 5 s.
1. Go to **cron-job.org** and create a free account.
2. Click **Create cronjob**.
3. Set the URL to `https://vera-bot-xxxx.onrender.com/v1/healthz` and the schedule to **every 5 minutes**. Save.

Keep this cron job running until magicpin says evaluation is over.

## 6. Final check against the live URL
Set `BOT_URL` in `judge_simulator.py` to your Render URL and run it again. When you're done testing, wipe the test state:
```powershell
Invoke-RestMethod -Method Post https://vera-bot-xxxx.onrender.com/v1/teardown
```
The bot also resets its send-dedup automatically when a new judge run starts, so leftover test runs can't mute the real evaluation.

## 7. Submit
1. On **magicpin.com/vera/ai-challenge**, click *Submit entry*. Use your **DTU college email** and paste the Render URL. The URL is the base only, with no `/v1`.
2. Reply to Rishav Kumar's email thread: "Submitted the Vera AI Challenge with bot URL https://…, from <DTU email>."
3. Don't redeploy, delete, or pause the service, or the cron job, until results come out.

## 8. If something breaks
- **Render log shows `ModuleNotFoundError`:** check that `requirements.txt` is in the repo root.
- **`/v1/healthz` is slow the first time:** the service was asleep. Check the cron job.
- **Want to switch the LLM off instantly:** delete `LLM_API_KEY` in Render's Environment tab. The bot redeploys as the pure rules engine.
