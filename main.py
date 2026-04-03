import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from typing import Annotated, List
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import create_react_agent
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.callbacks import BaseCallbackHandler
import datetime
import json
import sys

# Inject dynamic library path for custom sessions manager deployment
sys.path.append(os.path.join(os.path.dirname(__file__), "skills", "sessions", "scripts"))
import session_manager

# Load environment variables
load_dotenv()

class DebugLogHandler(BaseCallbackHandler):
    def __init__(self, log_file="debug_log.log"):
        self.log_file = log_file
        
    def _log(self, message):
        timestamp = datetime.datetime.now().isoformat()
        with open(self.log_file, "a") as f:
            f.write(f"[{timestamp}] {message}\n")

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._log(f"--- LLM START ---")
        for i, prompt in enumerate(prompts):
            self._log(f"Prompt {i}:\n{prompt}")

    def on_llm_end(self, response, **kwargs):
        self._log(f"--- LLM END ---")
        if response.generations:
             for i, gen in enumerate(response.generations[0]):
                  self._log(f"Generation {i}:\n{gen.text}")

    def on_tool_start(self, serialized, input_str, **kwargs):
        tool_name = serialized.get("name", "Unknown")
        self._log(f"--- TOOL START --- Tool: {tool_name} | Params: {input_str}")

    def on_tool_end(self, output, **kwargs):
        self._log(f"--- TOOL END --- Tool Output (truncated 200 chars): {str(output)[:200]}")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not DISCORD_TOKEN or DISCORD_TOKEN == "your_discord_bot_token_here":
    print("Warning: DISCORD_TOKEN not set or using placeholder. Please set it in .env")

if not GEMINI_API_KEY or GEMINI_API_KEY == "your_gemini_api_key_here":
    print("Warning: GEMINI_API_KEY not set or using placeholder. Please set it in .env")

# --- LangGraph Setup (Moved to async runtime) ---

# --- Discord Bot Setup ---

intents = discord.Intents.default()
intents.message_content = True # Required to read message content

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as Discord bot: {bot.user}')
    print("------")

@bot.event
async def on_message(message):
    # Ignore messages from the bot itself
    if message.author == bot.user:
        return

    # Skip commands (if any)
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    print(f"Received message from {message.author}: {message.content}")

    try:
        # Execute pre_message hooks (e.g. Session handling)
        history_messages = []
        for hook in pre_message_hooks:
             result = await hook(message, bot)
             if result is None:
                  return # Abort loop
             history_messages = result # Or aggregate

        if not history_messages:
             return # Skip if void void 
             
        # Prepare inputs with full memory context
        inputs = {"messages": history_messages}
        
        # Invoke LangGraph asynchronously with Debugging callbacks
        handler = DebugLogHandler()
        # Log total context passes before executing
        handler._log("=== GRAPH INVOKE START ===")
        for msg in history_messages:
             handler._log(f"Context Msg [{msg.type}]: {msg.content}")
             
        result = await bot.graph.ainvoke(inputs, config={"callbacks": [handler]})
        handler._log("=== GRAPH INVOKE END ===")
        
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
        for hook in post_message_hooks:
             await hook(message, bot, reply_text)

        # Send reply back to Discord
        await message.channel.send(reply_text)
        
    except Exception as e:
        print(f"Error processing message: {e}")
        await message.channel.send("Sorry, I encountered an error processing your request.")

import importlib

pre_message_hooks = []
post_message_hooks = []

def load_hooks():
    # Scan skills/* for hooks/startup.py
    for folder in os.listdir("skills"):
        startup_path = os.path.join("skills", folder, "hooks", "startup.py")
        if os.path.exists(startup_path):
             module_path = f"skills.{folder}.hooks.startup"
             try:
                  module = importlib.import_module(module_path)
                  if hasattr(module, "register_hooks"):
                       hooks = module.register_hooks()
                       if "pre_message" in hooks:
                            pre_message_hooks.append(hooks["pre_message"])
                       if "post_message" in hooks:
                            post_message_hooks.append(hooks["post_message"])
                       print(f"Loaded hooks from {module_path}")
             except Exception as e:
                  print(f"Failed to load hooks from {module_path}: {e}")

def load_skills():
    skills_dir = "skills"
    skills_text = ""
    if os.path.isdir(skills_dir):
        for skill_name in os.listdir(skills_dir):
            skill_path = os.path.join(skills_dir, skill_name, "SKILL.md")
            if os.path.isfile(skill_path):
                with open(skill_path, "r") as f:
                    content = f.read()
                parts = content.split("---")
                if len(parts) >= 3:
                     # parts[1] is frontmatter, parts[2] is body
                     body = parts[2].strip()
                     skills_text += f"### Skill: {skill_name}\n{body}\n\n"
    return skills_text

def run_mcp_server():
    from fastmcp import FastMCP
    import importlib
    
    mcp = FastMCP("Concierge Tool Server")
    
    tools_dir = "tools"
    if os.path.isdir(tools_dir):
         for folder in os.listdir(tools_dir):
              folder_path = os.path.join(tools_dir, folder)
              if os.path.isdir(folder_path) and not folder.startswith("__"):
                   for filename in os.listdir(folder_path):
                        if filename.endswith(".py") and not filename.startswith("__"):
                             tool_name = filename[:-3] # Drop .py
                             module_path = f"tools.{folder}.{tool_name}"
                             try:
                                  mod = importlib.import_module(module_path)
                                  if hasattr(mod, tool_name):
                                       func = getattr(mod, tool_name)
                                       mcp.tool()(func)
                                       print(f"Loaded tool {tool_name} from {module_path}")
                             except Exception as e:
                                  print(f"Failed to load tool from {module_path}: {e}")

    mcp.run()

async def run_bot():
    server_params = StdioServerParameters(
        command="python",
        args=[os.path.join(os.path.dirname(__file__), "main.py"), "--mcp"],
    )

    print("Connecting to MCP server...")
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            print("Initializing MCP session...")
            await session.initialize()
            
            print("Loading tools...")
            tools = await load_mcp_tools(session)
            print(f"Loaded {len(tools)} tools.")
            
            print("Building agent with tools and skills...")
            llm = ChatGoogleGenerativeAI(model="gemini-3-flash-preview")
            
            load_hooks() # Load dynamic plugin hooks
            skills_instructions = load_skills()
            system_prompt = f"You are a helpful assistant with access to tools and skills.\n\nYou are provided with the past history trace messages of this workspace session in the messages array. Use them to understand context and provide coherent replies.\n\nFollowing are instructions for available skills:\n{skills_instructions}"
            
            bot.graph = create_react_agent(llm, tools, prompt=system_prompt)
            
            print("Starting Discord bot...")
            async with bot:
                await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--mcp":
        run_mcp_server()
    elif DISCORD_TOKEN and DISCORD_TOKEN != "your_discord_bot_token_here":
        import asyncio
        asyncio.run(run_bot())
    else:
        print("Cannot start bot: DISCORD_TOKEN is missing or placeholder.")
