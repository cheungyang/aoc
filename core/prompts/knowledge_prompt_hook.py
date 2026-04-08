import datetime
import time

def knowledge_prompt_hook(current_prompt, **kwargs):
    # Get current time and date
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
    
    knowledge_str = "Common Knowledge:\n" + "\n".join([f"- {k}" for k in knowledge])
    updated_prompt = current_prompt + "\n\n" + knowledge_str 
    return updated_prompt

def register_hooks():
    return {
        "system_prompt": knowledge_prompt_hook
    }
