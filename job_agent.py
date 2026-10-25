import os
import html
import time
import requests
import feedparser
from datetime import datetime, timezone
from google import genai
from supabase import create_client

# Environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TG_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

FEEDS = [
    "https://weworkremotely.com/categories/remote-design-jobs.rss",
    "https://remotive.com/remote-jobs/feed",
    "https://hnrss.org/jobs"
]

# Disguising the bot as a Chrome browser
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def send_telegram(msg, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}
    if reply_markup: payload["reply_markup"] = reply_markup
    try:
        res = requests.post(url, json=payload, timeout=15).json()
        if not res.get("ok"):
            print(f"Telegram error: {res}")
    except Exception as e:
        print(f"Telegram connection error: {e}")

def mark_applied(jid=None, url=None):
    try:
        q = supabase.table("applications").select("id, status")
        if jid: q = q.eq("id", jid)
        elif url: q = q.eq("job_url", url)
        else: return ("not_found", None)

        cur = q.execute().data
        if not cur: return ("not_found", None)
        row = cur[0]
        if row["status"] == "applied": return ("already_applied", row["id"])

        supabase.table("applications").update({
            "status": "applied",
            "applied_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", row["id"]).execute()
        return ("applied", row["id"])
    except Exception as e:
        print(f"Error in mark_applied: {e}")
        return ("error", None)

def scan_jobs():
    print("--- SCANNING JOBS ---")
    for feed_url in FEEDS:
        print(f"Fetching: {feed_url}")
        try:
            feed = feedparser.parse(feed_url, request_headers=HEADERS)
            print(f"Found {len(feed.entries)} entries.")
            if not feed.entries: continue

            for entry in feed.entries[:3]: # Checking top 3 per feed to save API limits
                job_link = getattr(entry, 'link', None)
                job_title = getattr(entry, 'title', None) or 'Untitled Job'
                job_summary = getattr(entry, 'summary', None) or ''
                if not job_link: continue

                print(f"Checking: {job_title}")
                existing = supabase.table("applications").select("id").eq("job_url", job_link).execute()
                if existing.data:
                    print("-> Already in DB. Skipping.")
                    continue

                print("-> Pausing 6s for Gemini...")
                time.sleep(6)

                prompt = f"Analyze this job:\nTitle: {job_title}\nDescription: {job_summary}\nTarget roles: graphic design, website design, social media manager, content specialist, or frontend. If it matches, output a summary and pitch angle. If not, output 'SKIP'."

                try:
                    # UPDATED TO gemini-3.8-flash based on your error log
                    response = client.models.generate_content(
                        model='gemini-3.8-flash', 
                        contents=prompt
                    )
                    result_text = (response.text or "").strip()
                    print(f"-> Gemini says: {result_text[:50]}...")
                except Exception as e:
                    print(f"Gemini error: {e}")
                    continue

                if result_text.upper().startswith("SKIP"):
                    print("-> Skipped.")
                    supabase.table("applications").insert({"job_url": job_link, "title": job_title, "status": "skipped"}).execute()
                    continue

                print("-> MATCH FOUND! Sending Telegram...")
                res = supabase.table("applications").insert({"job_url": job_link, "title": job_title, "status": "found"}).execute()
                if not res.data: continue

                job_id = res.data[0]['id']
                alert = f"🚨 <b>New Job Match Found!</b>\n\n<b>{html.escape(job_title)}</b>\n\n{html.escape(result_text)[:1500]}\n\n<a href='{job_link}'>Open Job Link</a>"
                reply_markup = {"inline_keyboard": [[{"text": "✅ Mark Applied", "callback_data": f"applied:{job_id}"}, {"text": "🔒 Close", "callback_data": f"closed:{job_id}"}]]}
                send_telegram(alert, reply_markup=reply_markup)

        except Exception as e:
            print(f"Feed error: {e}")

def handle_telegram_updates():
    print("--- CHECKING TELEGRAM ---")
    try:
        state = supabase.table("bot_state").select("value").eq("key", "tg_offset").execute()
        offset = int(state.data[0]["value"]) if state.data else 0

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        resp = requests.get(url, params={"offset": offset, "timeout": 0}, timeout=15).json()

        if not resp.get("ok") or not resp.get("result"): return

        updates = resp["result"]
        max_id = max(u["update_id"] for u in updates)

        for update in updates:
            if "callback_query" in update:
                cq = update["callback_query"]
                data = cq.get("data", "")
                if data.startswith("applied:"):
                    jid = data.split(":", 1)[1]
                    supabase.table("applications").update({"status": "applied", "applied_at": datetime.now(timezone.utc).isoformat()}).eq("id", jid).execute()
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cq["id"], "text": "Logged ✓"}, timeout=10)

            message = update.get("message", {})
            text = message.get("text", "")
            chat_id = str(message.get("chat", {}).get("id", ""))
            if chat_id != str(TELEGRAM_CHAT_ID): continue

            if text.startswith("/help"):
                send_telegram("🤖 <b>Job Radar Bot</b>\n\nI will automatically send you jobs here. Just wait for an alert!")
            elif text.startswith("/status"):
                rows = supabase.table("applications").select("title, status").neq("status", "skipped").limit(10).execute().data
                send_telegram(f"Found {len(rows)} jobs in your database.")

        supabase.table("bot_state").upsert({"key": "tg_offset", "value": str(max_id + 1)}).execute()
    except Exception as e:
        print(f"Telegram error: {e}")

if __name__ == "__main__":
    scan_jobs()
    handle_telegram_updates()
    print("Finished!")
