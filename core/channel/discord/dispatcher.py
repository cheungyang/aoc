import os
import asyncio
from typing import Optional, List
import discord
from core.channel.response_parser import AgentResponse
from core.channel.discord.ui import PollButtonView, PollSelectView
from core.channel.chunks import safe_chunks

# Sources whose reply is posted: a message in the channel, a schedule, a
# proactive delivery (core/proactive/delivery.py) and a follow-up's check
# (core/proactive/follow_ups.py).
POSTED_SOURCES = ("discord", "scheduled", "proactive", "followup")
# Sources whose reply may carry a poll.
POLL_SOURCES = ("discord", "proactive", "followup")


async def dispatch_discord_output(
    channel: Optional[discord.abc.Messageable],
    response: AgentResponse,
    source: str = "discord"
) -> List[discord.Message]:
    """Sends split message chunks, poll UI, and media files to Discord. Returns the
    messages it sent: a proactive delivery keeps their ids, so a ✅ or ❌ on one of
    them is its outcome (core/proactive/outcomes.py). Other callers ignore them."""
    sent: List[discord.Message] = []
    if channel is None or source not in POSTED_SOURCES:
        return sent

    text_content = response.text
    poll_data = response.poll_data
    image_paths = response.image_paths
    video_paths = response.video_paths

    chunks = safe_chunks(text_content)

    view = None
    if poll_data and source in POLL_SOURCES and poll_data.get("options"):
        if poll_data.get("allow_multiple"):
            view = PollSelectView(poll_data, channel)
        else:
            view = PollButtonView(poll_data, channel)

    files = []
    missing_files = []
    if (image_paths or video_paths) and source == "discord":
        media_items = []
        if image_paths:
            for p in image_paths:
                media_items.append((p, "Image"))
        if video_paths:
            for p in video_paths:
                media_items.append((p, "Video"))

        for path, media_type in media_items:
            resolved_path = os.path.abspath(os.path.join(os.getcwd(), path))
            if os.path.exists(resolved_path):
                files.append(discord.File(resolved_path))
            else:
                missing_files.append((path, media_type))

    if not chunks and (files or view):
        chunks = [""]

    if chunks:
        for i, chunk in enumerate(chunks):
            if i > 0:
                await asyncio.sleep(1)
            if i == len(chunks) - 1:
                kwargs = {}
                if view:
                    kwargs["view"] = view
                if files:
                    kwargs["files"] = files
                try:
                    sent.append(await channel.send(chunk, **kwargs))
                except discord.HTTPException as e:
                    if view:
                        print(
                            "Warning: Failed to send with view ("
                            f"{e}). Retrying without view."
                        )
                        kwargs.pop("view", None)
                        sent.append(await channel.send(chunk, **kwargs))
                    else:
                        raise
            else:
                sent.append(await channel.send(chunk))

    if missing_files:
        for path, media_type in missing_files:
            sent.append(await channel.send(f"{media_type} file not found: {path}"))
    return sent
