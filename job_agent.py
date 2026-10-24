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

print("🚀 Starting Job Radar...")
print(f"Checking Supabase connection: {SUPABASE_URL}")

client = genai.Client(api_key=GEMINI_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

FEEDS = [
    "https://weworkremotely.com/categories/remote-front-end-programming.rss",
    "https://remoteok.com/remote-design-jobs.rss",
    "https://remotive.com/remote-jobs/design/feed"
]

def send_telegram(msg, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        res = requests.post(url, json=payload, timeout=15).json()
        if not res.get("ok"):
            print(f"❌ Telegram send error: {res}")
    except Exception as e:
        print(f"❌ Telegram connection error: {e}")

def mark_applied(jid=None, url=None):
    try:
        q = supabase.table("applications").select("id, status")
        if jid:
            q = q.eq("id", jid)
        elif url:
            q = q.eq("job_url", url)
        else:
            return ("not_found", None)

        cur = q.execute().data
        if not cur:
            return ("not_found", None)

        row = cur[0]
        if row["status"] == "applied":
            return ("already_applied", row["id"])

        supabase.table("applications").update({
            "status": "applied",
            "applied_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", row["id"]).execute()
        return ("applied", row["id"])
    except Exception as e:
        print(f"❌ Error in mark_applied: {e}")
        return ("error", None)

def scan_jobs():
    print("--- 🕵️‍♂️ SCANNING JOBS ---")
    for feed_url in FEEDS:
        print(f"Fetching feed: {feed_url}")
        try:
            feed = feedparser.parse(feed_url)
            print(f"Found {len(feed.entries)} entries in this feed.")
            if not feed.entries:
                continue

            for entry in feed.entries[:5]:  # Only checking top 5 for debug speed
                job_link = getattr(entry, 'link', None)
                job_title = getattr(entry, 'title', None) or 'Untitled Job'
                job_summary = getattr(entry, 'summary', None) or ''

                if not job_link:
                    continue

                print(f"Checking: {job_title}")
                
                existing = supabase.table("applications").select("id").eq("job_url", job_link).execute()
                if existing.data:
                    print(f"-> Already in database. Skipping.")
                    continue

                print("-> New job! Pausing 6 seconds for Gemini API limits...")
                time.sleep(6)

                prompt = f"Analyze this job listing:\nTitle: {job_title}\nDescription: {job_summary}\nTarget roles: graphic design, website design, social media manager, content specialist, or frontend work. If this matches any closely, output a short summary and pitch angle. If not, output exactly 'SKIP'."

                try:
                    response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=prompt,
                    )
                    result_text = (response.text or "").strip()
                    print(f"-> Gemini says: {result_text[:50]}...")
                except Exception as e:
                    print(f"❌ Gemini error for {job_link}: {e}")
                    continue

                if result_text.upper().startswith("SKIP"):
                    print("-> Job skipped by Gemini. Saving to DB to avoid re-checking.")
                    try:
                        supabase.table("applications").insert({
                            "job_url": job_link,
                            "title": job_title,
                            "status": "skipped",
                        }).execute()
                    except Exception as e:
                        print(f"❌ Failed to save skip: {e}")
                    continue

                print("-> MATCH FOUND! Saving to DB and sending Telegram...")
                res = supabase.table("applications").insert({
                    "job_url": job_link,
                    "title": job_title,
                    "status": "found"
                }).execute()

                if not res.data:
                    print(f"❌ Supabase insert failed. Output: {res}")
                    continue

                job_id = res.data[0]['id']
                safe_summary = html.escape(result_text)[:1500]
                safe_title = html.escape(job_title)

                alert = f"🚨 <b>New Job Match Found!</b>\n\n<b>{safe_title}</b>\n\n{safe_summary}\n\n<a href='{job_link}'>Open Job Link</a>"
                reply_markup = {"inline_keyboard": [[{"text": "✅ Mark Applied", "callback_data": f"applied:{job_id}"}, {"text": "🔒 Close", "callback_data": f"closed:{job_id}"}]]}
                send_telegram(alert, reply_markup=reply_markup)

        except Exception as e:
            print(f"❌ Error parsing feed {feed_url}: {e}")

def handle_telegram_updates():
    print("--- 📱 CHECKING TELEGRAM COMMANDS ---")
    try:
        state = supabase.table("bot_state").select("value").eq("key", "tg_offset").execute()
        offset = int(state.data[0]["value"]) if state.data else 0

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        resp = requests.get(url, params={"offset": offset, "timeout": 0}, timeout=15).json()

        if resp.get("error_code") == 409:
            print("❌ Conflict 409: Another instance is polling.")
            return

        if not resp.get("ok") or not resp.get("result"):
            print("No new Telegram messages found.")
            return

        updates = resp["result"]
        print(f"Found {len(updates)} new messages from Telegram!")
        max_id = max(u["update_id"] for u in updates)

        for update in updates:
            message = update.get("message", {})
            text = message.get("text", "")
            chat_id = str(message.get("chat", {}).get("id", ""))

            if chat_id != str(TELEGRAM_CHAT_ID):
                continue

            print(f"Received command: {text}")

            if text.startswith("/help"):
                send_telegram("🤖 <b>Job Radar Bot Help</b>\n\n• Tap <b>✅ Mark Applied</b> or <b>🔒 Close</b> on job cards.\n• <code>/status</code> - View your last 10 active applications.")
            elif text.startswith("/status"):
                rows = supabase.table("applications").select("title, status").neq("status", "skipped").limit(10).execute().data
                send_telegram("📋 Database connected! Check Supabase to see your rows." if not rows else f"Found {len(rows)} jobs in your database.")

        supabase.table("bot_state").upsert({"key": "tg_offset", "value": str(max_id + 1)}).execute()
        print("-> Saved Telegram offset to database.")

    except Exception as e:
        print(f"❌ Error in handle_telegram_updates: {e}")

if __name__ == "__main__":
    scan_jobs()
    handle_telegram_updates()
    print("🏁 Finished!")
                                                              
