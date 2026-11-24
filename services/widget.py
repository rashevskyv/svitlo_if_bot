"""Live schedule widget (Bot API 10.4 sendWidget): one shared widget per (region, queue)."""
import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
from urllib.parse import urlencode
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest

_LOGGER = logging.getLogger(__name__)

# Comment lines are dropped before sending: Telegram allows 4096 bytes after zlib.
HTML = re.sub(r"(?m)^[ \t]*//.*\n", "", Path(__file__).with_name("widget.html").read_text(encoding="utf-8"))
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


_SUBS = {}  # (region, queue) -> set of asyncio.Queue, one per open web page
_MAX_PAGES = 500
_PING = 30  # seconds; a closed page is noticed and dropped at the next ping


async def notify_pages(api_client):
    """Push fresh data to open web pages; each page drops the event when its data did not change."""
    for (r, q), queues in list(_SUBS.items()):
        try:
            sd = await api_client.fetch_schedule(r, q)
        except Exception as exc:  # one bad queue must not stop the update check
            _LOGGER.warning("Page notify %s/%s failed: %s", r, q, exc)
            continue
        if sd:
            d = widget_data(sd, q)
            for queue in list(queues):
                queue.put_nowait(d)


async def notify_push(api_client):
    """FCM push to Android widgets of every queue a phone asked for (/w/sub), only when the data changed."""
    from database.db import get_push_topics, save_push_topic
    from services import fcm
    for r, q, last in await get_push_topics():
        try:
            sd = await api_client.fetch_schedule(r, q)
            d = widget_data(sd, q) if sd else None
            if d and d != last and await fcm.send(r, q, d):
                await notify_schedule_change(r, q, last, d)
                await save_push_topic(r, q, d)
        except Exception as exc:  # one bad queue must not stop the update check
            _LOGGER.warning("FCM push %s/%s failed: %s", r, q, exc)


def schedule_changed(old: str, new: str) -> bool:
    """True when the known schedule really changed; the midnight roll of t alone is not a change."""
    try:
        a, b = json.loads(old), json.loads(new)
    except (TypeError, ValueError):
        return False  # first data for this queue: nothing to compare with
    shift = round((b["t"] - a["t"]) / 86400) * 48  # whole days; DST days are 23/25 h
    x, y = (a["s"][shift:] if shift >= 0 else "0" * -shift + a["s"]), b["s"]
    n = max(len(x), len(y))
    return x.ljust(n, "0") != y.ljust(n, "0")  # missing = unknown ("0")


def events(d: str, now: float):
    """[(kind, epoch)] of the first power-off start and the first power-on start after now.
    Same rules as reminder_service: "possible" counts as off; "on" only directly after off/possible."""
    data = json.loads(d)
    s, base, out = data["s"], datetime.fromtimestamp(data["t"]), {}
    for j, c in enumerate(s):
        p = s[j - 1] if j else ""
        kind = "off" if c in "23" and (j == 0 or p not in "23") else "on" if c == "1" and p in ("2", "3") else None
        if kind and kind not in out:
            ts = (base + timedelta(minutes=30 * j)).timestamp()  # wall-clock slot -> epoch, DST-safe
            if ts > now:
                out[kind] = ts
    return sorted(((k, int(v)) for k, v in out.items()), key=lambda e: e[1])


def _next_text(d, now):
    ev = events(d, now)
    if not ev:
        return "Змін не заплановано."
    k, ts = ev[0]
    return f"Наступне {'вимкнення' if k == 'off' else 'увімкнення'} о {datetime.fromtimestamp(ts):%H:%M}."


async def _notify(token, key, ts, title, body):
    """Send once per key (dedup survives a restart); a dead token loses its settings."""
    from database.db import mark_push_sent, delete_push_prefs
    from services import fcm
    # ponytail: marked before sending, so a network error loses this one notification instead of sending it twice.
    if not await mark_push_sent(key, ts):
        return
    try:
        await fcm.notify(token, title, body)
    except fcm.Unregistered:
        await delete_push_prefs(token)


async def notify_before(api_client, now=None):
    """Every minute: "power off / on in N minutes" for phones that asked for it (/w/prefs)."""
    from database.db import get_push_prefs
    now = now or time.time()
    by_queue = {}
    for p in await get_push_prefs():
        if p[4] or p[5]:
            by_queue.setdefault((p[1], p[2]), []).append(p)
    for (r, q), prefs in by_queue.items():
        try:
            sd = await api_client.fetch_schedule(r, q)
            if not sd:
                continue
            for kind, ts in events(widget_data(sd, q), now):
                left = math.ceil((ts - now) / 60)
                for token, *_, off_min, on_min in prefs:
                    n = off_min if kind == "off" else on_min
                    if n and left <= n:
                        word = "Вимкнення" if kind == "off" else "Увімкнення"
                        await _notify(token, f"{token}|{kind}|{ts}", ts,
                                      f"{'🔴' if kind == 'off' else '🟢'} {word} світла через {left} хв",
                                      f"Черга {q}: о {datetime.fromtimestamp(ts):%H:%M} за графіком.")
        except Exception as exc:  # one bad queue must not stop the others
            _LOGGER.warning("Push reminder %s/%s failed: %s", r, q, exc)


