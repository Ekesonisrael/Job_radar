# 🤖 Job Radar

A zero-cost, cloud-native job hunting agent. Runs hourly on GitHub Actions, filters jobs with Gemini, alerts you on Telegram, and tracks applications with follow-up reminders in Supabase.

## Stack
- **GitHub Actions** — free hourly cron execution
- **Python** — feedparser + Gemini SDK + Supabase
- **Gemini API** — free tier job matching
- **Telegram Bot** — alerts + inline buttons
- **Supabase** — application tracking & dedupe

## Setup

### 1. Create Supabase project
1. Go to [supabase.com](https://supabase.com), create a free project
2. Open SQL Editor, paste `schema.sql`, run it
3. Copy **Project URL** (Settings → API) → `SUPABASE_URL`
4. Copy **service_role key** (Settings → API) → `SUPABASE_KEY`

### 2. Create Telegram bot
1. Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`
2. Save the **bot token** → `TG_BOT_TOKEN`
3. Send any message to your new bot
4. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` — copy `chat.id` → `TG_CHAT_ID`

### 3. Get Gemini API key
1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Create API key → `GEMINI_API_KEY`

### 4. Add GitHub Secrets
Repo → Settings → Secrets and variables → Actions → New repository secret:
- `TG_BOT_TOKEN`
- `TG_CHAT_ID`
- `GEMINI_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_KEY`

### 5. Push and trigger
- Push all files to your repo
- Actions tab → **Hourly Job Radar & Commands** → **Run workflow**

## Local test
```bash
export TG_BOT_TOKEN=... TG_CHAT_ID=... GEMINI_API_KEY=... SUPABASE_URL=... SUPABASE_KEY=...
pip install -r requirements.txt
python job_agent.py
