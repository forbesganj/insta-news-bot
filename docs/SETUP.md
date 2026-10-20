# Step-by-step setup (from zero to a running pipeline)

Follow the parts in order. Do **not** skip Part 2: you will see the whole pipeline work before you create a single account.

---
## Part 1: Put the code in place (10 minutes, on your computer)

You need Python 3.10+, git and ffmpeg.
- Windows: install Python from python.org (tick "Add to PATH"), Git from git-scm.com, ffmpeg via `winget install ffmpeg`. Using **WSL (Ubuntu)** is easiest: `sudo apt install python3 python3-pip ffmpeg fonts-dejavu-core git unzip`.
- macOS: `brew install python ffmpeg git`.

```bash
# 1. You already cloned the upstream repo. Go into it:
cd instagram-agent-skill

# 2. Unzip the overlay ON TOP of it (it only adds files; skills/ is not touched):
unzip -o ../instagram-news-pipeline-overlay.zip

# 3. Install and ignore local files:
pip install -r requirements.txt
cat gitignore-additions.txt >> .gitignore
```
You should now see `skills/` **and** `newsroom/` side by side:
```bash
ls          # newsroom  skills  config  tests  docs  scripts  tools ...
```

## Part 2: See it work with no accounts and no keys (5 minutes)

```bash
python -m newsroom.cli demo
```
This runs the whole pipeline on fictional feeds: collect, dedupe, research, verify, script, gates, render, QA, dry-run publish. Expected end of output: two `video: out/s....mp4` lines, and one `rejected ...: verify: only 1 independent source(s), need 2` (the demo's fake rumour being correctly blocked). Open the MP4s and look at them.

```bash
python -m unittest discover -s tests -t .     # all tests should pass (about 2 minutes: it renders videos)
python -m newsroom.cli doctor                 # shows what is missing, in plain words
```

## Part 3: Connect the upstream skills (important, 5 minutes)

```bash
python -m newsroom.cli check-upstream
python tools/audit_repo.py --smoke            # writes docs/audit_report.md
```
`check-upstream` prints, for each upstream tool (`hookscore`, `beats`, `caption`, `detect`), whether the wrapper found it and **parsed** its output.
- All `parsed OK`: the real skills are driving the gates. 
- Any `NOT PARSED`: the pipeline still runs using built-in heuristics, but that gate is not using your skill. Send me the output of `check-upstream` and `docs/audit_report.md` and I will adapt the parser (it is a 10-line change in `newsroom/gates.py`). I could not read the upstream scripts' source when I built this, so this check is how we close that gap.
- `humanize.py`: `check-upstream` also prints its raw output. If it prints the cleaned text, set `gates.humanize_mode: upstream_stdout` in `config/settings.yaml`; if it rewrites the file in place, set `upstream_inplace`. The default `builtin` always works and is always applied afterwards.

## Part 4: Real scripts, still no publishing (15 minutes)

1. Get a free Gemini key: https://aistudio.google.com/apikey
2. ```bash
   export GEMINI_API_KEY="paste-key-here"      # Windows PowerShell: $env:GEMINI_API_KEY="..."
   export DRY_RUN=true
   python -m newsroom.cli produce
   python -m newsroom.cli status
   ```
   This pulls real news from the feeds in `config/sources.yaml`, verifies it, writes real scripts through your skills, and renders videos into `out/`. `DRY_RUN=true` means nothing can be posted.
3. Watch the videos. Read the captions with `status` or in the SQLite file. Tune `config/settings.yaml` (brand name, handle, colours), `config/pillars.yaml`, `config/sources.yaml`.
4. Voice: without any key the videos are silent text Reels. For a free voice, `pip install edge-tts` is already in requirements, so voice is automatic (unofficial service, may break). For something sturdier add Azure Speech (free tier): see `docs/SECRETS.md`.

## Part 5: Put it on GitHub (15 minutes)

1. Create a **private** repository on GitHub (private keeps your state and prompts out of public view; free accounts get 2,000 Actions minutes/month, enough for about 5 Reels a day).
2. Point your clone at it and push:
   ```bash
   git remote rename origin upstream
   git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
   git add -A && git commit -m "Add newsroom pipeline" && git push -u origin HEAD
   ```
3. GitHub > repo > **Settings > Secrets and variables > Actions**:
   - **Variables** tab: `DRY_RUN` = `true`, `APPROVAL_MODE` = `telegram`
   - **Secrets** tab: add the ones you have from `docs/SECRETS.md` (start with `GEMINI_API_KEY`).
4. **Settings > Actions > General > Workflow permissions** > "Read and write permissions" > Save (needed for the state branch).
5. **Actions** tab > **check-setup** > Run workflow. Read the log. Fix every `[FIX ]` line.
6. **Actions** > **produce** > Run workflow. When it finishes, a `state` branch appears in your repo and the log shows the story counts. The schedules then run by themselves.

## Part 6: Telegram approvals (10 minutes)

1. In Telegram, chat with **@BotFather**, send `/newbot`, copy the token into secret `TELEGRAM_BOT_TOKEN`.
2. Send any message to your new bot. Open `https://api.telegram.org/bot<TOKEN>/getUpdates`, copy the number after `"chat":{"id":` into secret `TELEGRAM_CHAT_ID`. Use a private chat with the bot, not a group.
3. Run **produce** again. Each rendered Reel arrives in Telegram with **Approve / Reject** buttons.
4. Taps are read on the next **publish** run (up to a few hours later). Rejected or unapproved Reels expire after 36 hours.

## Part 7: Public video hosting (10 minutes)

Instagram downloads the video from a public link, so the file must be hosted briefly (it is deleted after publishing).
1. supabase.com > new project (free, no card).
2. **Storage > New bucket** > name `reels` > tick **Public bucket**.
3. **Project Settings > API**: copy Project URL into `SUPABASE_URL`, the `service_role` key into `SUPABASE_SERVICE_KEY`, and `reels` into `SUPABASE_BUCKET`.

## Part 8: Instagram / Meta API (30 minutes)

1. Instagram app > Settings > **Account type and tools > Switch to professional account** (Creator or Business).
2. https://developers.facebook.com > **My Apps > Create App**. Add the **Instagram** product and open **API setup with Instagram login**. (Meta changes these screens often; the documentation is at https://developers.facebook.com/docs/instagram-platform/content-publishing.)
3. In that screen add your Instagram account, enable permissions `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_insights`, and **Generate token** (long-lived, 60 days). Copy it into secret `IG_ACCESS_TOKEN`.
4. Find your account ID (dashboard, or `curl -H "Authorization: Bearer YOUR_TOKEN" "https://graph.instagram.com/v25.0/me?fields=user_id,username"` and use `user_id`) and store it as `IG_USER_ID`.
5. Run **check-setup** again. You want: `Instagram token valid for @yourname` and `IG_USER_ID accepted by the publishing endpoint`.
6. Auto-refresh: create `GH_PAT` as described in `docs/SECRETS.md`. The **refresh-token** workflow then renews the token on the 1st and 15th of each month.

Whether your app can publish to your own account while it is in development mode, or needs App Review for live mode, depends on what the dashboard shows for your app. Check it. I could not test against your account.

## Part 9: Going live safely

| Week | Setting | What you do |
|---|---|---|
| 1 | `DRY_RUN=true`, Telegram on | Read every preview. Look at `status` for rejections and why. Tighten sources/prompts. |
| 2 | `DRY_RUN=false`, `APPROVAL_MODE=telegram`, `publish.max_posts_per_day: 2` | Approve only Reels you would be proud of. Check each post on Instagram. |
| 3-4 | raise to 3-5 a day | Keep approving. Watch for any wrong fact: if you find one, stop and tell me, we tighten the gates. |
| Month 2+ | optionally `APPROVAL_MODE=auto_low_risk` | Sensitive stories (courts, deaths, health, rumours) still need your tap. Keep spot-checking daily. |

Disclose AI: the caption adds "AI-generated voice." automatically when synthetic speech is used. Review Instagram's current rules on AI labels for realistic synthetic media.

## Part 10: Routine

- Weekly: `python -m newsroom.cli status` (or read the Actions logs); add/remove feeds; read `python -m newsroom.cli analytics` output (learned weights).
- The system learns from real posts only after 5+ posts have 24h+ insights; before that it uses the priors in `config/news_hooks.json` and `config/pillars.yaml`.
- Never commit secrets. If a key leaks, rotate it.
