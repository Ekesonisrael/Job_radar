import os
import html
from datetime import datetime, timezone
import requests
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID   = os.getenv("TG_CHAT_ID")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
CADENCE = {3: "first nudge", 7: "real follow-up", 14: "final bump"}


def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=15
        )
    except Exception as e:
        print(f"Followup send error: {e}")


def check_followups():
    try:
        now = datetime.now(timezone.utc)
        res = supabase.table("applications") \
            .select("*") \
            .eq("status", "applied") \
            .execute()

        if not res.data:
            return

        for row in res.data:
            if not row.get("applied_at"):
                continue

            # Defensive ISO parsing with Z replacement
            applied_str = row["applied_at"].replace("Z", "+00:00")
            applied = datetime.fromisoformat(applied_str)
            days = (now - applied).days

            # Threshold evaluation: triggers even if a daily run was missed
            for threshold, label in CADENCE.items():
                flag_key = f"followup_{threshold}_sent"
                if days >= threshold and not row.get(flag_key):
                    title = html.escape(row.get('title') or '(untitled)')
                    send_telegram(
                        f"⏰ <b>Follow-up Due ({label})</b>\n\n"
                        f"<b>{title}</b>\n"
                        f"Applied {days} days ago.\n"
                        f"<a href='{row['job_url']}'>Open Job Link</a>"
                    )
                    supabase.table("applications") \
                        .update({flag_key: True}) \
                        .eq("id", row["id"]).execute()
                    break  # One nudge per run per job max
    except Exception as e:
        print(f"Error in check_followups: {e}")


if __name__ == "__main__":
    check_followups()
