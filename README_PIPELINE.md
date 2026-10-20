# Automated Instagram news pipeline (extension of instagram-agent-skill)

Adds a complete, mostly automated pipeline around the existing skills, without modifying them:

`trends/news -> dedupe -> research -> fact verification -> story scoring -> script/hook/caption (existing skills + gates) -> humanise -> visuals -> TTS -> subtitles -> 9:16 video -> media QA -> Telegram approval -> Instagram API publish -> analytics -> learned prioritisation`

Start with **docs/SETUP.md**. Credentials: **docs/SECRETS.md**. Design: **docs/ARCHITECTURE.md**. Problems: **docs/TROUBLESHOOTING.md**.

## Commands
```
python -m newsroom.cli demo            offline end-to-end demo (no keys, nothing published)
python -m newsroom.cli doctor          check ffmpeg, fonts, upstream tools, keys, Instagram token
python -m newsroom.cli check-upstream  show how each upstream skill tool is parsed
python -m newsroom.cli produce         collect -> process -> render (+ Telegram previews)
python -m newsroom.cli publish         apply approvals, publish at most one approved Reel
python -m newsroom.cli analytics       pull insights, update learned weights
python -m newsroom.cli status          queue, rejections, runs, posts
python -m newsroom.cli refresh-token --print-token
```
Environment: `DRY_RUN` (default **true**), `APPROVAL_MODE` (telegram | auto_low_risk | auto).

## What is deliberately not here
No Instagram passwords, no browser automation, no scraping of Instagram, no follow/unfollow/DM/comment automation, no fake engagement, no rate-limit or CAPTCHA workarounds. Publishing and analytics use the official Instagram API only. `/ig-viral` (browser-based research in the upstream repo) is intentionally not automated.

## Honest limits
- Fact checking is a strict automated gate (multi-source rule + quote grounding + number grounding), not a guarantee. Keep human approval on until you trust it.
- Upstream scripts were wrapped from their documented output formats; run `check-upstream` to confirm (see SETUP Part 3).
- Free LLM/TTS tiers and unofficial feeds (Google Trends, edge-tts) can change or break; the pipeline degrades gracefully and logs why.
- Instagram can change API behaviour and its rules on automated or unoriginal content. Original explanation, source attribution and review are your protection.
- I could not test live Instagram, Telegram, Supabase or LLM calls from my sandbox. Those paths are unit-tested against the documented API shapes with mocks; `doctor` and a dry-run week are how you verify them on your accounts.
