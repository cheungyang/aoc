"""What the runtime tells the learning loop, written off the event loop.

Four things leave no row anywhere else, so their hooks call `record()`:

    command          a command answered (core/runtime/commands/registry.py):
                     `!new`, `!kill`, spoken "stop", "go back"...
    refusal          an action an agent's grants refused (ToolsLoader.check_permission),
                     not a probe and not a tool-level check
    speech_fallback  a voice reply that came without its <speech>
                     (core/channel/voice/speech/reply.py)
    reaction         your 👎 on an agent's message (core/channel/discord/runner.py)

Every write runs on one worker thread, like the Board's (core/proactive/board.py):
the bot's event loop never waits on SQLite. A hook never raises: a write that
fails is printed and dropped, since a lost event costs a data point, and a raised
one would cost a turn. `LEARNING_OBSERVE=0` turns the hooks off.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Mapping, Optional, Tuple

from core.config.settings import Config
from core.storage.learning import COMMAND, REACTION, REFUSAL, SPEECH_FALLBACK, LearningStore

_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="learning-events")
# How much of a command's arguments, or a refused path, an event keeps.
CLIP = 200


def observing() -> bool:
    """Whether the hooks record anything (`LEARNING_OBSERVE`, on by default)."""
    try:
        return bool(Config().learning_observe)
    except Exception:
        return False


def submit(write: Callable[[LearningStore], Any]) -> None:
    """Runs `write(store)` on the worker, against memory.db as it is configured now. Never raises."""
    if not observing():
        return
    try:
        store = LearningStore(Config().memory_db_path)
        _WORKER.submit(_run, write, store)
    except Exception as e:  # a shut-down executor, a broken config: drop the event
        print(f"[learning] could not queue an event: {type(e).__name__}: {e}")


def _run(write: Callable[[LearningStore], Any], store: LearningStore) -> None:
    try:
        write(store)
    except Exception as e:
        print(f"[learning] could not record an event: {type(e).__name__}: {e}")


def record(kind: str, *, agent_id: str = "", channel: str = "", session_id: str = "", name: str = "",
           detail: Optional[Mapping[str, Any]] = None, at: Optional[float] = None) -> None:
    """Queues one learning_events row. Never raises."""
    when = time.time() if at is None else at
    submit(lambda store: store.record_event(kind, at=when, agent_id=agent_id, channel=channel,
                                            session_id=session_id, name=name, detail=detail))


def flush(timeout: float = 5.0) -> None:
    """Waits for every queued write: for tests, and for a process about to exit."""
    _WORKER.submit(lambda: None).result(timeout=timeout)


def _clip(text: Any) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= CLIP else text[:CLIP] + "…"


def _channel_name(channel: Any) -> str:
    return str(getattr(channel, "name", "") or getattr(channel, "id", "") or "")


def command_origin(ctx: Any) -> Tuple[str, str]:
    """(agent, channel) a command was given to: the bot's agent and the channel it was typed
    in, or for a spoken one, the agent and channel the voice room was on. Read before the
    command runs, since "go back" or a switch moves the room. Never raises."""
    try:
        agent_id = str(getattr(ctx, "agent_id", "") or "")
        channel = _channel_name(getattr(ctx, "channel", None))
        voice = getattr(ctx, "voice", None)
        if hasattr(ctx, "heard") and voice is not None:  # a SpokenContext
            focus = voice.focus()
            agent_id = str(getattr(focus, "agent_id", "") or getattr(voice, "agent_id", "") or agent_id)
            channel = str(getattr(focus, "channel_name", "") or channel)
        return agent_id, channel
    except Exception:
        return "", ""


def record_command(registry: str, name: str, ctx: Any, args: str = "",
                   origin: Optional[Tuple[str, str]] = None) -> None:
    """A command that answered: which one, given where (`origin`, from command_origin()),
    and its arguments (clipped). Never raises."""
    if not observing():
        return
    try:
        agent_id, channel = origin if origin is not None else command_origin(ctx)
        session = getattr(ctx, "session", None)
        record(COMMAND, agent_id=agent_id, channel=channel, session_id=str(getattr(session, "session_id", "") or ""),
               name=name, detail={"registry": registry, "args": _clip(args), "heard": _clip(getattr(ctx, "heard", ""))})
    except Exception as e:
        print(f"[learning] could not note the command {name!r}: {type(e).__name__}: {e}")


def record_refusal(ctx: Any, tool_id: str, action: Optional[str], path: Optional[str]) -> None:
    """An action the agent's grants refused (`ctx` is the run's ExecutionContext). Never raises."""
    if not observing():
        return
    try:
        record(REFUSAL, agent_id=str(getattr(ctx, "agent_id", "") or ""),
               channel=str(getattr(ctx, "channel_name", "") or ""),
               session_id=str(getattr(ctx, "session_id", "") or ""), name=tool_id,
               detail={"action": action or "", "path": _clip(path)})
    except Exception as e:
        print(f"[learning] could not note a refusal of {tool_id!r}: {type(e).__name__}: {e}")


def record_speech_fallback(agent_id: str) -> None:
    """A voice reply that came without its <speech>: the fallback was spoken instead. Never raises."""
    record(SPEECH_FALLBACK, agent_id=str(agent_id or ""))


def record_reaction(agent_id: str, emoji: str, *, message_id: Any, channel_id: Any, channel: str = "") -> None:
    """Your reaction on one of `agent_id`'s messages. Never raises."""
    record(REACTION, agent_id=str(agent_id or ""), channel=str(channel or channel_id or ""), name=str(emoji),
           detail={"message_id": str(message_id or ""), "channel_id": str(channel_id or "")})
