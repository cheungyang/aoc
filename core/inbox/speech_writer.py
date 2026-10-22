"""Speech versions for posts that don't have one, written once you're in voice.

A scheduled run writes its own speech version. Replies, scripts' output and
problem notices don't, and most are read in Discord and never heard, so
nothing is written for them until you join voice. Then every unfinished row
without a speech version is handed to the stateless worker (graph-worker-low,
FLASH_LITE), all in parallel, under the same rules as <voice_mode>,
<question> included: `SpeechWriterPrompt` in core/prompts/voice.py. Whoever
writes a speech version decides whether it holds a question; after that,
nothing is judged again.

The result decides a candidate's fate (writer.store_speech): a reply that
asks you something stays, and any other is dropped.
"""
import asyncio
from typing import Awaitable, Callable, Dict, Optional, Tuple

from core.inbox.model import InboxRow
from core.inbox.writer import store_speech
from core.prompts.voice import Speech, SpeechWriterPrompt

# One call per post, all at once, but no more than this many in flight.
MAX_PARALLEL = 4

Write = Callable[[InboxRow, str], Awaitable[Speech]]


async def write_speech(row: InboxRow, name: str) -> Speech:
    """The speech version of `row`'s post, spoken as `name`, from one worker task."""
    if not (row.excerpt or "").strip():
        return None, None
    speech = await SpeechWriterPrompt(name=name, channel=row.channel, post=row.excerpt).run(channel=row.channel)
    if speech is None:
        print(f"[SpeechWriter] No speech version for #{row.channel} this time.")
        return None, None
    return speech


class SpeechWriter:
    """Writes missing speech versions in parallel, one task per channel, so a
    switch into a channel can wait for its own."""

    def __init__(self, write: Optional[Write] = None, max_parallel: int = MAX_PARALLEL):
        self._write = write or write_speech
        self._max_parallel = max_parallel
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._tasks: Dict[str, Tuple[float, asyncio.Task]] = {}

    def start(self, row: InboxRow, name: str,
              on_done: Optional[Callable[[InboxRow], None]] = None) -> Optional[asyncio.Task]:
        """Starts writing `row`'s speech version, unless that's already under way.
        `on_done(row)` gets the row as stored, with its speech version."""
        running = self._tasks.get(row.channel)
        if running and running[0] == row.posted_at and not running[1].done():
            return running[1]
        try:
            task = asyncio.get_running_loop().create_task(self._run(row, name, on_done))
        except RuntimeError:
            return None
        self._tasks[row.channel] = (row.posted_at, task)
        return task

    def writing(self, channel: str) -> bool:
        running = self._tasks.get(channel)
        return bool(running) and not running[1].done()

    async def wait(self, channel: str, timeout: float) -> None:
        """Waits up to `timeout` seconds for the channel's speech version, if one is being written."""
        running = self._tasks.get(channel)
        if not running or running[1].done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(running[1]), timeout)
        except asyncio.TimeoutError:
            print(f"[SpeechWriter] #{channel}'s speech version is taking longer than {timeout:.0f}s.")
        except Exception:
            pass

    async def _run(self, row: InboxRow, name: str, on_done) -> Optional[InboxRow]:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_parallel)
        async with self._semaphore:
            speech, question = await self._write(row, name)
        if not speech:
            return None
        stored = store_speech(row, speech, question)
        if stored is not None and on_done is not None:
            try:
                on_done(stored)
            except Exception as e:
                print(f"[SpeechWriter] After #{row.channel}'s speech version: {e}")
        return stored
