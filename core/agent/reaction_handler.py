import json
import ast
import asyncio
from typing import Dict, Any, Optional
from langchain_core.callbacks import AsyncCallbackHandler

class ReactionCallbackHandler(AsyncCallbackHandler):
    def __init__(self, message):
        super().__init__()
        self.message = message
        from core.loaders.agents_loader import AgentsLoader
        from core.loaders.graphs_loader import GraphsLoader
        self.loader = AgentsLoader()
        self.graphs_loader = GraphsLoader()

    def _parse_input(self, input_str: Any) -> Optional[dict]:
        if isinstance(input_str, dict):
            return input_str
        if isinstance(input_str, str):
            try:
                return json.loads(input_str)
            except json.JSONDecodeError:
                try:
                    return ast.literal_eval(input_str)
                except Exception:
                    return None
        return None

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
        tool_name = serialized.get("name")
        # `agent_call` is deliberately absent. Delegation now announces itself
        # from `core.agent.delegation` via a ROUTE_REACTION stream event, because
        # the deterministic router delegates without invoking a tool and this
        # callback would never fire for it. Reacting here as well would
        # double-react on the LLM's path.
        if tool_name == "graph_call":
            try:
                args = self._parse_input(input_str)
                if not isinstance(args, dict):
                    return
                
                graph_name = args.get("graph_name") or args.get("subgraph_name") or args.get("name")
                if graph_name and graph_name != "main":
                    try:
                        config = self.graphs_loader.get_graph_config(graph_name)
                        if config.get("graph_id") != "main":
                            emoji = config.get("emoji", "📊")
                            await self._add_reaction_safe(emoji)
                    except Exception as e:
                        print(f"Error adding graph reaction in callback: {e}")
                        try:
                            await self._add_reaction_safe("📊")
                        except Exception:
                            pass
            except Exception as e:
                print(f"Error in on_tool_start callback for graph: {e}")
