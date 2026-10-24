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

# Multi-feed support (easily expandable)
FEEDS = [
    "https://weworkremotely.com/categories/remote-front-end-programming.rss",
    # Add more free RSS feeds here as needed:
    # "https://remoteok.com/remote-design-jobs.rss",
    # "https://remotive.com/remote-jobs/design/feed",
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
            print(f"Telegram send error: {res}")
    except Exception as e:
        print(f"Telegram connection error: {e}")


def mark_applied(jid=None, url=None):
    """Shared helper to prevent double-tap resetting applied_at clock."""
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
        print(f"Error in mark_applied: {e}")
        return ("error", None)


def scan_jobs():
    for feed_url in FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                continue

            for entry in feed.entries[:15]:
                job_link = getattr(entry, 'link', None)
                job_title = getattr(entry, 'title', None) or 'Untitled Job'
                job_summary = getattr(entry, 'summary', None) or ''

                if not job_link:
                    continue

                # Check if already processed (found, applied, closed, or skipped)
                existing = supabase.table("applications").select("id").eq("job_url", job_link).execute()
                if existing.data:
                    continue

                # Throttle to stay safely under Gemini free tier (10 RPM)
                time.sleep(6)

                prompt = f"""
                Analyze this job listing:
                Title: {job_title}
                Description: {job_summary}

                Target roles: graphic design, website design, social media manager, content specialist, or frontend work.
                If it matches closely, output a short summary explaining why it's a good match and a suggested pitch angle.
                If it does not match, output exactly 'SKIP'.
                """

                try:
                    response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=prompt,
                    )
                    result_text = (response.text or "").strip()
                except Exception as e:
                    print(f"Gemini error for {job_link}: {e}")
                    continue

                # Cache skips so we never re-analyze rejected jobs
                if result_text.upper().startswith("SKIP"):
                    try:
                        supabase.table("applications").insert({
                            "job_url": job_link,
                            "title": job_title,
                            "status": "skipped",
                        }).execute()
                    except Exception:
                        pass
                    continue

                # Save matching job to Supabase
                res = supabase.table("applications").insert({
                    "job_url": job_link,
                    "title": job_title,
                    "status": "found"
                }).execute()

                if not res.data:
                    continue

                job_id = res.data[0]['id']
                safe_summary = html.escape(result_text)[:1500]
                safe_title = html.escape(job_title)

                alert = (
                    f"🚨 <b>New Job Match Found!</b>\n\n"
                    f"<b>{safe_title}</b>\n\n"
                    f"{safe_summary}\n\n"
                    f"<a href='{job_link}'>Open Job Link</a>"
                )

                # Inline buttons for applied & close actions
                reply_markup = {
                    "inline_keyboard": [[
                        {"text": "✅ Mark Applied", "callback_data": f"applied:{job_id}"},
                        {"text": "🔒 Close", "callback_data": f"closed:{job_id}"}
                    ]]
                }

                send_telegram(alert, reply_markup=reply_markup)

        except Exception as e:
            # Silent per-feed failure — check Actions logs if alerts stop
            print(f"Error parsing feed {feed_url}: {e}")


