import discord

def get_session_id(message):
    platform = "discord"
    channel_name = message.channel.name if hasattr(message.channel, "name") else str(message.channel.id)
    thread_id = ""
    if isinstance(message.channel, discord.Thread):
        thread_id = str(message.channel.id)
        if message.channel.parent:
            channel_name = message.channel.parent.name
            
    session_id = f"{platform}:{channel_name}"
    if thread_id:
        session_id += f":{thread_id}"
    return session_id

def split_message(text, limit=2000):
    """Splits a text into chunks of at most 'limit' characters."""
    if not text:
        return []
        
    chunks = []
    while len(text) > limit:
        # Find the last newline before the limit
        idx = text.rfind('\n', 0, limit)
        if idx == -1:
            # If no newline, find the last space
            idx = text.rfind(' ', 0, limit)
            if idx == -1:
                # If no space, hard split
                idx = limit
        
        chunks.append(text[:idx].strip())
        text = text[idx:].lstrip()
        
    if text:
        chunks.append(text)
    return chunks
