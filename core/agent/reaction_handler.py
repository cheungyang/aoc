import json
import ast
import asyncio
from typing import Dict, Any
from langchain_core.callbacks import AsyncCallbackHandler

class ReactionCallbackHandler(AsyncCallbackHandler):
    def __init__(self, message):
        super().__init__()
        self.message = message
        from core.loaders.agents_loader import AgentsLoader
        self.loader = AgentsLoader()

    async def _add_reaction_safe(self, emoji: str):
        if not self.message or not hasattr(self.message, "add_reaction"):
            return

        message_loop = getattr(getattr(self.message, "_state", None), "loop", None)
        if isinstance(message_loop, asyncio.AbstractEventLoop) and message_loop.is_running():
            try:
                current_loop = asyncio.get_running_loop()
            except RuntimeError:
                current_loop = None

            if current_loop == message_loop:
                await self.message.add_reaction(emoji)
            else:
                asyncio.run_coroutine_threadsafe(self.message.add_reaction(emoji), message_loop)
        else:
            res = self.message.add_reaction(emoji)
            if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                await res

    async def on_tool_start(self, serialized: Dict[str, Any], input_str: Any, **kwargs: Any) -> Any:
        if serialized.get("name") == "agent_call":
            try:
                args = None
                if isinstance(input_str, dict):
                    args = input_str
                elif isinstance(input_str, str):
                    try:
                        args = json.loads(input_str)
                    except json.JSONDecodeError:
                        try:
                            args = ast.literal_eval(input_str)
                        except Exception:
                            return
                else:
                    return

                if not isinstance(args, dict):
                    return
                
                agent_id = args.get("agent_id")
                if agent_id:
                    try:
                        config = self.loader.get_agent(agent_id).config
                        emoji = config.get("emoji", "🤖")
                        await self._add_reaction_safe(emoji)
                    except Exception as e:
                        print(f"Error adding reaction in callback: {e}")
                        try:
                            await self._add_reaction_safe("🤖")
                        except Exception:
                            pass
            except Exception as e:
                print(f"Error in on_tool_start callback: {e}")
