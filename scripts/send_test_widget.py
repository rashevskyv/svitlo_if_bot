"""Manual test: send the live widget for one queue to a test chat.

Usage (env vars only, never .env):  BOT_TOKEN_TEST=... TEST_CHAT_ID=... python scripts/send_test_widget.py [queue]
TEST_CHAT_ID must be your private chat with the test bot: production sendWidget works in private chats only.
The slots come from data/if_schedules.json (first two dates of the queue), shifted to today, so the
countdown is live. Nothing is written to the bot database.
"""
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

from aiogram import Bot  # noqa: E402
from services.widget import HTML, widget_data  # noqa: E402


async def main():
    token, chat_id = os.environ.get("BOT_TOKEN_TEST"), os.environ.get("TEST_CHAT_ID")
    if not token or not chat_id:
        sys.exit("Set BOT_TOKEN_TEST and TEST_CHAT_ID environment variables.")
    queue = sys.argv[1] if len(sys.argv) > 1 else "1.1"
    days = json.loads((ROOT / "data" / "if_schedules.json").read_text(encoding="utf-8"))["schedules"][queue]
    src = [days[d] for d in sorted(days)[:2]]
    today, tomorrow = date.today().isoformat(), (date.today() + timedelta(days=1)).isoformat()
    sd = {"date_today": today, "date_tomorrow": tomorrow,
          "schedule": dict(zip((today, tomorrow), src))}
    bot = Bot(token=token)
    if not hasattr(bot, "send_widget"):
        sys.exit("aiogram has no send_widget: pip install -U \"aiogram>=3.34.0\"")
    try:
        msg = await bot.send_widget(chat_id=int(chat_id), html=HTML, width=360, height=240,
                                    public_data=widget_data(sd, queue, rev=1), is_shared=True,
                                    caption=f"Тестовий віджет, черга {queue}")
        print("Sent message", msg.message_id, "widget id", msg.widget.id if msg.widget else None)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