def handle_telegram_updates():
    try:
        state = supabase.table("bot_state").select("value").eq("key", "tg_offset").execute()
        offset = int(state.data[0]["value"]) if state.data else 0

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        resp = requests.get(url, params={"offset": offset, "timeout": 0}, timeout=15).json()

        if resp.get("error_code") == 409:
            print("Conflict 409: Another instance is polling.")
            return

        if not resp.get("ok") or not resp.get("result"):
            return

        updates = resp["result"]
        max_id = max(u["update_id"] for u in updates)

        for update in updates:
            # Handle Inline Button Taps (Callback Queries)
            if "callback_query" in update:
                cq = update["callback_query"]

                # Security check: verify chat ID on callback message
                chat_id_from_cb = str(cq.get("message", {}).get("chat", {}).get("id", ""))
                if chat_id_from_cb and chat_id_from_cb != str(TELEGRAM_CHAT_ID):
                    continue

                data = cq.get("data", "")

                if data.startswith("applied:"):
                    jid = data.split(":", 1)[1]
                    status_res, _ = mark_applied(jid=jid)

                    if status_res == "applied":
                        requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                            json={"callback_query_id": cq["id"], "text": "Logged ✓ Applied successfully!"},
                            timeout=10
                        )
                        send_telegram(f"✅ Application logged for job ID: <code>{jid}</code>")
                    elif status_res == "already_applied":
                        requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                            json={"callback_query_id": cq["id"], "text": "Already marked applied ✓"},
                            timeout=10
                        )
                    else:
                        requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                            json={"callback_query_id": cq["id"], "text": "⚠️ Job not found in database."},
                            timeout=10
                        )

                elif data.startswith("closed:"):
                    jid = data.split(":", 1)[1]
                    res = supabase.table("applications").update({"status": "closed"}).eq("id", jid).execute()
                    if res.data:
                        requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                            json={"callback_query_id": cq["id"], "text": "Thread closed 🔒"},
                            timeout=10
                        )
                        send_telegram(f"🔒 Closed job thread ID: <code>{jid}</code>")
                    else:
                        requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
                            json={"callback_query_id": cq["id"], "text": "⚠️ Failed to close."},
                            timeout=10
                        )

            # Handle Text Commands
            message = update.get("message", {})
            text = message.get("text", "")
            chat_id = str(message.get("chat", {}).get("id", ""))

            if chat_id != str(TELEGRAM_CHAT_ID):
                continue

            if text.startswith("/help"):
                help_text = (
                    "🤖 <b>Job Radar Bot Help</b>\n\n"
                    "• Tap <b>✅ Mark Applied</b> or <b>🔒 Close</b> on job cards.\n"
                    "• <code>/status</code> - View your last 10 active applications.\n"
                    "• <code>/applied [url]</code> - Manually log an application.\n"
                    "• <code>/closed [url]</code> - Mark a job thread as closed.\n"
                    "• <code>/help</code> - Show this menu."
                )
                send_telegram(help_text)

            elif text.startswith("/status"):
                rows = supabase.table("applications") \
                    .select("title, status, applied_at") \
                    .neq("status", "skipped") \
                    .order("applied_at", desc=True, nullsfirst=False) \
                    .limit(10).execute().data

                if not rows:
                    send_telegram("📭 No applications or active leads found yet.")
                else:
                    msg = "📋 <b>Recent Activity & Applications:</b>\n\n"
                    for r in rows:
                        when = r.get("applied_at")
                        when_str = when[:10] if when else "—"
                        title = html.escape(r.get('title') or '(untitled)')
                        msg += f"• <b>{title}</b>\n  {when_str} · <code>{r['status']}</code>\n\n"
                    send_telegram(msg)

            elif text.startswith("/applied"):
                parts = text.split(" ", 1)
                if len(parts) < 2 or not parts[1].strip():
                    send_telegram("⚠️ Usage: <code>/applied https://job-url.com</code>")
                    continue
                url_to_mark = parts[1].strip()
                status_res, _ = mark_applied(url=url_to_mark)

                if status_res == "applied":
                    send_telegram(f"✅ Marked as applied: {url_to_mark}")
                elif status_res == "already_applied":
                    send_telegram("ℹ️ This job was already marked as applied.")
                elif status_res == "not_found":
                    send_telegram("⚠️ No matching job found. Copy the URL directly from the alert link.")
                else:
                    send_telegram("⚠️ Database error — try again in a moment.")

            elif text.startswith("/closed"):
                parts = text.split(" ", 1)
                if len(parts) < 2 or not parts[1].strip():
                    send_telegram("⚠️ Usage: <code>/closed https://job-url.com</code>")
                    continue
                url_to_close = parts[1].strip()
                res = supabase.table("applications").update({"status": "closed"}).eq("job_url", url_to_close).execute()
                if res.data:
                    send_telegram(f"🔒 Closed job thread: {url_to_close}")
                else:
                    send_telegram("⚠️ No matching job found with that URL.")

        supabase.table("bot_state").upsert({"key": "tg_offset", "value": str(max_id + 1)}).execute()

    except Exception as e:
        print(f"Error in handle_telegram_updates: {e}")


if __name__ == "__main__":
    scan_jobs()
    handle_telegram_updates()
