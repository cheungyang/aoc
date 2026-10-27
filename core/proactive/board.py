"""Board calls off the event loop, one at a time.

Every SignalStore call the proactive service and pipeline make runs on one
worker thread. The bot's event loop never waits on SQLite, and the team's own
writes never contend with each other (two turns ending at once, a decision
claiming turns while another records). Other writers to memory.db still meet
SQLite's busy timeout.
"""
import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="proactive-board")


async def on_board(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """`call(*args, **kwargs)` on the Board's worker thread."""
    return await asyncio.get_running_loop().run_in_executor(_WORKER, functools.partial(call, *args, **kwargs))
