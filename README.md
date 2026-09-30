# ZeroCost Shorts


Fully automated pipeline: Groq script → Edge TTS voice → AI images → captions → FFmpeg → YouTube upload — running daily via GitHub Actions.





Sample video from the channel: https://youtube.com/shorts/HrYHpO7QymI

<table>
  <tr>
    <td>
      <video src="https://github.com/user-attachments/assets/e8a4c716-ef53-46e6-97fb-60cbfa6c4059" width="400" controls></video>
    </td>
    <td>
      <video src="https://github.com/user-attachments/assets/9b24423a-87f7-45cf-8d00-0be7bb0df3d6" width="400" controls></video>
    </td>
  </tr>
</table>

Analytics of one of the channels — you can create hundreds like this
<img width="807" height="548" alt="Screenshot 2026-06-11 at 12 20 29 PM" src="https://github.com/user-attachments/assets/09f2ed03-aa1e-43bc-bd0c-21c0df3a958e" />


## Architecture

```
scripts/run_short.py          ← orchestrator
  pipeline/groq_script.py     ← Groq LLM writes the Short script (JSON)
  pipeline/edge_tts_synth.py  ← Edge TTS: free voice, per-segment MP3
  pipeline/images.py          ← HuggingFace image gen (Pollinations fallback)
  pipeline/captions.py        ← SRT from segment timings
  pipeline/render_short.py    ← FFmpeg: slideshow + audio + burned captions
  pipeline/youtube_upload.py  ← YouTube Data API v3 upload
  pipeline/channel_presets.py ← niche presets (ghost_stories, facts, etc.)
```

## Quick Start (local)

```bash
git clone <this-repo>
cd Youtube_bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Copy and fill in your keys:
cp .env.example .env
# edit .env → add GROQ_API_KEY, HF_TOKEN

# Install ffmpeg if needed:
brew install ffmpeg         # macOS
# sudo apt install ffmpeg   # Ubuntu

# Generate a ghost story Short (no upload):
.venv/bin/python scripts/run_short.py --channel ghost_stories

# Output → output/runs/ghost_stories_<timestamp>/short.mp4
```

## Free API Keys You Need

| Service | Free? | What for | Get it here |
|---------|-------|----------|-------------|
| **Groq** | ✅ free tier | Script generation (LLM) | https://console.groq.com/keys |
| **HuggingFace** | ✅ free tier | Image generation | https://huggingface.co/settings/tokens (select "Inference Providers" permission) |
| **Edge TTS** | ✅ 100% free | Voice / audio | No key needed — uses Microsoft Edge voices |
| **FFmpeg** | ✅ open source | Video render | `brew install ffmpeg` or `apt install ffmpeg` |
| **YouTube API** | ✅ free | Upload | Google Cloud Console (see below) |

## YouTube Upload Setup (one-time)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or pick existing)
3. Enable **YouTube Data API v3**: [direct link](https://console.cloud.google.com/apis/library/youtube.googleapis.com)
4. Go to **Credentials** → **Create OAuth client ID** → choose **Desktop app**
5. Download the JSON → save as `secrets/client_secret.json`
6. Run the auth helper:

```bash
.venv/bin/python scripts/youtube_auth.py
```

7. Browser opens → sign in → approve → token saved to `secrets/youtube_token.json`
8. Now you can upload:

```bash
.venv/bin/python scripts/run_short.py --channel ghost_stories --upload --privacy private
```

## GitHub Actions (daily autopilot)

### Add these secrets to your repo:

Go to **repo → Settings → Secrets and variables → Actions → New repository secret**:

| Secret name | Value |
|-------------|-------|
| `GROQ_API_KEY` | Your Groq key |
| `HF_TOKEN` | Your HuggingFace token |
| `YT_CLIENT_ID` | Printed by `youtube_auth.py` |
| `YT_CLIENT_SECRET_VALUE` | Printed by `youtube_auth.py` |
| `YT_REFRESH_TOKEN` | Printed by `youtube_auth.py` |

### Trigger

- **Automatic**: runs daily at 10:00 UTC (edit cron in `.github/workflows/daily_short.yml`)
- **Manual**: Actions tab → "Daily YouTube Short" → Run workflow → pick channel + topic

### Change channel / schedule

Edit `.github/workflows/daily_short.yml`:
- Change `cron: "0 10 * * *"` to your preferred time ([crontab.guru](https://crontab.guru/))
- Change default channel in the `CHANNEL=` line

## Channel Presets

| `--channel` | Niche |
|-------------|-------|
| `ghost_stories` | Spooky / horror storytime |
| `facts` | Interesting facts / trivia |
| `school_story` | School drama / storytime |
| `psych_tradeoff` | Psychology / habits |
| `history_micro` | One moment in history |

Add your own in `pipeline/channel_presets.py`.

## Voices

Edge TTS has dozens of free voices. List them:

```bash
.venv/bin/python -m edge_tts --list-voices | grep en-US
```

Set in `.env`:
```
EDGE_TTS_VOICE=en-US-GuyNeural        # casual male
EDGE_TTS_VOICE=en-US-JennyNeural      # female
EDGE_TTS_VOICE=en-US-ChristopherNeural # deep male (default)
```

## Multi-Channel

For multiple channels: duplicate the workflow YAML, use different `CHANNEL=` defaults and separate `YT_*` secrets per channel (e.g. `YT_REFRESH_TOKEN_FACTS`, `YT_REFRESH_TOKEN_GHOST`). Each channel needs its own YouTube OAuth.

## Cost

| Component | Cost |
|-----------|------|
| Groq | Free tier (~$0) |
| HuggingFace images | Free tier (~$0) |
| Edge TTS | $0 |
| FFmpeg | $0 |
| GitHub Actions | 2000 min/month free |
| YouTube upload | $0 |
| **Total** | **~$0/day** |


