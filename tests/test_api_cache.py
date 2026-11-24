import asyncio
import copy
import json
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from services import api_client


class Response:
    def __init__(self, data=None, status=200, etag="new"):
        self.data, self.status = data, status
        self.headers = {"ETag": etag}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self, **kwargs):
        return self.data


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return self.response


class CacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.prior_instance = api_client._instance
        api_client._instance = None
        self.old = {"date_today": datetime.now().date().isoformat(), "regions": [
            {"cpu": "test", "schedule": {"5.2": {"2026-10-05": {"00:00": 1}}}}]}
        self.new = copy.deepcopy(self.old)
        self.new["regions"][0]["schedule"]["5.2"]["2026-10-05"]["00:00"] = 2
        self.session = Session(Response({"body": json.dumps(self.new)}))
        self.client = api_client.SvitloApiClient(session=self.session, cache_ttl=360)
        self.client._cached_data = self.old
        self.client._etag = "old"

    async def asyncTearDown(self):
        await self.client.stop_refresh()
        api_client._instance = self.prior_instance

    async def test_candidate_and_etag_are_not_visible_during_refresh(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def update(candidate):
            started.set()
            await finish.wait()
        with patch.object(self.client, "_update_if_region_data", update):
            task = asyncio.create_task(self.client._refresh_cache())
            await asyncio.wait_for(started.wait(), 1)
            self.assertIs(self.client._cached_data, self.old)
            self.assertEqual(self.client._etag, "old")
            finish.set()
            await task
        self.assertEqual(self.client._cached_data["regions"], self.new["regions"])
        self.assertEqual(self.client._etag, "new")
        self.assertEqual(self.client._old_cached_data, self.old)

    async def test_total_timeout_preserves_published_cache(self):
        wait_for = asyncio.wait_for
        async def short_timeout(operation, timeout):
            self.assertEqual(timeout, 45)
            return await wait_for(operation, .02)
        async def blocked_update(candidate):
            await asyncio.Event().wait()
        with patch.object(self.client, "_update_if_region_data", blocked_update), \
             patch.object(api_client.asyncio, "wait_for", short_timeout):
            self.assertEqual(await self.client._refresh_cache(), [])
        self.assertEqual(self.client._cached_data, self.old)
        self.assertEqual(self.client._etag, "old")

    async def test_cancelled_waiter_does_not_cancel_shared_refresh(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def refresh():
            started.set()
            await finish.wait()
            return ["test"]
        with patch.object(self.client, "_refresh_cache_locked", AsyncMock(side_effect=refresh)) as run:
            first = asyncio.create_task(self.client._refresh_cache())
            await started.wait()
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(self.client._refresh_cache())
            await asyncio.sleep(0)
            finish.set()
            self.assertEqual(await second, ["test"])
            run.assert_awaited_once()

    async def test_failed_refresh_is_shared_and_cooldown_allows_later_retry(self):
        started, finish = asyncio.Event(), asyncio.Event()
        async def fail():
            started.set()
            await finish.wait()
            return []
        with patch.object(self.client, "_refresh_cache_locked", AsyncMock(side_effect=fail)) as run:
            batch = asyncio.gather(*(self.client._refresh_cache() for _ in range(8)))
            await started.wait()
            await asyncio.sleep(0)
            finish.set()
            await batch
            self.assertEqual(run.await_count, 1)
            await self.client._refresh_cache(force=True)
            self.assertEqual(run.await_count, 1)
            self.client._last_refresh_completed = 0
            await self.client._refresh_cache(force=True)
            self.assertEqual(run.await_count, 2)

    async def test_bad_response_preserves_data_etag_and_change_state(self):
        self.session.response = Response({"body": json.dumps({"regions": [None]})})
        await self.client._refresh_cache()
        self.assertEqual(self.client._cached_data, self.old)
        self.assertEqual(self.client._etag, "old")
        self.assertEqual(self.client.get_changed_regions(), [])
        self.session.response = Response({"body": json.dumps(self.new)})
        self.client._last_refresh_completed = 0
        with patch.object(self.client, "_update_if_region_data", AsyncMock()):
            await self.client._refresh_cache()
        self.assertEqual(self.client._etag, "new")

    async def test_304_rolls_dates_without_relabelling_schedule_keys(self):
        yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
        self.old["date_today"] = yesterday
        self.client._last_fetch_time = time.time() - 86400
        self.session.response = Response(status=304)
        with patch.object(self.client, "_update_if_region_data", AsyncMock()):
            await self.client._refresh_cache()
        self.assertEqual(self.client._cached_data["date_today"], datetime.now().date().isoformat())
        self.assertEqual(self.client._cached_data["regions"], self.old["regions"])

    async def test_shutdown_cancels_shared_refresh(self):
        started = asyncio.Event()
        async def slow():
            started.set()
            await asyncio.Event().wait()
        with patch.object(self.client, "_refresh_cache_locked", slow):
            waiter = asyncio.create_task(self.client._refresh_cache())
            await started.wait()
            await self.client.stop_refresh()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
        self.assertIsNone(self.client._refresh_task)


class OldSourceMergeTests(unittest.TestCase):
    def test_old_only_regions_added_dtek_wins_and_fallback_keeps_old(self):
        from services.old_source import merge_old_regions
        cache = {"regions": [{"cpu": "kyiv", "schedule": {"1.1": "dtek"}}]}
        old = {"regions": [{"cpu": "kyiv", "schedule": {"1.1": "old"}},
                           {"cpu": "sumska-oblast", "schedule": {"1.1": "s"}},
                           {"cpu": "hersonska-oblast", "schedule": None}]}
        merge_old_regions(cache, old)
        self.assertEqual([r["cpu"] for r in cache["regions"]], ["kyiv", "sumska-oblast"])
        self.assertEqual(cache["regions"][0]["schedule"], {"1.1": "dtek"})
        # 304 path: candidate already holds old regions; refresh replaces, not duplicates
        merge_old_regions(cache, {"regions": [{"cpu": "sumska-oblast", "schedule": {"1.1": "s2"}}]})
        self.assertEqual([r["schedule"] for r in cache["regions"]], [{"1.1": "dtek"}, {"1.1": "s2"}])
        # old API down: keep old-only regions from prior data, never DTEK ones
        fresh = {"regions": []}
        merge_old_regions(fresh, None, prior_data=cache)
        self.assertEqual([r["cpu"] for r in fresh["regions"]], ["sumska-oblast"])


if __name__ == "__main__":
    unittest.main()
