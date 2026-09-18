import re
import time
import asyncio
import os
import discord
from typing import Optional, List
from core.channel.response_parser import AgentResponse
from core.channel.discord.ui import PollButtonView, PollSelectView


class DiscordStreamBuffer:
    """
    Manages rate-limited (1.5s) message editing and multi-message overflow
    (1,900 chars) for real-time Discord text streaming while filtering
    incomplete/completed XML tags.
    """

    def __init__(
        self,
        channel: Optional[discord.abc.Messageable],
        edit_interval: float = 1.5,
        max_chunk_size: int = 1900
    ):
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
        Suppresses completed and in-flight XML blocks (<poll>, <images>,
        <videos>, <system_memory_log>) during live streaming so Discord users
        only see natural text deltas.
        """
        if not text:
            return ""

        # Remove completed XML tags and their contents
        filtered = re.sub(
            r'<poll>.*?</poll>', '', text, flags=re.DOTALL | re.IGNORECASE
        )
        filtered = re.sub(
            r'<images>.*?</images>', '', filtered,
            flags=re.DOTALL | re.IGNORECASE
        )
        filtered = re.sub(
            r'<videos>.*?</videos>', '', filtered,
            flags=re.DOTALL | re.IGNORECASE
        )
        filtered = re.sub(
            r'<system_memory_log>.*?</system_memory_log>', '',
            filtered, flags=re.DOTALL | re.IGNORECASE
        )

        # Remove unclosed in-flight XML blocks
        filtered = re.sub(
            r'<(?:poll|images|videos|system_memory_log)[^>]*>.*$',
            '', filtered, flags=re.DOTALL | re.IGNORECASE
        )
        filtered = re.sub(
            r'<(?:poll|images|videos|system_memory_log)[^>]*$',
            '', filtered, flags=re.IGNORECASE
        )
        filtered = re.sub(r'<[^>]*$', '', filtered)

        return filtered.rstrip()

    async def append_token(self, token: str):
        """Appends a token delta and updates Discord message if due."""
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

        chunks = [
            visible_text[i:i + self.max_chunk_size]
            for i in range(0, len(visible_text), self.max_chunk_size)
        ]
        if not chunks:
            chunks = ["..."]

        for idx, chunk in enumerate(chunks):
            if idx < len(self.messages):
                try:
                    await self.messages[idx].edit(content=chunk)
                except (discord.NotFound, discord.Forbidden) as e:
                    print(
                        f"[DiscordStreamBuffer] Message {idx} inaccessible: {e}"
                    )
                except discord.HTTPException as e:
                    print(
                        f"[DiscordStreamBuffer] Warning editing chunk "
                        f"{idx}: {e}"
                    )
            else:
                try:
                    msg = await self.channel.send(chunk)
                    self.messages.append(msg)
                except (discord.NotFound, discord.Forbidden) as e:
                    self._disabled = True
                    print(
                        f"[DiscordStreamBuffer] Channel inaccessible ({e}). "
                        "Disabling live stream for this turn."
                    )
                    break
                except discord.HTTPException as e:
                    print(
                        f"[DiscordStreamBuffer] Error sending chunk {idx}: {e}"
                    )
                    break

    async def finalize(
        self, final_text: str, response: Optional[AgentResponse] = None
    ):
        """
        Finalizes the stream by posting full sanitized response text and
        attaching UI elements (PollButtonView, image files, video files).
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

            chunks = [
                clean_text[i:i + self.max_chunk_size]
                for i in range(0, len(clean_text), self.max_chunk_size)
            ]
            if not chunks:
                chunks = ["Done."]

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
                        print(
                            f"[DiscordStreamBuffer] Channel inaccessible: {e}"
                        )
                        break
                    except discord.HTTPException as e:
                        print(
                            f"[DiscordStreamBuffer] Error sending final "
                            f"chunk: {e}"
                        )
                        break

            if len(chunks) < len(self.messages):
                for extra_msg in self.messages[len(chunks):]:
                    try:
                        await extra_msg.delete()
                    except Exception as e:
                        print(
                            f"[DiscordStreamBuffer] Warning deleting extra: {e}"
                        )
                self.messages = self.messages[:len(chunks)]

            if self._disabled:
                return

            if response and self.channel:
                view = None
                if response.poll_data and response.poll_data.get("options"):
                    if response.poll_data.get("allow_multiple"):
                        view = PollSelectView(response.poll_data, self.channel)
                    else:
                        view = PollButtonView(response.poll_data, self.channel)

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
                            await self.channel.send(
                                files=attachments, view=view
                            )
                    except Exception as e:
                        print(
                            "[DiscordStreamBuffer] Error attaching UI/media: "
                            f"{e}"
                        )

                if missing_files:
                    for path, media_type in missing_files:
                        try:
                            await self.channel.send(
                                f"{media_type} file not found: {path}"
                            )
                        except Exception:
                            pass
