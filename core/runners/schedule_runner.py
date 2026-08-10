import asyncio
import datetime
from croniter import croniter
from core.loaders.agents_loader import AgentsLoader
from core.loaders.bots_loader import BotsLoader
from core.util import split_message
from core.agent.session_manager import SessionManager
from core.config import Config

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

                prompt = schedule.get("prompt")
                if isinstance(prompt, list):
                    prompt = "\n".join(prompt)

                self.schedules.append({
                    "agent_id": agent_id,
                    "cron": cron_expr,
                    "prompt": prompt,
                    "enabled": str(schedule.get("enabled", "true")).lower() == "true",
                    "channel": schedule.get("channel"),
                    "thread": schedule.get("thread"),
                    "next_run": next_run
                })


        print(f"Loaded {len(self.schedules)} schedules.")

    async def start(self):
        print("ScheduleRunner started.")
        while True:
            await asyncio.sleep(30)
            now = datetime.datetime.now()
            triggered = False
            for item in self.schedules:
                if not item["enabled"]:
                    continue
                
                if now >= item["next_run"]:
                    if triggered:
                        await asyncio.sleep(2)
                    await self._execute_schedule(item)
                    triggered = True
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
        thread_name = item["thread"]
        
        if not Config().is_channel_allowed(channel_name):
            print(f"ScheduleRunner: Skipping schedule for {agent_id} on channel '{channel_name}' (debug mode active, restricted to '{Config().debug_channel}')")
            return

        print(f"Triggering schedule for {agent_id} on channel {channel_name}" + (f" thread {thread_name}" if thread_name else ""))
        
        try:
            agent = self.loader.get_agent(agent_id)

            # Find which agent owns the channel
            owner_agent_id = None
            for aid in self.loader.list_agent_ids():
                a = self.loader.get_agent(aid)
                if channel_name in a.get_config("channel_hosts", []):
                    owner_agent_id = aid
                    break
            channel = self.bots_loader.get_channel(owner_agent_id, channel_name)
            if channel is None:
                channel = self.bots_loader.find_channel(channel_name)
            
            if channel is None:
                print(f"Channel {channel_name} not found for agent {agent_id}")
            
            if thread_name and channel:
                found_thread = None
                # channel.threads is a list of active threads
                for thread in channel.threads:
                    if thread.name == thread_name or str(thread.id) == thread_name:
                        found_thread = thread
                        break
                
                if found_thread:
                    channel = found_thread
                    print(f"Using thread {found_thread.name} ({found_thread.id})")
                else:
                    print(f"Thread {thread_name} not found in channel {channel_name}, falling back to channel.")

            # Execute regardless of channel existance
            await agent.execute(prompt, source="scheduled", channel=channel, role="user")


        except Exception as e:
            print(f"Error executing schedule for {agent_id}: {e}")
