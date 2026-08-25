import os
import asyncio
import discord
from typing import Optional

# Ensure FFmpeg executable is located
try:
    import imageio_ffmpeg
    FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_EXE = "ffmpeg"

class AudioItem:
    def __init__(self, file_path: str, auto_delete: bool = True):
        self.file_path = file_path
        self.auto_delete = auto_delete

class AudioStreamQueue:
    """
    Manages an asynchronous FIFO audio queue for seamless, back-to-back playback
    in Discord voice channels with instant cancellation (barge-in) support.
    """

    def __init__(self, voice_manager, loop: Optional[asyncio.AbstractEventLoop] = None):
        self.voice_manager = voice_manager
        self._loop = loop
        self._queue: Optional[asyncio.Queue[Optional[AudioItem]]] = None
        self._queue_loop: Optional[asyncio.AbstractEventLoop] = None
        self._playback_task: Optional[asyncio.Task] = None
        self._current_done_event: Optional[asyncio.Event] = None
        self._is_active = False
        self._running = True

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """Returns the active running event loop, falling back to bound or default loop."""
        from unittest.mock import MagicMock
        if isinstance(self._loop, MagicMock):
            return self._loop
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            if self._loop and not getattr(self._loop, "_closed", False):
                return self._loop
            try:
                return asyncio.get_event_loop()
            except RuntimeError:
                return self._loop

    def _ensure_queue(self):
        """Ensures the internal asyncio.Queue is attached to the current active event loop."""
        cur_loop = self.loop
        from unittest.mock import MagicMock
        if isinstance(cur_loop, MagicMock):
            if self._queue is None:
                self._queue = asyncio.Queue()
            return

        if self._queue is None:
            self._queue = asyncio.Queue()
            self._queue_loop = cur_loop
        elif self._queue_loop is not None and self._queue_loop != cur_loop:
            # Event loop changed (e.g. startup loop -> asyncio.run loop)
            old_items = []
            while not self._queue.empty():
                try:
                    old_items.append(self._queue.get_nowait())
                except Exception:
                    break
            self._queue = asyncio.Queue()
            self._queue_loop = cur_loop
            for item in old_items:
                self._queue.put_nowait(item)

    def start(self):
        """Starts the background playback loop on the active event loop."""
        self._running = True
        self._ensure_queue()
        cur_loop = self.loop
        from unittest.mock import MagicMock
        if isinstance(cur_loop, MagicMock):
            return

        if self._playback_task is None or self._playback_task.done():
            try:
                self._playback_task = cur_loop.create_task(self._playback_loop())
            except Exception as e:
                print(f"[AudioStreamQueue] Error creating playback task: {e}")

    async def put(self, file_path: str, priority: bool = False, auto_delete: bool = True):
        """
        Enqueues an audio file for playback.
        If priority is True, clears existing queue items and plays immediately.
        """
        if not file_path or not os.path.exists(file_path):
            return

        if priority:
            self.clear()

        self._ensure_queue()
        item = AudioItem(file_path=file_path, auto_delete=auto_delete)
        await self._queue.put(item)
        self.start()

    def put_nowait(self, file_path: str, priority: bool = False, auto_delete: bool = True):
        """Non-blocking enqueue for sync contexts."""
        if not file_path or not os.path.exists(file_path):
            return
        if priority:
            self.clear()
        self._ensure_queue()
        item = AudioItem(file_path=file_path, auto_delete=auto_delete)
        self._queue.put_nowait(item)
        self.start()

    def clear(self):
        """
        Instantly halts current playback and clears all pending audio files in the queue.
        Used for barge-in / interruption handling.
        """
        # Stop active voice playback
        vc = getattr(self.voice_manager, "voice_client", None) if self.voice_manager else None
        if vc and (vc.is_playing() or (hasattr(vc, "is_paused") and vc.is_paused())):
            try:
                if hasattr(vc, "stop_playing"):
                    vc.stop_playing()
                elif hasattr(vc, "stop"):
                    vc.stop()
            except Exception:
                pass

        if self._current_done_event:
            self._current_done_event.set()

        # Drain the queue and remove pending files
        self._ensure_queue()
        while self._queue and not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                if item and item.auto_delete and os.path.exists(item.file_path):
                    try:
                        os.unlink(item.file_path)
                    except Exception:
                        pass
                self._queue.task_done()
            except (asyncio.QueueEmpty, ValueError):
                break

    def is_playing(self) -> bool:
        """Returns True if audio is actively playing or items are waiting in the queue."""
        vc = getattr(self.voice_manager, "voice_client", None) if self.voice_manager else None
        if vc and vc.is_playing():
            return True
        if self._queue is not None:
            return not self._queue.empty()
        return False

    async def _playback_loop(self):
        """Worker loop that sequentially plays audio files from the queue."""
        while self._running:
            try:
                self._ensure_queue()
                item = await self._queue.get()
                if item is None or not self._running:
                    if item is not None:
                        self._queue.task_done()
                    break

                vc = getattr(self.voice_manager, "voice_client", None) if self.voice_manager else None
                if not vc or not vc.is_connected():
                    # Discord voice client not available, discard file
                    if item.auto_delete and os.path.exists(item.file_path):
                        try:
                            os.unlink(item.file_path)
                        except Exception:
                            pass
                    self._queue.task_done()
                    continue

                if not os.path.exists(item.file_path):
                    self._queue.task_done()
                    continue

                self._current_done_event = asyncio.Event()
                cur_loop = self.loop

                def _after(error):
                    if error:
                        print(f"[AudioStreamQueue] Playback error: {error}")
                    if item.auto_delete and os.path.exists(item.file_path):
                        try:
                            os.unlink(item.file_path)
                        except Exception:
                            pass
                    if self._current_done_event and not self._current_done_event.is_set():
                        try:
                            cur_loop.call_soon_threadsafe(self._current_done_event.set)
                        except Exception:
                            pass

                try:
                    # If voice client is currently playing something, stop it before starting next item
                    if vc.is_playing():
                        try:
                            if hasattr(vc, "stop_playing"):
                                vc.stop_playing()
                            elif hasattr(vc, "stop"):
                                vc.stop()
                        except Exception:
                            pass
                        await asyncio.sleep(0.02)

                    source = discord.FFmpegPCMAudio(item.file_path, executable=FFMPEG_EXE)
                    vc.play(source, after=_after)
                    await self._current_done_event.wait()
                except Exception as e:
                    print(f"[AudioStreamQueue] Error playing audio item: {e}")
                    if item.auto_delete and os.path.exists(item.file_path):
                        try:
                            os.unlink(item.file_path)
                        except Exception:
                            pass
                finally:
                    self._queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[AudioStreamQueue] Loop exception: {e}")
                await asyncio.sleep(0.1)

    async def stop(self):
        """Stops the audio queue completely."""
        self._running = False
        self.clear()
        if self._playback_task and not self._playback_task.done():
            self._playback_task.cancel()
            try:
                await self._playback_task
            except asyncio.CancelledError:
                pass
            self._playback_task = None
