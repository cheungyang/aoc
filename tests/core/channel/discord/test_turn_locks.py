"""One turn at a time per household channel or thread (core/channel/discord/turn_locks.py)."""
import asyncio
import unittest

from core.channel.discord.turn_locks import TURN_LOCKS, TurnLocks


class TestTurnLocks(unittest.IsolatedAsyncioTestCase):

    async def test_a_second_turn_on_a_key_waits_for_the_first(self):
        locks, order = TurnLocks(), []
        first_in, release = asyncio.Event(), asyncio.Event()

        async def first():
            async with locks.hold("general"):
                order.append("first in")
                first_in.set()
                await release.wait()
                order.append("first out")

        async def second():
            await first_in.wait()
            async with locks.hold("general"):
                order.append("second in")

        tasks = [asyncio.create_task(first()), asyncio.create_task(second())]
        await first_in.wait()
        await asyncio.sleep(0)
        self.assertEqual(order, ["first in"])  # the second waits
        self.assertTrue(locks.held("general"))
        release.set()
        await asyncio.gather(*tasks)
        self.assertEqual(order, ["first in", "first out", "second in"])

    async def test_other_keys_run_side_by_side(self):
        locks = TurnLocks()
        async with locks.hold("meal-planning"):
            await asyncio.wait_for(self._enter(locks, "general"), timeout=1)
            await asyncio.wait_for(self._enter(locks, "thread-42"), timeout=1)

    @staticmethod
    async def _enter(locks, key):
        async with locks.hold(key):
            return True

    async def test_a_turn_already_holding_its_key_goes_straight_through(self):
        locks = TurnLocks()

        async def nested():
            async with locks.hold("general"):
                async with locks.hold("general"):
                    return "no deadlock"

        self.assertEqual(await asyncio.wait_for(nested(), timeout=1), "no deadlock")
        self.assertEqual(len(locks), 0)

    async def test_it_is_released_when_the_turn_fails(self):
        locks = TurnLocks()
        with self.assertRaises(RuntimeError):
            async with locks.hold("general"):
                raise RuntimeError("the turn failed")
        self.assertFalse(locks.held("general"))
        self.assertTrue(await asyncio.wait_for(self._enter(locks, "general"), timeout=1))

    async def test_a_cancelled_turn_or_waiter_lets_go(self):
        locks, holding = TurnLocks(), asyncio.Event()

        async def hold_forever():
            async with locks.hold("general"):
                holding.set()
                await asyncio.Event().wait()

        holder = asyncio.create_task(hold_forever())
        await holding.wait()
        waiter = asyncio.create_task(self._enter(locks, "general"))
        await asyncio.sleep(0)
        waiter.cancel()
        holder.cancel()
        for task in (waiter, holder):
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(len(locks), 0)
        self.assertTrue(await asyncio.wait_for(self._enter(locks, "general"), timeout=1))

    async def test_a_key_is_dropped_once_nobody_holds_or_waits_for_it(self):
        locks = TurnLocks()
        for n in range(100):
            await self._enter(locks, f"thread-{n}")
        self.assertEqual(len(locks), 0)

    def test_every_bot_runner_shares_the_process_locks(self):
        self.assertIsInstance(TURN_LOCKS, TurnLocks)


if __name__ == "__main__":
    unittest.main()
