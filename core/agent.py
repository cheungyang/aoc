from core.hook_loader import HookLoader
from core.debug_handler import DebugLogHandler

class Agent:
    def __init__(self, graph):
        self.graph = graph
        self.hook_loader = HookLoader() # Load dynamic plugin hooks

    async def execute(self, content: str, session_id: str) -> str:
        """Invokes the graph directly without Discord dependencies."""
        debug_handler = DebugLogHandler()
        config = {
            "configurable": {
                "thread_id": session_id
            },
            "callbacks": [debug_handler]
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
            
        return reply_text

    async def process_message(self, message, bot):
        """Handles Discord messages and routes them through execute."""
        from core.util import get_session_id
        
        # Execute pre_message hooks
        for hook in self.hook_loader.pre_message_hooks:
             await hook(message, bot)

        session_id = get_session_id(message)
        
        reply_text = await self.execute(message.content, session_id)
        
        # Execute post_message hooks (e.g. Session logging)
        for hook in self.hook_loader.post_message_hooks:
             await hook(message, bot, reply_text)

        # Send reply back to Discord
        await message.channel.send(reply_text)
