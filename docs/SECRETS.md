# Credentials: where each one comes from

Add secrets in GitHub: **repo > Settings > Secrets and variables > Actions > New repository secret**.
Add the two switches as **Variables** (same page, "Variables" tab).

| Name | Type | Needed for | Where it comes from |
|---|---|---|---|
| `DRY_RUN` | **Variable** | Safety switch. `true` = never publish. Default if unset: `true` | You set it. Change to `false` only when ready to go live. |
| `APPROVAL_MODE` | **Variable** | `telegram` (default, you tap Approve), `auto_low_risk` (auto-publish unless the story is flagged sensitive), `auto` | You set it. |
| `GEMINI_API_KEY` | Secret | Script writing + fact extraction (free tier) | https://aistudio.google.com/apikey |
| `ANTHROPIC_API_KEY` or `GROQ_API_KEY` | Secret | Alternative to Gemini (set only one provider, or pin with variable `LLM_PROVIDER`) | console.anthropic.com / console.groq.com |
| `SUPABASE_URL` | Secret | Public video hosting (Instagram downloads the video from a URL) | supabase.com > your project > Project Settings > API > Project URL |
| `SUPABASE_SERVICE_KEY` | Secret | Same | Same page > `service_role` key. **Keep secret**: it bypasses all access rules. |
| `SUPABASE_BUCKET` | Secret | Same | Storage > New bucket (e.g. `reels`) > tick **Public bucket** |
| `IG_USER_ID` | Secret | Publishing + analytics | Meta app dashboard > Instagram > API setup with Instagram login (account ID), or `curl -H "Authorization: Bearer $TOKEN" "https://graph.instagram.com/v25.0/me?fields=user_id,username"` and use `user_id` |
| `IG_ACCESS_TOKEN` | Secret | Publishing + analytics | Meta app dashboard > Instagram > API setup with Instagram login > Generate token (long-lived). Needs permissions `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_insights`. |
| `TELEGRAM_BOT_TOKEN` | Secret | Approval previews | Telegram: chat with **@BotFather**, send `/newbot` |
| `TELEGRAM_CHAT_ID` | Secret | Same | Message your new bot once, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id` |
| `GH_PAT` | Secret | Auto-refreshing the Instagram token every 15 days | GitHub > Settings > Developer settings > Fine-grained tokens > this repo only > Repository permissions > **Secrets: Read and write** |
| `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` | Secret (optional) | Better Indian-English voice | Azure portal > create a **Speech** resource (free F0 tier) > Keys and Endpoint |
| `GOOGLE_TTS_API_KEY` | Secret (optional) | Alternative voice | Google Cloud > enable Text-to-Speech API > Credentials > API key |
| `PEXELS_API_KEY` | Secret (optional) | Photo backgrounds | https://www.pexels.com/api/ |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | Secret (optional) | Reddit source (off by default; check Reddit's API terms first) | reddit.com/prefs/apps > create "script" app |
| `R2_*` | Secret (optional) | Cloudflare R2 instead of Supabase | Cloudflare dashboard > R2 (needs `pip install boto3`) |

**Minimum to see everything working in dry-run:** none. Run `python -m newsroom.cli demo`.
**Minimum for real scripts, still not publishing:** `GEMINI_API_KEY`.
**Minimum to publish for real:** `GEMINI_API_KEY`, `SUPABASE_*`, `IG_USER_ID`, `IG_ACCESS_TOKEN`, and (recommended) Telegram.

Never commit secrets. Rotate any key you ever paste into a chat, issue or log.

## Voice-over secrets (v1.3), see docs/VOICE.md
| Name | Type | Where it comes from |
|---|---|---|
| `ELEVENLABS_API_KEY` | Secret | elevenlabs.io > Profile > API Keys |
| `ELEVENLABS_VOICE_ID` | Secret | elevenlabs.io > Voices > ... > Copy voice ID |
| `ELEVENLABS_MODEL` | Variable (optional) | `eleven_multilingual_v2` (default) or `eleven_flash_v2_5` |
| `GOOGLE_TTS_API_KEY` | Secret | Google Cloud > Credentials > API key (Text-to-Speech API enabled) |
| `GOOGLE_TTS_VOICE` | Variable (optional) | e.g. `en-IN-Chirp3-HD-Aoede` |
