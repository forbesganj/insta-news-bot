# Architecture

```
skills/ (UPSTREAM, untouched)         newsroom/ (NEW)                             GitHub Actions
 ig-reel / ig-caption / ig-human       cli.py -> pipeline.py (stages)
 hookscore.py beats.py caption.py      sources/   RSS, Hacker News, Google Trends, Google News, Reddit
 humanize.py detect.py hooks.json      dedupe.py  cluster + duplicate prevention
        ^                              research.py, verify.py, score.py
        | subprocess + SKILL.md text   llm/       Gemini | Anthropic | OpenAI-compatible | offline
        |                              content.py  script -> hook gate -> beats gate -> clean -> human gate -> caption gate
        +----------- gates.py ---------+ media/     tts -> visuals -> subtitles -> ffmpeg -> QA
                                       publish/   hosting (Supabase/R2), meta (Instagram API), telegram (approval)
                                       analytics/ collector (insights) + optimizer (learned weights)
                                       store.py   SQLite (state branch)

produce.yml (3x/day)  collect -> process -> render -> queue (+ Telegram preview)
publish.yml (5x/day)  apply approvals -> publish at most 1 approved Reel
analytics.yml (daily) insights at 24h/72h/7d -> perf score -> weights
refresh-token.yml     renew the 60-day Instagram token twice a month
```

## Story lifecycle
`candidate` -> `verified` | `rejected` -> `scripted` -> `queued` (approval: pending / approved / auto) -> `publishing` -> `published` (or `dry_published` when DRY_RUN=true). Terminal: `rejected`, `failed`, `expired`, `needs_manual_check`.

## Gates (all fail closed)
1. Verification: 2+ independent outlet groups or 1 tier-1 source; not stale; 3+ facts whose evidence quote is found in the source text; rumour language needs strong confirmation; stock-tip topics rejected; sensitive topics force human review.
2. Numeric grounding: every number in the script must appear in the extracted facts.
3. Third person only (no "I/we/my").
4. Hook: upstream `hookscore.py` score >= `gates.hook_min`, no dealbreaker (built-in fallback if the tool is unavailable).
5. Timing: upstream `beats.py` duration 18-40s, hook <= 3.6s.
6. Humanise + upstream `detect.py` score >= `gates.human_min`.
7. Caption: upstream `caption.py` (no FAIL) + built-in hard rules (<= 5 hashtags, <= 2200 chars).
8. Media QA: 1080x1920 H.264/AAC, duration, size, not-blank frames, source attribution present.
9. DRY_RUN, daily cap, minimum gap, approval.

## Learning loop
`perf` (0-100) = 60% reach vs account median + 40% engagement quality (shares/saves weighted highest). Per pillar and per hook formula, a shrunk mean becomes a multiplier in [0.7, 1.4] that changes story scores and which hook formulas the LLM is offered. 20% of selections are exploratory.
