# Voice-over (v1.3)

The kinetic Reels have a voice-over by default (voice.mode: on). If a premium voice (ElevenLabs / Google Chirp 3 HD) is
configured it is used; otherwise the free edge-tts voice (en-IN-NeerjaNeural) is used, which is clearly an AI voice.
Set voice.mode: auto to use ONLY premium voices (text + beat if none), or off for no voice at all.

## Your two good options
| | Google Chirp 3 HD | ElevenLabs |
|---|---|---|
| Sound | Very natural, Indian English voices (en-IN: Charon, Puck, Aoede, Kore ...) | Usually the most natural; big voice library incl. Indian-accent voices |
| Cost | First 1,000,000 characters per month free (you use about 50,000 at 5 Reels a day) | Starter about $5-6/month (about 90 Reels/month on Multilingual v2, about 180 on Flash); Creator about $22 for 5 Reels/day on v2 |
| Needs | Google Cloud account with billing enabled (card), API key | Account + API key + a voice ID |
| Secrets | `GOOGLE_TTS_API_KEY` (+ optional variable `GOOGLE_TTS_VOICE`) | `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` (+ optional variable `ELEVENLABS_MODEL`) |

Prices and free tiers change. Check the provider's pricing page before you subscribe.

## Set up Google Chirp 3 HD
1. console.cloud.google.com > create a project > enable **Cloud Text-to-Speech API** > set up billing (stays free inside the free tier).
2. **APIs & Services > Credentials > Create credentials > API key**. Restrict it to the Text-to-Speech API.
3. GitHub > Settings > Secrets and variables > Actions: add secret `GOOGLE_TTS_API_KEY`.
4. Optional voice choice: add a **Variable** `GOOGLE_TTS_VOICE` = `en-IN-Chirp3-HD-Aoede` (or Charon, Puck, Kore). Default is Charon.

## Set up ElevenLabs
1. elevenlabs.io > sign up > **Profile > API Keys** > create a key > secret `ELEVENLABS_API_KEY`.
2. **Voices** > filter by language English and accent Indian > preview several > the ... menu > **Copy voice ID** > secret `ELEVENLABS_VOICE_ID`.
3. Optional variable `ELEVENLABS_MODEL` = `eleven_flash_v2_5` (half the cost, slightly less expressive). Default `eleven_multilingual_v2`.

## LISTEN before you go live
Actions > **voice-test** > Run workflow. It reads a sample news script with every configured voice and sends the audio
to your Telegram bot. Type a different voice ID / Google voice name in the box to audition more voices. Pick the one you
would believe on TV, then set that voice in the secret/variable above.

## What the pipeline does with the voice
- The AI writes a separate narration line for every scene (anchor style, short sentences), carrying the same facts as the screen text.
- Gates: narration must be 30-62 words, hook line at most 9 words, no invented numbers, no first-person claims, hook/human/duration checks.
- Acronyms and money are rewritten for the ear (`config/pronunciation.yaml`; add your own words there when you hear a mistake).
- Each scene lasts as long as its narration needs. The beat is ducked under the voice, the voice gets a broadcast-style polish
  (high-pass, gentle compression, presence boost) and the whole mix is normalised to about -14 LUFS.
- The caption automatically says "AI-generated voice." Check Instagram's current rules on labelling AI-generated audio.

## Settings (config/settings.yaml)
  voice: {mode: auto, allow_basic: false, elevenlabs_model: eleven_multilingual_v2, stability: 0.5, style: 0.2, speed: 1.0}
  mode: off  = never use a voice (text + beat only)      mode: on = use any available voice, even basic ones

## Honest limits
- No synthetic voice is truly identical to a human anchor; the best you can do is a very good voice + good writing + good mixing.
  The voice-test workflow is how you judge this for yourself. A recorded human voice is still the most natural option.
- I could not listen to or test these providers from my environment; the request formats were checked against their
  documentation and tested with mocks. Always run voice-test and one dry-run Reel first.
