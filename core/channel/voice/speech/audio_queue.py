"""Plays a turn's speech, in order, on the room's voice client.

Each item is a speaker (pcm_speaker.py): a stretch of speech that is still
being synthesized while it plays. Items play back to back, each as soon as its
first audio is in. Barge-in clears the queue, which stops what's playing and
cancels the synthesis behind everything queued; stage one of barge-in only
ducks the speech that's playing.
"""
import asyncio
from typing import Callable, Optional

import discord

# A queued speaker whose first audio hasn't arrived after this long is dropped.
FIRST_AUDIO_TIMEOUT_S = 10.0


class AudioItem:
    def __init__(self, speaker, on_start: Optional[Callable[[], None]] = None):
        self.speaker = speaker
        # Called when playback of this item begins: how a turn learns when the
        # listener actually started hearing it (its TurnTrace `audio_start`).
        self.on_start = on_start


class AudioStreamQueue:
    """Plays speakers back to back, with instant cancellation (barge-in)."""

    def __init__(self, connection, loop: Optional[asyncio.AbstractEventLoop] = None):
        self.connection = connection  # the VoiceConnection whose voice client plays
        self._loop = loop
        self._queue: Optional[asyncio.Queue[AudioItem]] = None
        self._queue_loop: Optional[asyncio.AbstractEventLoop] = None
        self._playback_task: Optional[asyncio.Task] = None
        self._current_done_event: Optional[asyncio.Event] = None
        self._preparing: Optional[AudioItem] = None  # taken off the queue, waiting for its first audio
        self._generation = 0  # bumped by clear(): an item from before it never plays
        self._running = True
        # Playback volume: lowered while the user talks over the agent (stage
        # one of barge-in) and back to full when the queue is cleared.
        self._volume = 1.0
        self._current_source = None

    def _voice_client(self):
        return getattr(self.connection, "voice_client", None) if self.connection else None

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """Returns the active running event loop, falling back to bound or default loop."""
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            if self._loop and hasattr(self._loop, "create_task"):
                return self._loop
            return asyncio.get_event_loop()

    def _ensure_queue(self):
        """Ensures the internal asyncio.Queue is attached to the current active event loop."""
        cur_loop = self.loop

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

        if self._playback_task is None or self._playback_task.done():
            try:
                self._playback_task = cur_loop.create_task(self._playback_loop())
            except Exception as e:
                print(f"[AudioStreamQueue] Error creating playback task: {e}")

    async def put(self, speaker, on_start: Optional[Callable[[], None]] = None):
        """Queues a speaker after everything before it. `on_start` is called when it starts playing."""
        self._ensure_queue()
        await self._queue.put(AudioItem(speaker, on_start=on_start))
        self.start()

    def clear(self):
        """
        Instantly halts current playback and drops everything queued.
        Used for barge-in / interruption handling.
        """
        self._volume = 1.0
        self._generation += 1

        # Stop active voice playback
        vc = self._voice_client()
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

        # Drop what's queued, and stop synthesizing it
        stale = [self._preparing] if self._preparing else []
        self._ensure_queue()
        while self._queue and not self._queue.empty():
            try:
                stale.append(self._queue.get_nowait())
                self._queue.task_done()
            except (asyncio.QueueEmpty, ValueError):
                break
        for item in stale:
            item.speaker.cleanup()

    def duck(self, volume: float):
        """Keeps playing, at `volume`: stage one of barge-in, while the user's words
        don't yet say whether they are interrupting."""
        self._volume = volume
        if self._current_source is not None:
            self._current_source.volume = volume

    def unduck(self):
        self.duck(1.0)

    def is_playing(self) -> bool:
        """True if speech is playing, about to (its first audio is on the way), or queued."""
        vc = self._voice_client()
        if vc and vc.is_playing():
            return True
        if self._preparing is not None:
            return True
        if self._queue is not None:
            return not self._queue.empty()
        return False

    async def _playback_loop(self):
        """Worker loop that plays the queued speakers one after another."""
        while self._running:
            try:
                self._ensure_queue()
                item = await self._queue.get()
                try:
                    if self._running:
                        await self._play(item)
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[AudioStreamQueue] Loop exception: {e}")
                await asyncio.sleep(0.1)

    async def _play(self, item: AudioItem):
        generation = self._generation
        self._preparing = item
        try:
            try:
                has_audio = await asyncio.wait_for(item.speaker.ready(), FIRST_AUDIO_TIMEOUT_S)
            except asyncio.TimeoutError:
                print("[AudioStreamQueue] No audio arrived for queued speech; skipping it.")
                has_audio = False
        finally:
            self._preparing = None
        vc = self._voice_client()
        if not has_audio or generation != self._generation or not vc or not vc.is_connected():
            item.speaker.cleanup()
            return

        if vc.is_playing():  # the cue
            try:
                if hasattr(vc, "stop_playing"):
                    vc.stop_playing()
                elif hasattr(vc, "stop"):
                    vc.stop()
            except Exception:
                pass
            await asyncio.sleep(0.02)

        done = asyncio.Event()
        self._current_done_event = done
        loop = self.loop

        def _after(error):
            if error:
                print(f"[AudioStreamQueue] Playback error: {error}")
            try:
                loop.call_soon_threadsafe(done.set)
            except RuntimeError:
                pass

        source = discord.PCMVolumeTransformer(item.speaker, volume=self._volume)
        self._current_source = source
        try:
            vc.play(source, after=_after)
            if item.on_start is not None:
                try:
                    item.on_start()
                except Exception as e:
                    print(f"[AudioStreamQueue] on_start callback failed: {e}")
            await done.wait()
        except Exception as e:
            print(f"[AudioStreamQueue] Error playing audio item: {e}")
            item.speaker.cleanup()
        finally:
            self._current_source = None

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
