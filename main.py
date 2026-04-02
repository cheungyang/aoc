import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from typing import Annotated, List
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, START, END

# Load environment variables
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not DISCORD_TOKEN or DISCORD_TOKEN == "your_discord_bot_token_here":
    print("Warning: DISCORD_TOKEN not set or using placeholder. Please set it in .env")

if not GEMINI_API_KEY or GEMINI_API_KEY == "your_gemini_api_key_here":
    print("Warning: GEMINI_API_KEY not set or using placeholder. Please set it in .env")

# --- LangGraph Setup ---

# Define the state
class State(TypedDict):
    messages: Annotated[List[BaseMessage], "The messages in the conversation"]

# Define the chatbot node
async def chatbot(state: State):
    """
    A single node that calls the Gemini model.
    """
    # Initialize the Gemini model
    # Note: Adjust the model name if needed (e.g., 'gemini-2.5-flash' or 'gemini-1.5-pro')
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash")
    
    # Invoke the model with the current messages
    response = await llm.ainvoke(state["messages"])
    
    # Return the new message to append to the state
    return {"messages": [response]}

# Build the graph
graph_builder = StateGraph(State)
graph_builder.add_node("chatbot", chatbot)
graph_builder.add_edge(START, "chatbot")
graph_builder.add_edge("chatbot", END)

# Compile the graph
graph = graph_builder.compile()

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
        result = await graph.ainvoke(inputs)
        
        # Extract the last response message
        reply_message = result["messages"][-1]
        reply_text = reply_message.content
        
        # Send reply back to Discord
        await message.channel.send(reply_text)
        
    except Exception as e:
        print(f"Error processing message: {e}")
        await message.channel.send("Sorry, I encountered an error processing your request.")

if __name__ == "__main__":
    if DISCORD_TOKEN and DISCORD_TOKEN != "your_discord_bot_token_here":
        print("Starting Discord bot...")
        bot.run(DISCORD_TOKEN)
    else:
        print("Cannot start bot: DISCORD_TOKEN is missing or placeholder.")
