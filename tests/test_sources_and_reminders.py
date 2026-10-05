import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services import api_client, if_source, reminder_service
from tests.test_api_cache import Response, Session


def sync_data(status=2, timestamp=None):
    return {"updated_at": timestamp or datetime.now(timezone.utc).isoformat(),
            "schedules": {"5.2": {datetime.now().date().isoformat(): {"00:00": status}}}}


class SourceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous_instance = api_client._instance
        api_client._instance = None
        self.client = api_client.SvitloApiClient()
        self.client._if_api_blocked = True
        self.client._cached_data = {"regions": [{"cpu": api_client.IF_REGION_ID, "schedule": {}}]}
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        (self.root / "data").mkdir()

    async def asyncTearDown(self):
        await self.client.stop_refresh()
        api_client._instance = self.previous_instance
        self.folder.cleanup()

    async def test_fresh_gist_is_used_after_stale_local_file(self):
        stale = sync_data(1, (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat())
        (self.root / "data/if_schedules.json").write_text(json.dumps(stale))
        self.client._session = Session(Response(sync_data(2)))
        with patch.object(api_client, "REPO_ROOT", str(self.root)), patch.dict(os.environ, {"GIST_ID": "offline-test"}):
            await self.client._update_if_region_data()
        today = datetime.now().date().isoformat()
        self.assertEqual(self.client._cached_data["regions"][0]["schedule"]["5.2"][today]["00:00"], 2)

    async def test_invalid_or_future_source_never_overwrites_proxy(self):
        for data in ({"schedules": sync_data()["schedules"]},
                     sync_data(timestamp=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()),
                     {"updated_at": datetime.now(timezone.utc).isoformat(), "schedules": {"5.2": []}}):
            with self.subTest(data=data):
                original = {"5.2": {"2026-10-05": {"00:00": 1}}}
                self.client._cached_data["regions"][0]["schedule"] = original.copy()
                (self.root / "data/if_schedules.json").write_text(json.dumps(data))
                with patch.object(api_client, "REPO_ROOT", str(self.root)), patch.dict(os.environ, {"GIST_ID": ""}):
                    await self.client._update_if_region_data()
                self.assertEqual(self.client._cached_data["regions"][0]["schedule"], original)

    async def test_direct_queries_have_bounded_concurrency(self):
        active = maximum = 0
        async def fetch(queue):
            nonlocal active, maximum
            active += 1
            maximum = max(active, maximum)
            await asyncio.sleep(.01)
            active -= 1
            return [{"eventDate": datetime.now().strftime("%d.%m.%Y"), "queues": {queue: []}}]
        self.client._if_api_blocked = False
        with patch.object(api_client, "REPO_ROOT", str(self.root)), patch.dict(os.environ, {"GIST_ID": ""}), \
             patch.object(self.client, "_fetch_if_queues", AsyncMock(return_value=[str(i) for i in range(12)])), \
             patch.object(self.client, "_fetch_if_schedule", AsyncMock(side_effect=fetch)) as get:
            await self.client._update_if_region_data()
        self.assertEqual(get.await_count, 12)
        self.assertLessEqual(maximum, 4)
        self.assertEqual(len(self.client._cached_data["regions"][0]["schedule"]), 12)

    def test_parser_does_not_invent_green_day_for_missing_queue(self):
        self.assertEqual(self.client._parse_if_schedule([{"eventDate": "05.10.2026", "queues": {}}], "5.2"), {})

    async def test_batch_timeout_keeps_completed_queues(self):
        self.client._if_api_blocked = False
        wait_for = asyncio.wait_for
        async def short_timeout(operation, timeout):
            self.assertEqual(timeout, 15)
            return await wait_for(operation, .03)
        async def fetch(queue):
            if queue == "slow":
                await asyncio.Event().wait()
            return [{"eventDate": "05.10.2026", "queues": {queue: []}}]
        with patch.object(api_client, "REPO_ROOT", str(self.root)), patch.dict(os.environ, {"GIST_ID": ""}), \
             patch.object(self.client, "_fetch_if_queues", AsyncMock(return_value=["fast", "slow"])), \
             patch.object(self.client, "_fetch_if_schedule", fetch), \
             patch.object(if_source.asyncio, "wait_for", short_timeout):
            await self.client._update_if_region_data()
        schedules = self.client._cached_data["regions"][0]["schedule"]
        self.assertIn("fast", schedules)
        self.assertNotIn("slow", schedules)


class ReminderTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, now, grid, last_reminder=None, quiet=None):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now
        data = {"date_today": now.date().isoformat(), "date_tomorrow": (now.date() + timedelta(days=1)).isoformat(),
                "schedule": {now.date().isoformat(): grid}}
        start, end, silent = quiet or (None, None, 1)
        record = (1, "test", '[{"id":"5.2","alias":"A_`"}]', None, "classic", 60,
                  last_reminder, None, 1, start, end, silent, None, None)
        bot = SimpleNamespace(send_message=AsyncMock())
        with patch.object(reminder_service, "datetime", Clock), \
             patch.object(reminder_service, "get_all_users", AsyncMock(return_value=[record])), \
             patch.object(reminder_service, "update_user_last_reminder", AsyncMock()) as save:
            await reminder_service.check_reminders(bot, SimpleNamespace(fetch_schedule=AsyncMock(return_value=data)))
        return bot, save

    async def test_past_restoration_does_not_block_future_outage(self):
        grid = {f"{h:02d}:{m:02d}": 1 for h in range(24) for m in (0, 30)}
        for slot in ("07:00", "07:30", "09:00", "09:30"):
            grid[slot] = 2
        bot, save = await self.check(datetime(2026, 10, 5, 8, 10), grid)
        bot.send_message.assert_awaited_once()
        self.assertIn("09:00", bot.send_message.await_args.args[1])
        self.assertIn("A\\_\\`", bot.send_message.await_args.args[1])
        save.assert_awaited_once()

    async def test_unknown_is_not_restoration(self):
        grid = {"07:00": 2, "07:30": 2}
        bot, save = await self.check(datetime(2026, 10, 5, 7, 10), grid)
        bot.send_message.assert_not_awaited()
        save.assert_not_awaited()

    async def test_past_outage_does_not_block_future_outage(self):
        grid = {f"{h:02d}:{m:02d}": 1 for h in range(24) for m in (0, 30)}
        grid["08:00"] = grid["09:00"] = 2
        grid["08:30"] = 0
        bot, save = await self.check(datetime(2026, 10, 5, 8, 10), grid)
        bot.send_message.assert_awaited_once()
        self.assertIn("09:00", bot.send_message.await_args.args[1])
        save.assert_awaited_once()

    async def test_quiet_hours_skip_and_silent_modes(self):
        grid = {"08:00": 1, "08:30": 2}
        now = datetime(2026, 10, 5, 8, 10)
        bot, _ = await self.check(now, grid, quiet=("07:00", "09:00", 0))
        bot.send_message.assert_not_awaited()
        bot, _ = await self.check(now, grid, quiet=("07:00", "09:00", 1))
        bot.send_message.assert_awaited_once()
        self.assertTrue(bot.send_message.await_args.kwargs["disable_notification"])

    async def test_same_event_is_not_sent_twice(self):
        bot, save = await self.check(datetime(2026, 10, 5, 8, 10), {"08:00": 1, "08:30": 2},
                                     last_reminder="off_202610050830")
        bot.send_message.assert_not_awaited()
        save.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
