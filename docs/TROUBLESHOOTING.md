# Troubleshooting

Run `python -m newsroom.cli doctor` locally, or Actions > **check-setup** > Run workflow. It tells you exactly what is missing.

| Symptom | Cause / fix |
|---|---|
| `upstream hookscore: script not found` | The overlay was not unzipped into the root of the instagram-agent-skill clone. `skills/` must sit next to `newsroom/`. |
| `upstream ...: output format not recognised` | The upstream tool prints a format this wrapper does not parse. Run `python -m newsroom.cli check-upstream`, send me the output, and I adapt the parser. Meanwhile the built-in heuristic is used. |
| Stories all rejected with `verify: only 1 independent source` | Normal for a quiet news day. Add feeds in `config/sources.yaml`, or lower `verify.min_independent_sources` (not recommended). |
| `verify: not enough readable source text` | Publishers block scraping or return only headlines. The pipeline respects robots.txt. Prefer feeds with full summaries or add tier-1 primary sources. |
| Everything stays `queued` / `pending` | `APPROVAL_MODE=telegram` but Telegram is not configured, or you have not tapped Approve. Check `python -m newsroom.cli status`. |
| `no public video_url` | Configure Supabase (or R2). The Instagram API downloads the video from a public URL. |
| Container status `ERROR` | Usually video format/size. Run `doctor`; check `qa` info in `status`. The failed story is retried up to 3 times. |
| HTTP 190 / token errors | Token expired or revoked. Generate a new token in the Meta dashboard, update `IG_ACCESS_TOKEN`. Pipeline halts and alerts Telegram. |
| Scheduled workflows stopped | GitHub disables schedules on repos with 60 days of no activity. The state branch commits count as activity; also use Actions > Enable workflow. |
| Story stuck in `publishing` / `needs_manual_check` | A run died mid-publish. Check Instagram before retrying so you do not double-post. Then set the status back with `sqlite3 state/newsroom.sqlite3 "update stories set status='queued' where id='<id>'"`. |
