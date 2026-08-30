import re
import time
import asyncio
import discord
from typing import Optional, List, Any, Dict, Callable
from core.agent.agent_response import AgentResponse
from core.agent.discord_ui import PollButtonView
from core.agent.session_identifier import SessionIdentifier

# Event Type Constants
EVENT_TOKEN = "token"
EVENT_TOOL_START = "tool_start"
EVENT_TOOL_END = "tool_end"
EVENT_FINAL_RESPONSE = "final_response"
EVENT_ERROR = "error"
EVENT_SUBAGENT_FINAL = "subagent_final"

# Subagent Custom Stream Events (dispatched via adispatch_custom_event)
SUBAGENT_STREAM_TOKEN = "subagent_stream_token"
SUBAGENT_STREAM_FINAL = "subagent_stream_final"


class StreamHandler:
    """
    Encapsulates LangGraph event streaming, normalization,
    subagent event propagation, and corrupt checkpoint recovery.
    """

    @staticmethod
    async def stream_graph_events(graph: Any, inputs: Dict[str, Any], config: Dict[str, Any]):
        """Low-level graph streaming generator yielding normalized token/tool events."""
        if hasattr(graph, "astream_events"):
            has_subagent_streamed = False
            async for event in graph.astream_events(inputs, config=config, version="v2"):
                kind = event.get("event")
                if kind == "on_chat_model_stream":
                    if has_subagent_streamed:
                        # Suppress top-level orchestrator (e.g. Concierge) post-delegation
                        # tokens to avoid echoing or duplicating subagent response text
                        continue
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content"):
                        content_delta = chunk.content
                        if isinstance(content_delta, str) and content_delta:
                            yield {"type": EVENT_TOKEN, "content": content_delta}
                        elif isinstance(content_delta, list):
                            for p in content_delta:
                                if isinstance(p, dict) and p.get("type") == "text":
                                    t = p.get("text", "")
                                    if t:
                                        yield {"type": EVENT_TOKEN, "content": t}
                                elif isinstance(p, str) and p:
                                    yield {"type": EVENT_TOKEN, "content": p}
                elif kind == "on_tool_start":
                    yield {
                        "type": EVENT_TOOL_START,
                        "tool_name": event.get("name"),
                        "tool_args": event.get("data", {}).get("input", {}),
                        "run_id": event.get("run_id")
                    }
                elif kind == "on_tool_end":
                    yield {
                        "type": EVENT_TOOL_END,
                        "tool_name": event.get("name"),
                        "output": event.get("data", {}).get("output"),
                        "run_id": event.get("run_id")
                    }
                elif kind == "on_custom_event":
                    event_name = event.get("name")
                    if event_name == SUBAGENT_STREAM_TOKEN:
                        data = event.get("data", {})
                        content_delta = data.get("content", "")
                        if content_delta:
                            has_subagent_streamed = True
                            yield {
                                "type": EVENT_TOKEN,
                                "content": content_delta,
                                "agent_id": data.get("agent_id")
                            }
                    elif event_name == SUBAGENT_STREAM_FINAL:
                        data = event.get("data", {})
                        has_subagent_streamed = True
                        yield {
                            "type": EVENT_SUBAGENT_FINAL,
                            "agent_id": data.get("agent_id"),
                            "response": data.get("response"),
                            "text": data.get("text", "")
                        }
        else:
            result = await graph.ainvoke(inputs, config=config)
            reply_message = result["messages"][-1]
            yield {"type": EVENT_TOKEN, "content": str(reply_message.content)}

    @classmethod
    async def stream_with_recovery(
        cls,
        graph: Any,
        inputs: Dict[str, Any],
        config: Dict[str, Any],
        session: SessionIdentifier,
        recover_checkpoint_fn: Callable[[SessionIdentifier], None],
        is_corrupt_checkpoint_fn: Callable[[Exception], bool]
    ):
        """Streams graph events with automatic corrupt checkpoint recovery and retry."""
        try:
            async for event in cls.stream_graph_events(graph, inputs, config):
                yield event
        except Exception as e:
            if is_corrupt_checkpoint_fn(e):
                recover_checkpoint_fn(session)
                async for event in cls.stream_graph_events(graph, inputs, config):
                    yield event
            else:
                raise e

    @staticmethod
    def resolve_final_response(
        graph: Any,
        config: Dict[str, Any],
        accumulated_tokens: Optional[List[str]],
        parse_fn: Callable[[Any], AgentResponse]
    ) -> AgentResponse:
        """Extracts final response from graph state or accumulated stream tokens and parses AgentResponse."""
        state = graph.get_state(config) if hasattr(graph, "get_state") else None
        raw_reply = ""
        if state and hasattr(state, "values") and state.values and "messages" in state.values and state.values["messages"]:
            raw_reply = state.values["messages"][-1].content
        elif accumulated_tokens:
            raw_reply = "".join(accumulated_tokens)
        return parse_fn(raw_reply)