async def notify_schedule_change(r, q, old, new):
    """"Графік оновлено" for phones of this queue with the setting on."""
    from database.db import get_push_prefs
    if not schedule_changed(old, new):
        return
    h = hashlib.md5(new.encode()).hexdigest()[:12]
    for token, pr, pq, sched, *_ in await get_push_prefs():
        if sched and (pr, pq) == (r, q):
            await _notify(token, f"{token}|sched|{h}", int(time.time()),
                          "📅 Графік оновлено", f"Черга {q}. {_next_text(new, time.time())}")


def is_preview(tg_id) -> bool:
    """The Telegram live widget mode is shown only to PREVIEW_USERS (comma-separated ids); the web link and the Android app are for everyone."""
    return str(tg_id) in {x for x in os.getenv("PREVIEW_USERS", "").replace(" ", "").split(",") if x}


ANDROID_BUTTON = "📲 Віджет для Android"
# Telegram HTML: <b> = headings and buttons to tap, <i> = text the phone shows and menu paths, <code> = names to type or find.
ANDROID_GUIDE = """<b>📲 Віджет «Світло» для Android</b>

Графік відключень на головному екрані телефона. Потрібно: <b>Android 8</b> або новіший і <b>сервіси Google Play</b>.
Застосунку немає в Play Market, тому його ставлять з файлу APK. Android попередить про це. Це нормально для будь-якого APK.

<b>1️⃣ Завантажте файл</b>
Натисніть кнопку <b>⬇️ Завантажити APK</b> під цим повідомленням.
Браузер може написати <i>«Файл може бути шкідливим»</i>. Натисніть <b>Усе одно завантажити</b>. Так він пише про кожен APK.

<b>2️⃣ Дозвольте встановлення</b>
Відкрийте файл <code>svitlo-widget.apk</code>. Android напише, що встановлювати застосунки з цього джерела заборонено.
• Натисніть <b>Налаштування</b>.
• Увімкніть <b>Дозволити з цього джерела</b>.
• Натисніть <b>Назад</b>.
Якщо вікна немає, увімкніть дозвіл вручну:
• Більшість телефонів: <i>Налаштування → Застосунки → Спеціальний доступ → Встановлення невідомих застосунків → Chrome</i> → <b>Дозволити</b>.
• Samsung: <i>Налаштування → Програми → ⋮ угорі → Спеціальний доступ → Встановлення невідомих програм → Chrome</i> → <b>увімкнути</b>.
• Xiaomi, Redmi, POCO: <i>Налаштування → Захист конфіденційності → Спеціальні дозволи → Встановлення невідомих застосунків → Chrome</i> → <b>Дозволити</b>.
Назви пунктів можуть трохи відрізнятися. Найпростіше: введіть <code>невідом</code> у пошуку налаштувань.

<b>3️⃣ Встановіть</b>
Натисніть <b>Встановити</b>.
Може з'явитися вікно Google Play Protect: <i>«Невідомий застосунок»</i> або <i>«Заблоковано»</i>. Натисніть <b>Докладніше</b>, потім <b>Усе одно встановити</b>.
На Xiaomi система перевіряє файл кілька секунд. Зачекайте і натисніть <b>Встановити</b>.

<b>4️⃣ Підключіть свою чергу</b>
• Запитайте в боті графік своєї черги: <b>📊 Поточний статус</b>.
• Під графіком є посилання <b>🔗 Живий графік у браузері</b>. Натисніть і утримуйте його. Виберіть <b>Копіювати посилання</b>.
• Відкрийте застосунок <code>Світло</code> (іконка з лампочкою).
• Вставте посилання в поле і натисніть <b>Зберегти</b>.

<b>5️⃣ Додайте віджет на екран</b>
• Натисніть і утримуйте пусте місце на головному екрані.
• Виберіть <b>Віджети</b>, знайдіть <code>Світло</code> і перетягніть його на екран.
• Найкращий розмір: <b>4 × 2</b> клітинки.

<b>6️⃣ Щоб віджет не «засинав»</b> (Xiaomi, Samsung, Huawei, Honor)
<i>Налаштування → Застосунки → Світло → Батарея</i> → <b>Без обмежень</b>. На Xiaomi також увімкніть <b>Автозапуск</b>.

<b>🔄 Оновлення</b>
Коли вийде нова версія, на віджеті з'явиться <i>«⬆ Є оновлення — торкніться»</i>. Торкніться, потім натисніть <b>⬆ Оновити застосунок</b>. Дозвольте встановлення для застосунку <code>Світло</code>, як у кроці 2.

<b>❓ Чому Android попереджає</b>
Застосунок не з Play Market, тому Google не може його перевірити. Застосунок має лише два дозволи: інтернет і встановлення оновлень. Він <b>не читає</b> контакти, файли, SMS і геолокацію."""


