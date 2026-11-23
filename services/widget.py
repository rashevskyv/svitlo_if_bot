"""Live schedule widget (Bot API 10.4 sendWidget): one shared widget per (region, queue)."""
import hashlib
import json
import logging
import time
from datetime import date, datetime, time as dtime
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest

_LOGGER = logging.getLogger(__name__)

HTML = Path(__file__).with_name("widget.html").read_text(encoding="utf-8")
TPL_HASH = hashlib.md5(HTML.encode()).hexdigest()[:12]
_CODES = {1: "1", 2: "2", 3: "3"}  # API codes: 1 on, 2 off, 3 possible, anything else unknown (0)


def widget_fallback(mode):
    """display_mode "widget:<prev>" -> prev image mode ("dynamic" by default); None if not a widget mode."""
    if not (mode or "").startswith("widget"):
        return None
    prev = mode.partition(":")[2]
    return prev if prev in ("classic", "dynamic", "list") else "dynamic"


def _digits(day: dict) -> str:
    return "".join(_CODES.get(day.get(f"{h:02d}:{m:02d}", 0), "0") for h in range(24) for m in (0, 30))


def kyiv_midnight_ts(date_iso: str) -> int:
    """Epoch of 00:00 on date_iso in server-local time (the bot assumes the server runs in Kyiv time)."""
    return int(datetime.combine(date.fromisoformat(date_iso), dtime.min).timestamp())


def build_widget_data(today: dict, tomorrow: dict, day_start_ts: int, queue_id, rev=None) -> str:
    """public_data JSON: t = midnight epoch, q = queue, s = one digit per half hour (tomorrow only if known)."""
    s, s2 = _digits(today or {}), _digits(tomorrow or {})
    if s2.strip("0"):
        s += s2
    data = {"t": int(day_start_ts), "q": str(queue_id)[:40], "s": s}  # 40-char cap keeps it far below 1024 bytes
    if rev is not None:
        data = {"r": int(rev), **data}
    return json.dumps(data, separators=(",", ":"))


def widget_data(schedule_data: dict, queue_id, rev=None) -> str:
    sched = schedule_data["schedule"]
    today = schedule_data["date_today"]
    return build_widget_data(sched.get(today, {}), sched.get(schedule_data["date_tomorrow"], {}),
                             kyiv_midnight_ts(today), queue_id, rev)


async def _push(bot, row, schedule_data) -> bool:
    """Edit the shared widget data. Returns False when Telegram no longer knows the widget."""
    from database.db import save_widget, delete_widget
    region_id, queue_id, tpl_hash, widget_id, _ = row
    try:
        await bot.edit_shared_widget_data(widget_id=widget_id,
                                          public_data=widget_data(schedule_data, queue_id, int(time.time())))
    except TelegramBadRequest as exc:
        if "WIDGET_ID_INVALID" in str(exc):
            await delete_widget(widget_id)
            return False
        if "not modified" not in str(exc).lower():
            raise
    await save_widget(region_id, queue_id, tpl_hash, widget_id, widget_data(schedule_data, queue_id))
    return True


async def send_widget_schedule(bot, tg_id, region_id, q, schedule_data, caption, silent=False):
    from database.db import get_widget, save_widget, delete_widget
    data = widget_data(schedule_data, q["id"])
    row = await get_widget(region_id, q["id"], TPL_HASH)
    if row and row[4] != data and not await _push(bot, row, schedule_data):
        row = None
    if row:
        try:
            return await bot.send_shared_widget(chat_id=tg_id, widget_id=row[3], caption=caption,
                                                parse_mode="Markdown", disable_notification=silent)
        except TelegramBadRequest as exc:
            if "WIDGET_ID_INVALID" not in str(exc):
                raise
            await delete_widget(row[3])
    msg = await bot.send_widget(chat_id=tg_id, html=HTML, width=360, height=240,
                                public_data=widget_data(schedule_data, q["id"], int(time.time())),
                                is_shared=True, caption=caption, parse_mode="Markdown",
                                disable_notification=silent)
    widget = getattr(msg, "widget", None)
    if widget and widget.id:
        await save_widget(region_id, q["id"], TPL_HASH, widget.id, data)
    return msg


async def refresh_widgets(bot, api_client):
    """Push changed data (and the midnight roll of t) to every stored shared widget, old templates included."""
    from database.db import get_all_widgets
    for row in await get_all_widgets():
        try:
            schedule_data = await api_client.fetch_schedule(row[0], row[1])
            if schedule_data and widget_data(schedule_data, row[1]) != row[4]:
                await _push(bot, row, schedule_data)
        except Exception as exc:  # one bad widget must not stop the update check
            _LOGGER.warning("Widget refresh %s/%s failed: %s", row[0], row[1], exc)
