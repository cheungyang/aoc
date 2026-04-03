import contextlib
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent
from core.skills_loader import SkillsLoader
from core.memory.flat_file_checkpointer import FlatFileCheckpointer
from core.agent import Agent

class AgentBuilder:
    def __init__(self, tools):
        self.tools = tools

    def build_agent(self):
         print("Building agent with tools and skills...")
         llm = ChatGoogleGenerativeAI(model="gemini-3-flash-preview")
         
         skills_loader = SkillsLoader()
         skills_instructions = skills_loader.load_skills()
         system_prompt = f"You are a helpful assistant with access to tools and skills.\n\nYou are provided with the past history trace messages of this workspace session in the messages array. Use them to understand context and provide coherent replies.\n\nFollowing are instructions for available skills:\n{skills_instructions}"
         
         checkpointer = FlatFileCheckpointer()
         graph = create_react_agent(llm, self.tools, prompt=system_prompt, checkpointer=checkpointer)
         return Agent(graph)
