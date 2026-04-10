import asyncio
import os
import logging

logger = logging.getLogger(__name__)

class HotReloader:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(HotReloader, cls).__new__(cls)
            cls._instance._files = {} # path -> (mtime, list of callbacks)
            cls._instance._running = False
            cls._instance._task = None
        return cls._instance
        
    def watch(self, file_path, callback):
        if not os.path.exists(file_path):
            return
        mtime = os.path.getmtime(file_path)
        if file_path not in self._files:
            self._files[file_path] = (mtime, [])
        if callback not in self._files[file_path][1]:
            self._files[file_path][1].append(callback)
            
    def start(self, interval=5):
        if self._running:
            return
        self._running = True
        try:
            loop = asyncio.get_running_loop()
            self._task = loop.create_task(self._run(interval))
        except RuntimeError:
            # No event loop is running yet, callbacks will be registered but the loop must be started explicitly later
            pass
            
    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _run(self, interval):
        while self._running:
            await asyncio.sleep(interval)
            for file_path, (mtime, callbacks) in list(self._files.items()):
                if not os.path.exists(file_path):
                    continue
                current_mtime = os.path.getmtime(file_path)
                if current_mtime > mtime:
                    self._files[file_path] = (current_mtime, callbacks)
                    logger.info(f"Config file updated: {file_path}")
                    for cb in callbacks:
                        try:
                            if asyncio.iscoroutinefunction(cb):
                                await cb(file_path)
                            else:
                                cb(file_path)
                        except Exception as e:
                            logger.error(f"Error executing callback for {file_path}: {e}")
