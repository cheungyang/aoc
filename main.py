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
        # Prepare inputs for LangGraph
        # For a basic chatbot, we just pass the incoming message
        inputs = {"messages": [HumanMessage(content=message.content)]}
        
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
        
        # Send reply back to Discord
        await message.channel.send(reply_text)
        
    except Exception as e:
        print(f"Error processing message: {e}")
        await message.channel.send("Sorry, I encountered an error processing your request.")

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
            
            print("Building agent with tools...")
            llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash")
            bot.graph = create_react_agent(llm, tools)
            
            print("Starting Discord bot...")
            async with bot:
                await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    if DISCORD_TOKEN and DISCORD_TOKEN != "your_discord_bot_token_here":
        import asyncio
        asyncio.run(run_bot())
    else:
        print("Cannot start bot: DISCORD_TOKEN is missing or placeholder.")
