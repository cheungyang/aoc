import asyncio
import unittest

from core.scheduler.limiter import ScheduleLimiter, configured_limit
from core.util.config import Config


class TestConfiguredLimit(unittest.TestCase):
    def tearDown(self):
        Config().max_concurrency = None

    def test_uses_system_wide_cap(self):
        Config().max_concurrency = 5
        self.assertEqual(configured_limit(), 5)
        self.assertEqual(ScheduleLimiter().limit, 5)

    def test_explicit_limit_is_clamped(self):
        self.assertEqual(ScheduleLimiter(0).limit, 1)


class TestLimiter(unittest.IsolatedAsyncioTestCase):
    async def test_never_exceeds_limit_and_loses_no_work(self):
        limiter = ScheduleLimiter(2)
        done = []

        async def job(i):
            async with limiter.slot(f"job{i}"):
                self.assertLessEqual(limiter.active, 2)
                await asyncio.sleep(0.01)
                done.append(i)

        await asyncio.gather(*(job(i) for i in range(6)))
        self.assertEqual(sorted(done), list(range(6)))
        self.assertEqual(limiter.peak, 2)
        self.assertEqual(limiter.active, 0)
        self.assertEqual(limiter.waiting, 0)

    async def test_slot_released_on_error(self):
        limiter = ScheduleLimiter(1)
        with self.assertRaises(RuntimeError):
            async with limiter.slot():
                raise RuntimeError("boom")
        async with limiter.slot():
            self.assertEqual(limiter.active, 1)


if __name__ == "__main__":
    unittest.main()