class DiscordStreamBuffer:
    """
    Manages rate-limited (1.5s) message editing and multi-message overflow (1,900 chars)
    for real-time Discord text streaming while filtering incomplete/completed XML tags.
    """

    def __init__(self, channel: Optional[discord.abc.Messageable], edit_interval: float = 1.5, max_chunk_size: int = 1900):
        self.channel = channel
        self.edit_interval = edit_interval
        self.max_chunk_size = max_chunk_size
        self.messages: List[discord.Message] = []
        self.accumulated_text = ""
        self.last_edit_time = 0.0
        self.last_rendered_text = ""
        self._lock = asyncio.Lock()
        self._disabled = (channel is None)

    @staticmethod
    def filter_xml_for_stream(text: str) -> str:
        """
        Suppresses completed and in-flight XML blocks (<poll>, <images>, <videos>, <system_memory_log>)
        during live streaming so Discord users only see natural text deltas.
        """
        if not text:
            return ""

        # Remove completed XML tags and their contents
        filtered = re.sub(r'<poll>.*?</poll>', '', text, flags=re.DOTALL | re.IGNORECASE)
        filtered = re.sub(r'<images>.*?</images>', '', filtered, flags=re.DOTALL | re.IGNORECASE)
        filtered = re.sub(r'<videos>.*?</videos>', '', filtered, flags=re.DOTALL | re.IGNORECASE)
        filtered = re.sub(r'<system_memory_log>.*?</system_memory_log>', '', filtered, flags=re.DOTALL | re.IGNORECASE)

        # Remove unclosed in-flight XML blocks
        filtered = re.sub(r'<(?:poll|images|videos|system_memory_log)[^>]*>.*$', '', filtered, flags=re.DOTALL | re.IGNORECASE)
        filtered = re.sub(r'<(?:poll|images|videos|system_memory_log)[^>]*$', '', filtered, flags=re.IGNORECASE)
        filtered = re.sub(r'<[^>]*$', '', filtered)

        return filtered.rstrip()

    async def append_token(self, token: str):
        """Appends a token delta and updates the Discord message if edit interval has elapsed."""
        if self._disabled:
            return

        async with self._lock:
            self.accumulated_text += token
            now = time.time()
            if now - self.last_edit_time >= self.edit_interval:
                await self._render()

    async def _render(self):
        """Renders the current filtered text to Discord messages."""
        if self._disabled or not self.channel:
            return

        visible_text = self.filter_xml_for_stream(self.accumulated_text)
        if not visible_text:
            return

        if visible_text == self.last_rendered_text:
            return

        self.last_rendered_text = visible_text
        self.last_edit_time = time.time()

        # Handle 1900 char chunks
        chunks = [visible_text[i:i + self.max_chunk_size] for i in range(0, len(visible_text), self.max_chunk_size)]
        if not chunks:
            chunks = ["..."]

        for idx, chunk in enumerate(chunks):
            if idx < len(self.messages):
                # Edit existing message chunk
                try:
                    await self.messages[idx].edit(content=chunk)
                except (discord.NotFound, discord.Forbidden) as e:
                    print(f"[DiscordStreamBuffer] Message {idx} inaccessible during edit: {e}")
                except discord.HTTPException as e:
                    print(f"[DiscordStreamBuffer] Warning editing message chunk {idx}: {e}")
            else:
                # Send new message chunk
                try:
                    msg = await self.channel.send(chunk)
                    self.messages.append(msg)
                except (discord.NotFound, discord.Forbidden) as e:
                    self._disabled = True
                    print(f"[DiscordStreamBuffer] Channel inaccessible ({e}). Disabling live stream for this turn.")
                    break
                except discord.HTTPException as e:
                    print(f"[DiscordStreamBuffer] Error sending new message chunk {idx}: {e}")
                    break

    async def finalize(self, final_text: str, response: Optional[AgentResponse] = None):
        """
        Finalizes the stream by posting full sanitized response text and attaching UI elements
        (PollButtonView, image files, video files).
        """
        if self._disabled or not self.channel:
            return

        async with self._lock:
            if final_text and not self.accumulated_text:
                self.accumulated_text = final_text

            clean_text = self.filter_xml_for_stream(self.accumulated_text)
            if not clean_text and response and response.text:
                clean_text = response.text
            if not clean_text:
                clean_text = "Done."

            chunks = [clean_text[i:i + self.max_chunk_size] for i in range(0, len(clean_text), self.max_chunk_size)]
            if not chunks:
                chunks = ["Done."]

            # Render text chunks
            for idx, chunk in enumerate(chunks):
                if idx < len(self.messages):
                    try:
                        await self.messages[idx].edit(content=chunk)
                    except discord.HTTPException:
                        pass
                else:
                    try:
                        msg = await self.channel.send(chunk)
                        self.messages.append(msg)
                    except (discord.NotFound, discord.Forbidden) as e:
                        self._disabled = True
                        print(f"[DiscordStreamBuffer] Channel inaccessible during finalization ({e}).")
                        break
                    except discord.HTTPException as e:
                        print(f"[DiscordStreamBuffer] Error sending final chunk {idx}: {e}")
                        break

            # Delete any extra messages that were created during stream but are no longer in chunks
            if len(chunks) < len(self.messages):
                for extra_msg in self.messages[len(chunks):]:
                    try:
                        await extra_msg.delete()
                    except Exception as e:
                        print(f"[DiscordStreamBuffer] Warning deleting extra message: {e}")
                self.messages = self.messages[:len(chunks)]

            if self._disabled:
                return

            # Attach Poll UI and Media if present in response
            if response and self.channel:
                from core.agent.discord_ui import PollButtonView, PollSelectView
                from core.util import Config
                import os

                view = None
                if response.poll_data and response.poll_data.get("options"):
                    if response.poll_data.get("allow_multiple"):
                        view = PollSelectView(response.poll_data, self.channel)
                    else:
                        view = PollButtonView(response.poll_data, self.channel)

                # Prepare discord.File attachments for images and videos
                attachments = []
                missing_files = []
                media_items = []
                if response.image_paths:
                    for p in response.image_paths:
                        media_items.append((p, "Image"))
                if response.video_paths:
                    for p in response.video_paths:
                        media_items.append((p, "Video"))

                for path, media_type in media_items:
                    # 1. Path is already an absolute path
                    # 2. Path is a relative path resolved against system root ("./")
                    resolved_path = os.path.abspath(path)

                    if os.path.exists(resolved_path):
                        attachments.append(discord.File(resolved_path))
                    else:
                        missing_files.append((path, media_type))

                if (view or attachments) and self.messages:
                    last_msg = self.messages[-1]
                    try:
                        if view and not attachments:
                            await last_msg.edit(view=view)
                        elif attachments:
                            await self.channel.send(files=attachments, view=view)
                    except Exception as e:
                        print(f"[DiscordStreamBuffer] Error attaching UI/media to final stream: {e}")

                if missing_files:
                    for path, media_type in missing_files:
                        try:
                            await self.channel.send(f"{media_type} file not found: {path}")
                        except Exception:
                            pass
