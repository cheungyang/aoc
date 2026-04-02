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
from langchain_core.messages import AIMessage
import sys

# Inject dynamic library path for custom sessions manager deployment
sys.path.append(os.path.join(os.path.dirname(__file__), "skills", "sessions", "scripts"))
import session_manager

# Load environment variables
load_dotenv()

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
        # Resolve Session ID locator
        platform = "discord"
        channel_name = message.channel.name if hasattr(message.channel, "name") else str(message.channel.id)
        thread_id = ""
        if isinstance(message.channel, discord.Thread):
            thread_id = str(message.channel.id)
            if message.channel.parent:
                channel_name = message.channel.parent.name
        
        session_id = f"{platform}:{channel_name}"
        if thread_id:
            session_id += f":{thread_id}"

        # Clear session shortcut
        if message.content.strip() == "/new":
            archive_status = session_manager.archive_session(session_id)
            await message.channel.send(f"Session context cleared. {archive_status}")
            return

        # Check active history presence
        history = session_manager.load_history(session_id)

        if not history:
             # First time seeing this session! Resolve thread context if applicable
             if isinstance(message.channel, discord.Thread):
                  try:
                       starter_msg = await message.channel.parent.fetch_message(message.channel.id)
                       if starter_msg and starter_msg.content:
                            from_role = "agent" if starter_msg.author.id == bot.user.id else "human"
                            session_manager.append_message(session_id, from_role, starter_msg.content)
                  except Exception as e:
                       print(f"Failed to resolve starter message block: {e}")

        # Block API corruption crashes logging empty contents
        if message.content.strip():
             session_manager.append_message(session_id, "human", message.content)

        # Refresh state memory logs
        history = session_manager.load_history(session_id)
        
        # Abort graph invocations if current trigger sequence was completely void
        if not message.content.strip():
             return
        
        # Convert history entries to LangChain base messages
        history_messages = []
        for entry in history:
             if entry["from"] == "human":
                  history_messages.append(HumanMessage(content=entry["message"]))
             elif entry["from"] == "agent":
                  history_messages.append(AIMessage(content=entry["message"]))
        
        # Prepare inputs with full memory context
        inputs = {"messages": history_messages}
        
        # Invoke LangGraph asynchronously
        result = await bot.graph.ainvoke(inputs)
        
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
        
        # Append agent reply to session
        session_manager.append_message(session_id, "agent", reply_text)

        # Send reply back to Discord
        await message.channel.send(reply_text)
        
    except Exception as e:
        print(f"Error processing message: {e}")
        await message.channel.send("Sorry, I encountered an error processing your request.")

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

async def run_bot():
    server_params = StdioServerParameters(
        command="python",
        args=[os.path.join(os.path.dirname(__file__), "mcp_server.py")],
    )
    
    # Check if file exists
    mcp_path = os.path.join(os.path.dirname(__file__), "mcp_server.py")
    if not os.path.exists(mcp_path):
        print(f"Error: MCP server file not found at {mcp_path}")
        return

    print("Connecting to MCP server...")
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            print("Initializing MCP session...")
            await session.initialize()
            
            print("Loading tools...")
            tools = await load_mcp_tools(session)
            print(f"Loaded {len(tools)} tools.")
            
            print("Building agent with tools and skills...")
            llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash")
            
            skills_instructions = load_skills()
            system_prompt = f"You are a helpful assistant with access to tools and skills.\n\nFollowing are instructions for available skills:\n{skills_instructions}"
            
            bot.graph = create_react_agent(llm, tools, prompt=system_prompt)
            
            print("Starting Discord bot...")
            async with bot:
                await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    if DISCORD_TOKEN and DISCORD_TOKEN != "your_discord_bot_token_here":
        import asyncio
        asyncio.run(run_bot())
    else:
        print("Cannot start bot: DISCORD_TOKEN is missing or placeholder.")
