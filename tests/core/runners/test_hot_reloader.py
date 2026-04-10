import unittest
import asyncio
import os
import tempfile
import sys
import time

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.runners.hot_reloader import HotReloader

class TestHotReloader(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reloader = HotReloader()
        self.reloader._files.clear()
        self.reloader._running = False
        if self.reloader._task:
            self.reloader._task.cancel()
            self.reloader._task = None

    async def asyncTearDown(self):
        self.reloader.stop()

    async def test_watch_invokes_callback_on_modification(self):
        # Create temp file
        with tempfile.NamedTemporaryFile(delete=False) as temp:
            temp.write(b"initial content")
            temp.flush()
            temp_path = temp.name
        
        callback_invoked = False
        invoked_file = None
        
        def my_callback(path):
            nonlocal callback_invoked, invoked_file
            callback_invoked = True
            invoked_file = path

        self.reloader.watch(temp_path, my_callback)
        self.reloader.start(interval=0.1) # short interval for tests

        # Wait briefly
        await asyncio.sleep(0.2)
        self.assertFalse(callback_invoked)

        # Modify temp file
        # Explicitly advance mtime to ensure detection
        current_mtime = os.path.getmtime(temp_path)
        os.utime(temp_path, (current_mtime + 5, current_mtime + 5))

        # Wait for reloader to pick it up
        await asyncio.sleep(0.3)
        self.assertTrue(callback_invoked)
        self.assertEqual(invoked_file, temp_path)

        # Clean up
        os.unlink(temp_path)

if __name__ == '__main__':
    unittest.main()
