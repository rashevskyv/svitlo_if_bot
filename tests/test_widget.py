import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest

from database import db
from services import widget

ROOT = Path(__file__).resolve().parent.parent
HTML_PATH = ROOT / "services" / "widget.html"
T = 1795384800  # any midnight epoch; the JS functions only use offsets from it


def n(h, m=0, s=0, ms=0):
    return T * 1000 + ((h * 60 + m) * 60 + s) * 1000 + ms


def utc(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


SPRING = utc(2026, 3, 28, 22) // 1000  # Kyiv midnight of 2026-03-29
FALL = utc(2026, 10, 24, 21) // 1000  # Kyiv midnight of 2026-10-25


def run_js(exprs):
    """Evaluate JS expressions against the PURE block of widget.html in one node process."""
    if not shutil.which("node"):
        raise unittest.SkipTest("node is not installed")
    html = HTML_PATH.read_text(encoding="utf-8")
    block = html.split("// PURE-BEGIN", 1)[1].split("// PURE-END", 1)[0]
    script = "//" + block + "\nconsole.log(JSON.stringify([" + ",\n".join(exprs) + "]));\n"
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "pure.js")
        Path(path).write_text(script, encoding="utf-8")
        out = subprocess.run(["node", path], capture_output=True, encoding="utf-8", check=True).stdout
    return json.loads(out)


