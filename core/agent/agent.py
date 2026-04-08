from core.hook_loader import HookLoader
from core.debug_handler import DebugLogHandler
from langchain_core.callbacks import AsyncCallbackHandler
import json
import ast
from typing import Any, Dict

class Agent:
    def __init__(self, graph):
        self.graph = graph
        self.hook_loader = HookLoader() # Load dynamic plugin hooks

    async def execute(self, content: str, session_id: str, callbacks: list = None) -> str:
        """Invokes the graph directly without Discord dependencies."""
        # Execute pre_message hooks (e.g. Session logging)
        for hook in self.hook_loader.pre_message_hooks:
            await hook(session_id, content)

        debug_handler = DebugLogHandler()
        config = {
            "configurable": {
                "thread_id": session_id
            },
            "callbacks": [debug_handler] + (callbacks or [])
        }
        inputs = {"messages": [{"role": "user", "content": content}]}
        
        try:
            result = await self.graph.ainvoke(inputs, config=config)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Error invoking graph: {e}")
            return "Sorry, I encountered an error processing the request."

        # Extract the last response message
        reply_message = result["messages"][-1]
        reply_text = reply_message.content
        
        # Handle list content (common with block format/tool responses)
        if isinstance(reply_text, list):
            texts = []
            for part in reply_text:
                if isinstance(part, dict) and part.get("type") == "text":
                    texts.append(part.get("text", ""))
                elif isinstance(part, str):
                    texts.append(part)
            reply_text = "".join(texts)
            
        # Execute post_message hooks (e.g. Session logging)
        for hook in self.hook_loader.post_message_hooks:
             await hook(session_id, reply_text)
             
        return reply_text

    async def process_message(self, message, bot):
        """Handles Discord messages and routes them through execute."""
        from core.util import get_session_id, split_message
        session_id = get_session_id(message)
            
        reaction_handler = ReactionCallbackHandler(message)
        async with message.channel.typing():
            reply_text = await self.execute(message.content, session_id, callbacks=[reaction_handler])

        if reply_text is not None:
            # Send reply back to Discord in chunks if necessary
            chunks = split_message(reply_text)
            for chunk in chunks:
                await message.channel.send(chunk)


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
                            config = self.loader.get_agent_config(agent_id)
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
