import json
import ast
from typing import Dict, Any
from langchain_core.callbacks import AsyncCallbackHandler

class ReactionCallbackHandler(AsyncCallbackHandler):
    def __init__(self, message):
        super().__init__()
        self.message = message
        from core.agent.agents_loader import AgentsLoader
        self.loader = AgentsLoader()

    async def on_tool_start(self, serialized: Dict[str, Any], input_str: str, **kwargs: Any) -> Any:
        if serialized.get("name") == "agent_call":
            try:
                try:
                    args = json.loads(input_str)
                except json.JSONDecodeError:
                    try:
                        args = ast.literal_eval(input_str)
                    except Exception:
                        return
                
                if args.get("action") == "launch_subagent":
                    agent_id = args.get("agent_id")
                    if agent_id:
                        try:
                            config = self.loader.get_agent(agent_id).config
                            emoji = config.get("emoji", "🤖")
                            await self.message.add_reaction(emoji)
                        except Exception as e:
                            print(f"Error adding reaction in callback: {e}")
                            try:
                                await self.message.add_reaction("🤖")
                            except:
                                pass
            except Exception as e:
                print(f"Error in on_tool_start callback: {e}")
