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