def android_guide():
    """Install guide for the Android widget app + a download button (None when WEB_BASE_URL is not set)."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    base = os.getenv("WEB_BASE_URL", "").rstrip("/")
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="⬇️ Завантажити APK", url=f"{base}/w/app/svitlo-widget.apk")]]) if base else None
    return ANDROID_GUIDE, markup


def page_url(region_id, queue_id, tg_id=None):
    """Link to the browser version of the widget (also used by the Android app); None when WEB_BASE_URL is not set."""
    base = os.getenv("WEB_BASE_URL", "").rstrip("/")
    if not base:
        return None
    return f"{base}/w?{urlencode({'r': region_id, 'q': queue_id})}"


async def start_web(api_client):
    """Serve the widget as a web page: /w?r=<region>&q=<queue>. The page re-reads /w/data every minute."""
    from aiohttp import web
    from services.api_client import REGIONS

    async def load(request):
        r, q = request.query.get("r", ""), request.query.get("q", "")
        if r not in REGIONS or not re.fullmatch(r"[\w.\-]{1,20}", q):
            raise web.HTTPNotFound()
        sd = await api_client.fetch_schedule(r, q)
        if not sd:
            raise web.HTTPNotFound()
        return widget_data(sd, q), r, q

    async def data(request):
        return web.Response(text=(await load(request))[0], content_type="application/json")

    async def events(request):
        """Server-Sent Events: current data at once, then only when the bot sees a change. Comments keep it alive."""
        if sum(map(len, _SUBS.values())) >= _MAX_PAGES:
            raise web.HTTPServiceUnavailable()
        d, r, q = await load(request)
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store"})
        await resp.prepare(request)
        queue = asyncio.Queue()
        _SUBS.setdefault((r, q), set()).add(queue)
        last = None
        try:
            while True:
                if d != last:
                    await resp.write(f"data: {d}\n\n".encode())
                    last = d
                try:
                    d = await asyncio.wait_for(queue.get(), _PING)
                except asyncio.TimeoutError:
                    await resp.write(b": ping\n\n")
        except ConnectionResetError:
            pass
        finally:
            subs = _SUBS.get((r, q), set())
            subs.discard(queue)
            if not subs:
                _SUBS.pop((r, q), None)
        return resp

    async def sub(request):
        """The Android app registers its queue: from now on the bot pushes it by FCM. Returns the current data."""
        from database.db import save_push_topic
        d, r, q = await load(request)
        await save_push_topic(r, q, d, replace=False)
        return web.Response(text=d, content_type="application/json")

    async def prefs(request):
        """The Android app saves its notification settings: token, sched=0/1, off/on = minutes before (0 = off)."""
        from database.db import save_push_prefs
        _, r, q = await load(request)
        g = request.query.get
        token = g("token", "")
        try:
            off, on = (max(0, min(int(g(k, "0")), 180)) for k in ("off", "on"))
        except ValueError:
            raise web.HTTPBadRequest()
        if not re.fullmatch(r"[\w:\-]{20,400}", token):
            raise web.HTTPBadRequest()
        await save_push_prefs(token, r, q, g("sched") == "1", off, on)
        return web.Response(text="ok")

    async def page(request):
        d, r, q = await load(request)
        shim = ("window.TelegramWidget={publicData:" + json.dumps(d).replace("<", "\\u003c") + "};"
                "new EventSource('w/events?" + urlencode({"r": r, "q": q}) + "').onmessage="
                "e=>{TelegramWidget.publicData=e.data};")
        html = HTML.replace('<meta charset="utf-8">', '<meta charset="utf-8"><meta name="viewport" content="width=370">'
                            '<title>Світло: черга ' + q + '</title>', 1).replace("<script>", "<script>" + shim, 1)
        return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})

    app = web.Application()
    app.add_routes([web.get("/w", page), web.get("/w/data", data), web.get("/w/events", events),
                    web.get("/w/sub", sub), web.get("/w/prefs", prefs)])
    # Android self-update: APP_DIR holds svitlo-widget.apk and version.txt (its versionCode).
    app_dir = Path(os.getenv("APP_DIR", Path(__file__).resolve().parent.parent / "app"))
    app_dir.mkdir(exist_ok=True)
    app.router.add_static("/w/app/", app_dir)
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=1)  # open event streams must not delay a restart
    await runner.setup()
    port = int(os.getenv("WEB_PORT", "8080"))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    _LOGGER.info("Widget web page on port %s", port)
    return runner


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
