from core.hook_loader import HookLoader

class Agent:
    def __init__(self, graph):
        self.graph = graph
        self.hook_loader = HookLoader() # Load dynamic plugin hooks

    async def process_message(self, message, bot):
        from core.util import get_session_id
        from core.debug_handler import DebugLogHandler
        
        # Execute pre_message hooks
        for hook in self.hook_loader.pre_message_hooks:
             await hook(message, bot)

        # Invoke LangGraph asynchronously with Debugging callbacks
        debug_handler = DebugLogHandler()
        config = {
            "configurable": {
                "thread_id": get_session_id(message)
            },
            "callbacks": [debug_handler]
        }
        inputs = {"messages": [{"role": "user", "content": message.content}]}
        
        try:
            result = await self.graph.ainvoke(inputs, config=config)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Error processing message: {e}")
            await message.channel.send("Sorry, I encountered an error processing your request.")
            return

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
             await hook(message, bot, reply_text)

        # Send reply back to Discord
        await message.channel.send(reply_text)