def day(codes):
    return {f"{h:02d}:{m:02d}": codes[h * 2 + (m // 30)] for h in range(24) for m in (0, 30)}


class PythonTests(unittest.TestCase):
    def test_template_size(self):
        raw = widget.HTML.encode()  # what Telegram gets: comment lines removed
        self.assertLessEqual(len(raw), 32768)
        self.assertLessEqual(len(zlib.compress(raw, 9)), 3800)  # server limit 4096, keep a margin

    def test_build_widget_data(self):
        today = day([1, 2, 3, 0, 7] + [1] * 43)
        d = json.loads(widget.build_widget_data(today, {}, T, "1.1"))
        self.assertEqual(d, {"t": T, "q": "1.1", "s": "12300" + "1" * 43})
        d = json.loads(widget.build_widget_data(today, day([0] * 48), T, "1.1", rev=5))
        self.assertEqual((d["r"], len(d["s"])), (5, 48))  # empty tomorrow is dropped
        d = json.loads(widget.build_widget_data(today, day([2] * 48), T, "1.1"))
        self.assertEqual(d["s"][48:], "2" * 48)
        long = widget.build_widget_data(today, day([2] * 48), T, "ї" * 5000, rev=2**40)
        self.assertLessEqual(len(long.encode()), 1024)

    def test_widget_fallback(self):
        f = widget.widget_fallback
        self.assertEqual([f("widget:classic"), f("widget:list"), f("widget"), f("widget:junk")],
                         ["classic", "list", "dynamic", "dynamic"])
        self.assertEqual([f("classic"), f("dynamic"), f(None)], [None, None, None])

    def test_kyiv_midnight_ts(self):
        t = widget.kyiv_midnight_ts("2026-11-23")
        self.assertEqual(datetime.fromtimestamp(t), datetime(2026, 11, 23))

    @unittest.skipUnless((ROOT / "data" / "if_schedules.json").exists(), "data/if_schedules.json is absent")
    def test_payload_from_data_file(self):
        src = json.loads((ROOT / "data" / "if_schedules.json").read_text(encoding="utf-8"))  # read only
        days = src["schedules"]["1.1"]
        dates = sorted(days)
        sd = {"date_today": dates[0], "date_tomorrow": (datetime.fromisoformat(dates[0]) + timedelta(days=1)).date().isoformat(),
              "schedule": days}
        payload = widget.widget_data(sd, "1.1", rev=1)
        d = json.loads(payload)
        self.assertLessEqual(len(payload.encode()), 1024)
        self.assertEqual(d["t"], widget.kyiv_midnight_ts(dates[0]))
        self.assertIn(len(d["s"]), (48, 96))
        self.assertEqual(d["s"][:48], "".join(str(days[dates[0]].get(f"{h:02d}:{m:02d}", 0)) for h in range(24) for m in (0, 30)))


class WebPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_and_push(self):
        import asyncio
        import time
        import aiohttp
        sched = {"date_today": "2026-11-23", "date_tomorrow": "2026-11-24", "schedule": {"2026-11-23": day([1] * 48)}}
        api = SimpleNamespace(fetch_schedule=AsyncMock(side_effect=lambda r, q: sched))
        app_dir = tempfile.TemporaryDirectory()
        self.addCleanup(app_dir.cleanup)
        with patch.dict(os.environ, {"WEB_PORT": "0", "WEB_BASE_URL": "http://h:1/", "APP_DIR": app_dir.name}):
            self.assertEqual(widget.page_url("ivano-frankivska-oblast", "5.2"), "http://h:1/w?r=ivano-frankivska-oblast&q=5.2")
            runner = await widget.start_web(api)
        base = "http://127.0.0.1:%d/w" % runner.addresses[0][1]
        qs = "?r=ivano-frankivska-oblast&q=5.2"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(base + qs) as r:
                    html = await r.text()
                    self.assertEqual(r.status, 200)
                    self.assertIn("new EventSource('w/events" + qs, html)
                async with s.get(base + "?r=nope&q=5.2") as r:
                    self.assertEqual(r.status, 404)
                async with s.get(base + "/events" + qs) as r:
                    read = lambda: asyncio.wait_for(r.content.readuntil(b"\n\n"), 5)
                    self.assertEqual(await read(), b"data: " + widget.widget_data(sched, "5.2").encode() + b"\n\n")
                    await widget.notify_pages(api)  # unchanged: nothing is sent
                    sched["schedule"]["2026-11-23"] = day([2] * 48)
                    await widget.notify_pages(api)  # changed: pushed
                    self.assertEqual(await read(), b"data: " + widget.widget_data(sched, "5.2").encode() + b"\n\n")
            with patch.object(widget, "_PING", 0.1):
                await widget.notify_pages(api)  # wake the handler so it re-reads _PING
                for _ in range(50):  # the closed page is dropped at its next ping
                    if not widget._SUBS:
                        break
                    await asyncio.sleep(0.1)
            self.assertEqual(widget._SUBS, {})
        finally:
            started = time.monotonic()
            await asyncio.wait_for(runner.cleanup(), 5)
            self.assertLess(time.monotonic() - started, 3)  # a restart is not held up


class PushTests(unittest.IsolatedAsyncioTestCase):
    async def test_sub_and_fcm_push(self):
        import aiohttp
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        with patch.object(db, "DB_PATH", os.path.join(folder.name, "p.db")):
            await db.init_db()
            sched = {"date_today": "2026-11-23", "date_tomorrow": "2026-11-24", "schedule": {"2026-11-23": day([1] * 48)}}
            api = SimpleNamespace(fetch_schedule=AsyncMock(side_effect=lambda r, q: sched))
            app_dir = os.path.join(folder.name, "app")
            with patch.dict(os.environ, {"WEB_PORT": "0", "APP_DIR": app_dir}):
                runner = await widget.start_web(api)
            Path(app_dir, "version.txt").write_text("2")
            try:
                base = "http://127.0.0.1:%d/w/" % runner.addresses[0][1]
                async with aiohttp.ClientSession() as s:
                    async with s.get(base + "sub?r=ivano-frankivska-oblast&q=5.2") as r:
                        self.assertEqual(await r.text(), widget.widget_data(sched, "5.2"))
                    async with s.get(base + "app/version.txt") as r:
                        self.assertEqual(await r.text(), "2")
                    async with s.get(base + "app/../../services/fcm.py") as r:
                        self.assertEqual(r.status, 404)  # no way out of APP_DIR
            finally:
                await runner.cleanup()
            from services import fcm
            self.assertEqual(fcm.topic("ivano-frankivska-oblast", "5.2"), "q_ivano-frankivska-oblast_5.2")
            with patch.object(fcm, "send", AsyncMock(return_value=True)) as send:
                await widget.notify_push(api)  # unchanged since /w/sub: nothing is sent
                send.assert_not_called()
                sched["schedule"]["2026-11-23"] = day([2] * 48)
                await widget.notify_push(api)
                await widget.notify_push(api)  # saved after the first send: sent once
                send.assert_awaited_once_with("ivano-frankivska-oblast", "5.2", widget.widget_data(sched, "5.2"))

    async def test_fcm_jwt(self):
        """The signed JWT verifies with the service account's public key."""
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from services import fcm
        pk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption()).decode()
        key = {"client_email": "a@b", "private_key": pem, "token_uri": "https://t/token"}
        seen = {}

        class Resp:
            status = 200
            async def json(self): return {"access_token": "tok", "expires_in": 3600}
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass

        def post(url, data):
            seen.update(data)
            return Resp()
        with patch.dict(fcm._TOKEN, {"v": None, "exp": 0}):
            self.assertEqual(await fcm._token(SimpleNamespace(post=post), key), "tok")
        h, b, sig = seen["assertion"].split(".")
        pad = lambda x: x + "=" * (-len(x) % 4)
        import base64
        pk.public_key().verify(base64.urlsafe_b64decode(pad(sig)), f"{h}.{b}".encode(), padding.PKCS1v15(), hashes.SHA256())
        self.assertEqual(json.loads(base64.urlsafe_b64decode(pad(b)))["aud"], "https://t/token")


class JsTests(unittest.TestCase):
    def test_pure_functions(self):
        s27 = "'" + "1" * 25 + "22" + "1" * 21 + "'"
        mid = "'" + "1" * 46 + "2222" + "1" * 46 + "'"
        cases = [
            (f"slotIndex({n(12, 29, 59, 999)},{T},48)", 24),
            (f"slotIndex({n(12, 30)},{T},48)", 25),
            (f"slotIndex({n(0)},{T},48)", 0),
            (f"slotIndex({n(23, 59, 59, 999)},{T},48)", 47),
            (f"slotIndex({n(24)},{T},96)", 48),
            (f"slotIndex({n(24)},{T},48)", -1),
            (f"slotIndex({n(0) - 1},{T},48)", -1),
            ("stateAt('12',-1)", 0), ("stateAt('12',1)", 2),
            ("nextChange('1122',0)", 2), ("nextChange('2211',0)", 2), ("nextChange('1133',0)", 2),
            ("nextChange('11002',0)", 4), ("nextChange('1111',0)", -1), ("nextChange('0011',0)", -1),
            ("nextChange('0000',0)", -1), ("nextChange('2011',0)", -1),
            (f"nextChange('{'1' * 48 + '2' * 48}',10)", 48),
            (f"nextChange({s27},slotIndex({n(12, 29, 59, 999)},{T},48))", 25),
            (f"nextChange({s27},slotIndex({n(12, 30)},{T},48))", 27),
            (f"timeLeftMs({n(12, 29, 59, 999)},{T},25)", 1),
            (f"timeLeftMs({n(23)},{T},48)", 3600000),
            (f"runLength({mid},46)", 4), ("runLength('1122',2)", 2),
            ("slotLabel(0)", "00:00"), ("slotLabel(25)", "12:30"), ("slotLabel(47)", "23:30"),
            ("slotLabel(48)", "00:00 завтра"), ("slotLabel(96)", "00:00"),
            ("formatLeft(0)", "0:00:00"), ("formatLeft(999)", "0:00:00"), ("formatLeft(59000)", "0:00:59"),
            ("formatLeft(60000)", "0:01:00"), ("formatLeft(3600000)", "1:00:00"), ("formatLeft(90000000)", "25:00:00"),
            (f"offHours('{'222' + '1' * 45}',0)", 1.5), (f"offHours({mid},1)", 1), (f"offHours('{'1' * 48}',1)", None),
            (f"isStale({n(24)},{T},48)", True), (f"isStale({n(23, 59, 59, 999)},{T},48)", False),
            (f"isStale({n(24)},{T},96)", False), (f"isStale({n(0) - 1},{T},48)", True),
            ("parseData(null,0)", None), ("parseData('{bad',0)", None),
            ("parseData('{\"r\":5,\"t\":1,\"s\":\"1\"}',6)", None),
            ("parseData('{\"r\":5,\"t\":1,\"s\":\"1\"}',5).s", "1"),
            ("parseData('{\"t\":1,\"s\":\"1\"}',0).t", 1),
            # DST: 2026-03-29 (23 h day) and 2026-10-25 (25 h day). Slots follow the Kyiv wall clock.
            (f"kyivOffset({utc(2026, 3, 29, 0, 59)})/36e5", 2), (f"kyivOffset({utc(2026, 3, 29, 1)})/36e5", 3),
            (f"kyivOffset({utc(2026, 10, 25, 0, 59)})/36e5", 3), (f"kyivOffset({utc(2026, 10, 25, 1)})/36e5", 2),
            (f"slotIndex({utc(2026, 3, 29, 9)},{SPRING},48)", 24),  # 12:00 wall
            (f"slotIndex({utc(2026, 10, 25, 10)},{FALL},48)", 24),  # 12:00 wall
            (f"slotIndex({utc(2026, 3, 29, 20, 59)},{SPRING},48)", 47),  # 23:59 wall
            (f"slotIndex({utc(2026, 10, 25, 21, 59)},{FALL},48)", 47),  # 23:59 wall, not stale an hour early
            (f"isStale({utc(2026, 10, 25, 21, 59)},{FALL},48)", False),
            (f"isStale({utc(2026, 3, 29, 21)},{SPRING},48)", True),  # next midnight
            (f"timeLeftMs({utc(2026, 3, 28, 23)},{SPRING},8)/36e5", 2),  # 01:00 -> 04:00 wall is 2 real hours
            (f"timeLeftMs({utc(2026, 10, 24, 22)},{FALL},8)/36e5", 4),  # 01:00 -> 04:00 wall is 4 real hours
            # "Коло (Прогноз)": before the hand = tomorrow, after = today; hand splits its slot (21:45 = 1305).
            (f"faceArcs('{'1' * 48 + '2' * 48}',1305)", [[0, 1305, "2"], [1305, 1440, "1"]]),
            (f"faceArcs('{'1' * 48}',1305)", [[0, 1305, "0"], [1305, 1440, "1"]]),
            (f"faceArcs('{'1' * 48 + '2' * 48}',0)", [[0, 1440, "1"]]),
            # After midnight the old data: day 1 becomes "today", day 2 is unknown.
            (f"faceArcs('{'1' * 48 + '2' * 48}',1470)", [[0, 30, "0"], [30, 1440, "2"]]),
            (f"overlayArcs('{'1' * 48 + '2' * 48}',1305)", [[1305, 1440, "2"]]),
            (f"overlayArcs('{'2' * 48 + '1' * 48}',1305)", [[1305, 1440, "1"]]),
            (f"overlayArcs('{'1' * 48 + '1' * 48}',1305)", []),
            (f"overlayArcs('{'1' * 48}',1305)", []),
        ]
        results = run_js([c[0] for c in cases])
        for (expr, expected), got in zip(cases, results):
            self.assertEqual(got, expected, expr)

    @unittest.skipUnless(find_spec("matplotlib"), "matplotlib is not installed")
    def test_next_change_matches_caption(self):
        from services.image_generator import get_next_event_info
        cases = ["1" * 20 + "2" * 8 + "1" * 20, "1" * 30 + "0" * 4 + "1" * 14, "2" * 40 + "3" * 8, "1" * 48]
        tomorrow = "2" * 4 + "1" * 44
        name = {"1": "on", "2": "off", "3": "possible", "0": "unknown"}
        labels = run_js([f"(j=>j<0?null:slotLabel(j))(nextChange('{s + tomorrow}',20))" for s in cases])
        now = datetime(2026, 11, 23, 10, 15)
        for s, label in zip(cases, labels):
            text = get_next_event_info([name[c] for c in s], [name[c] for c in tomorrow], now)
            m = re.search(r"о \*\*(\d\d:\d\d)( \(завтра\))?", text)
            expected = (m.group(1) + (" завтра" if m.group(2) else "")) if m else None
            self.assertEqual(label, expected, s)


class PreviewTests(unittest.TestCase):
    def test_only_preview_users_get_the_link(self):
        with patch.dict(os.environ, {"WEB_BASE_URL": "http://h:1", "PREVIEW_USERS": "5, 252419732"}):
            self.assertTrue(widget.is_preview(252419732))
            self.assertFalse(widget.is_preview(7))
            self.assertIsNone(widget.page_url("r", "1", 7))
            self.assertEqual(widget.page_url("r", "1", 5), "http://h:1/w?r=r&q=1")
        with patch.dict(os.environ, {"PREVIEW_USERS": ""}):
            self.assertFalse(widget.is_preview(""))  # empty setting: nobody

    def test_android_guide_fits_one_message(self):
        with patch.dict(os.environ, {"WEB_BASE_URL": "http://h:1/"}):
            text, markup = widget.android_guide()
        self.assertLessEqual(len(text), 4096)  # Telegram message limit
        self.assertEqual(markup.inline_keyboard[0][0].url, "http://h:1/w/app/svitlo-widget.apk")


def bad_request(text):
    return TelegramBadRequest(method=None, message=text)


class SendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        p = patch.object(db, "DB_PATH", os.path.join(folder.name, "w.db"))
        p.start()
        self.addCleanup(p.stop)
        await db.init_db()
        today = datetime.now().date()
        self.sd = {"date_today": today.isoformat(), "date_tomorrow": (today + timedelta(days=1)).isoformat(),
                   "schedule": {today.isoformat(): day([1] * 24 + [2] * 24)}}
        self.bot = SimpleNamespace(send_widget=AsyncMock(return_value=SimpleNamespace(widget=SimpleNamespace(id="w1"))),
                                   send_shared_widget=AsyncMock(), edit_shared_widget_data=AsyncMock())
        self.q = {"id": "1.1", "alias": "Home"}

    async def send(self):
        await widget.send_widget_schedule(self.bot, 7, "if", self.q, self.sd, "cap")

    async def test_send_reuse_edit_and_invalid_id(self):
        await self.send()
        self.assertTrue(self.bot.send_widget.await_args.kwargs["is_shared"])
        await self.send()
        self.assertEqual(self.bot.send_shared_widget.await_args.kwargs["widget_id"], "w1")
        self.bot.edit_shared_widget_data.assert_not_awaited()
        self.sd["schedule"][self.sd["date_today"]] = day([2] * 48)
        await self.send()
        self.bot.edit_shared_widget_data.assert_awaited_once()
        self.bot.send_shared_widget.side_effect = bad_request("Bad Request: WIDGET_ID_INVALID")
        await self.send()
        self.assertEqual(self.bot.send_widget.await_count, 2)

    async def test_refresh(self):
        await self.send()
        api = SimpleNamespace(fetch_schedule=AsyncMock(return_value=self.sd))
        await widget.refresh_widgets(self.bot, api)
        self.bot.edit_shared_widget_data.assert_not_awaited()
        self.sd["schedule"][self.sd["date_today"]] = day([2] * 48)
        self.bot.edit_shared_widget_data.side_effect = bad_request("message is not modified")
        await widget.refresh_widgets(self.bot, api)
        row = await db.get_widget("if", "1.1", widget.TPL_HASH)
        self.assertEqual(row[4], widget.widget_data(self.sd, "1.1"))


if __name__ == "__main__":
    unittest.main()
