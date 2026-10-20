# START HERE (v1.4 final)

1. Upload these folders/files to your GitHub repo (Add file > Upload files > Commit):
   newsroom, config, .github, assets, tests, docs, requirements.txt
2. Actions > produce > Run workflow. Wait 5-15 minutes.
3. A video arrives in Telegram with Approve / Reject. It now has:
   - voice-over (free AI voice by default; premium if you add ElevenLabs/Google keys, see docs/VOICE.md)
   - animated visuals: counting numbers, bar charts, phone payment, flow diagrams, route maps, check marks
   - an arranged music bed with sound effects placed on what appears on screen
4. Keep DRY_RUN = true until you like what you see for a few days. Then follow docs/SETUP.md Parts 7-9 (Supabase + Instagram).

Switches (config/settings.yaml)
  voice:   {mode: on}          # on | auto (premium only) | off
  video:   {style: kinetic}    # kinetic | classic
  media:   {music_mode: auto}  # your own royalty-free tracks in assets/music/ replace the built-in music
