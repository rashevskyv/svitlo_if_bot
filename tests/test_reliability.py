import asyncio
import threading
import time
import unittest
from datetime import datetime
from io import BytesIO
from unittest.mock import patch

from services import image_generator
import services.api_client as api_client_module
from services.api_client import SvitloApiClient
from services.image_cache import ImageCache
from main import get_last_announcement_id


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_renderer_does_not_block_loop_and_serializes(self):
        active = 0
        maximum = 0
        guard = threading.Lock()
        def slow_render(*args, **kwargs):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.08)
            with guard:
                active -= 1
            return [BytesIO(b"png")]

        with patch.object(image_generator, "generate_schedule_image", slow_render):
            renders = asyncio.gather(
                image_generator.generate_schedule_image_async([], [], datetime.now()),
                image_generator.generate_schedule_image_async([], [], datetime.now()),
            )
            ticks = 0
            async def heartbeat():
                nonlocal ticks
                for _ in range(5):
                    await asyncio.sleep(0.01)
                    ticks += 1
            await asyncio.gather(renders, heartbeat())
        self.assertEqual(maximum, 1)
        self.assertEqual(ticks, 5)

    async def test_refresh_is_singleflight_and_ttl_is_configured(self):
        api_client_module._instance = None
        client = SvitloApiClient(cache_ttl=360)
        SvitloApiClient()
        self.assertEqual(client._cache_ttl, 360)
        calls = 0
        async def refresh():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.03)
            return []
        with patch.object(client, "_refresh_cache_locked", refresh):
            await asyncio.gather(*(client._refresh_cache() for _ in range(8)))
        self.assertEqual(calls, 1)

    async def test_image_cache_is_bounded_and_replaces_old_hash(self):
        ImageCache._instance = None
        cache = ImageCache()
        cache.set("r", "q", "list", "old", [BytesIO(b"old")])
        cache.set("r", "q", "list", "new", [BytesIO(b"new")])
        self.assertIsNone(cache.get("r", "q", "list", "old"))
        for i in range(120):
            cache.set("r", str(i), "list", "h", [BytesIO(b"x")])
        self.assertLessEqual(len(cache._cache), 100)

    async def test_unknown_forecast_is_not_reported_as_no_outages(self):
        text = image_generator.get_next_event_info(
            ["unknown"] * 48, ["unknown"] * 48, datetime(2026, 10, 5, 8, 10)
        )
        self.assertIn("Даних", text)
        self.assertNotIn("не заплановано", text)

    def test_all_render_modes_at_clock_boundaries(self):
        for hour, minute in ((0, 0), (7, 9), (23, 59)):
            for mode in ("classic", "list", "dynamic"):
                with self.subTest(time=(hour, minute), mode=mode):
                    images = image_generator.generate_schedule_image(
                        ["on"] * 48, ["off"] * 48,
                        datetime(2026, 10, 5, hour, minute), mode, "1.1"
                    )
                    self.assertTrue(images)
                    self.assertTrue(all(image.getvalue().startswith(b"\x89PNG\r\n\x1a\n") for image in images))

    def test_announcement_state_uses_schema_column_14(self):
        self.assertEqual(get_last_announcement_id(tuple(range(14))), 13)


if __name__ == "__main__":
    unittest.main()
