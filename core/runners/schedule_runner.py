import asyncio
import datetime
from croniter import croniter
from core.loaders.agents_loader import AgentsLoader
from core.loaders.bots_loader import BotsLoader
from core.util import split_message

class ScheduleRunner:
    def __init__(self):
        self.loader = AgentsLoader()
        self.bots_loader = BotsLoader()
        self.schedules = []
        self._load_schedules()

    def _load_schedules(self):
        agent_ids = self.loader.list_agent_ids()
        now = datetime.datetime.now()
        for agent_id in agent_ids:
            agent = self.loader.get_agent(agent_id)
            config = agent.config
            schedules = config.get("schedules", [])
            for schedule in schedules:
                cron_expr = schedule.get("cron")
                try:
                    iter = croniter(cron_expr, now)
                    next_run = iter.get_next(datetime.datetime)
                except Exception as e:
                    print(f"Error parsing cron '{cron_expr}' for agent {agent_id}: {e}")
                    continue

                self.schedules.append({
                    "agent_id": agent_id,
                    "cron": cron_expr,
                    "prompt": schedule.get("prompt"),
                    "enabled": str(schedule.get("enabled", "true")).lower() == "true",
                    "channel": schedule.get("channel"),
                    "next_run": next_run
                })
        print(f"Loaded {len(self.schedules)} schedules.")

    async def start(self):
        print("ScheduleRunner started.")
        while True:
            await asyncio.sleep(30)
            now = datetime.datetime.now()
            for item in self.schedules:
                if not item["enabled"]:
                    continue
                
                if now >= item["next_run"]:
                    await self._execute_schedule(item)
                    # Update next run time
                    try:
                        iter = croniter(item["cron"], now)
                        item["next_run"] = iter.get_next(datetime.datetime)
                    except Exception as e:
                        print(f"Error updating next run for {item['agent_id']}: {e}")

    async def _execute_schedule(self, item):
        agent_id = item["agent_id"]
        prompt = item["prompt"]
        channel_name = item["channel"]
        
        print(f"Triggering schedule for {agent_id} on channel {channel_name}")
        
        try:
            agent = self.loader.get_agent(agent_id)
            cron_id = f"cron:{agent_id}:{datetime.date.today().isoformat()}"
            response = await agent.execute(prompt, cron_id)
            
            # Send message to Discord
            # Find which agent owns the channel
            owner_agent_id = None
            for aid in self.loader.list_agent_ids():
                a = self.loader.get_agent(aid)
                if channel_name in a.get_config("channel_hosts", []):
                    owner_agent_id = aid
                    break
            
            # Fall back to the current agent if no owner found
            bot_runner = self.bots_loader.get_bot(owner_agent_id or agent_id)
            if bot_runner and bot_runner.bot:
                channel = None
                for guild in bot_runner.bot.guilds:
                    for ch in guild.text_channels:
                        if ch.name == channel_name or str(ch.id) == channel_name:
                            channel = ch
                            break
                    if channel:
                        break
                        
                if channel:
                    chunks = split_message(response)
                    for chunk in chunks:
                        await channel.send(chunk)
                    print(f"Message sent to channel {channel_name} for agent {agent_id}")
                else:
                    print(f"Channel {channel_name} not found for agent {agent_id}")
            else:
                print(f"Bot not found or not initialized for agent {agent_id}")
                
        except Exception as e:
            print(f"Error executing schedule for {agent_id}: {e}")
