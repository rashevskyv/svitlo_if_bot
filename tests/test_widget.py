import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import zlib
from datetime import datetime, timedelta
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
        raw = HTML_PATH.read_bytes()
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
