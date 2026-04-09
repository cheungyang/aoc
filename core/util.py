import discord
import datetime
import time

def get_session_id(agent_id, message):
    channel_name = message.channel.name if hasattr(message.channel, "name") else str(message.channel.id)
    thread_id = ""
    if isinstance(message.channel, discord.Thread):
        thread_id = str(message.channel.id)
        if message.channel.parent:
            channel_name = message.channel.parent.name
            
    session_id = f"{agent_id}:{channel_name}"
    if thread_id:
        session_id += f":{thread_id}"
    return session_id

def get_job_id(agent_id):
    date_str = datetime.date.today().isoformat()
    return f"{agent_id}:job:{date_str}"

def get_cron_id(agent_id):
    date_str = datetime.date.today().isoformat()
    return f"{agent_id}:cron:{date_str}"

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

def get_knowledge_prompt():
    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    day_of_week = now.strftime("%A")
    weekday = now.weekday()
    day_type = "Weekday" if weekday < 5 else "Weekend"
    
    tz_str = time.strftime('%Z')
    if not tz_str:
        tz_str = "UTC-7"
        
    knowledge = [
        f"Today's Date: {date_str}",
        f"Today is: {day_of_week} ({day_type})",
        f"Current Time: {time_str}",
        f"Current Timezone: {tz_str}",
        "Current City: San Jose"
    ]
    return "<common_knowledge>\n" + "\n".join([f"- {k}" for k in knowledge]) + "\n</common_knowledge>"
