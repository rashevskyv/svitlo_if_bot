import json
import os
import tempfile
import unittest
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123456:OFFLINE_TEST_DUMMY")

import main
from database import db
from services import image_generator, schedule_sender
from services.image_cache import ImageCache
from services.utils import escape_markdown


def user(aliases=("Home",), last_hash="old"):
    queues = [{"id": "5.2", "alias": alias} for alias in aliases]
    return (1, "test", json.dumps(queues), last_hash, "classic", 60,
            None, None, 1, None, None, 1, None, None)


def schedule(status=1):
    today = datetime.now().date().isoformat()
    return {"schedule": {today: {f"{h:02d}:{m:02d}": status
                                 for h in range(24) for m in (0, 30)}},
            "date_today": today, "date_tomorrow": "2099-01-01"}


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        database_path = patch.object(db, "DB_PATH", os.path.join(folder.name, "offline.db"))
        database_path.start()
        self.addCleanup(database_path.stop)
        ImageCache._instance = None
        self.target = SimpleNamespace(send_photo=AsyncMock(), send_media_group=AsyncMock())
        self.api = SimpleNamespace(fetch_schedule=AsyncMock(return_value=schedule()))
        self.render = AsyncMock(return_value=[BytesIO(b"png")])

    async def test_photo_failure_does_not_advance_delivery_hash(self):
        self.target.send_photo.side_effect = RuntimeError("network failure")
        with patch.object(schedule_sender, "get_user", AsyncMock(return_value=user())), \
             patch.object(db, "update_user_hash", AsyncMock()) as save, \
             patch.object(image_generator, "generate_schedule_image_async", self.render):
            with self.assertRaises(RuntimeError):
                await schedule_sender.send_schedule(self.target, 1, api_client=self.api)
            save.assert_not_awaited()

    async def test_missing_queue_does_not_advance_partial_delivery_hash(self):
        record = list(user())
        record[2] = json.dumps([{"id": "5.2", "alias": "Home"}, {"id": "6.2", "alias": "Work"}])
        self.api.fetch_schedule.side_effect = [schedule(), None]
        with patch.object(schedule_sender, "get_user", AsyncMock(return_value=record)), \
             patch.object(db, "update_user_hash", AsyncMock()) as save, \
             patch.object(image_generator, "generate_schedule_image_async", self.render):
            self.assertFalse(await schedule_sender.send_schedule(self.target, 1, api_client=self.api))
            self.target.send_photo.assert_awaited_once()
            save.assert_not_awaited()

    async def test_cached_image_keeps_id_and_personal_caption(self):
        aliases = ("Alice", "Bob_`[*]\\")
        with patch.object(schedule_sender, "get_user", AsyncMock(side_effect=[user((a,)) for a in aliases])), \
             patch.object(db, "update_user_hash", AsyncMock()) as save, \
             patch.object(image_generator, "generate_schedule_image_async", self.render):
            self.assertTrue(await schedule_sender.send_schedule(self.target, 1, api_client=self.api))
            self.assertTrue(await schedule_sender.send_schedule(self.target, 2, api_client=self.api))
            self.render.assert_awaited_once()
            self.assertEqual(self.render.await_args.args[4], "5.2")
            caption = self.target.send_photo.await_args.kwargs["caption"]
            self.assertIn(escape_markdown(aliases[1]), caption)
            self.assertNotIn("Alice", caption)
            self.assertEqual(save.await_count, 2)

    async def test_duplicate_queue_aliases_are_successful_deliveries(self):
        with patch.object(schedule_sender, "get_user", AsyncMock(return_value=user(("Home", "Work")))), \
             patch.object(db, "update_user_hash", AsyncMock()) as save, \
             patch.object(image_generator, "generate_schedule_image_async", self.render):
            self.assertTrue(await schedule_sender.send_schedule(self.target, 1, api_client=self.api))
            self.assertEqual(self.target.send_photo.await_count, 2)
            save.assert_awaited_once()

    async def test_failed_notification_is_retried_next_update_pass(self):
        api = SimpleNamespace(_refresh_cache=AsyncMock(), get_changed_regions=lambda reset: [],
                              fetch_schedule=AsyncMock(return_value=schedule(2)),
                              get_old_schedule=AsyncMock(return_value=schedule(1)))
        bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username="test")),
                              send_message=AsyncMock())
        with patch.object(main, "api_client", api), patch.object(main, "bot", bot), \
             patch.object(db, "get_all_users", AsyncMock(return_value=[user()])), \
             patch.object(main, "send_schedule", AsyncMock(side_effect=RuntimeError("send failed"))) as send, \
             patch.object(main, "update_user_hash", AsyncMock()) as save:
            await main._check_updates()
            await main._check_updates()
            self.assertEqual(send.await_count, 2)
            save.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
