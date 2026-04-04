print("Starting script...")
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

print("Importing AgentBuilder...")
from core.agent_builder import AgentBuilder

print("Instantiating AgentBuilder...")
builder = AgentBuilder([])
print("Calling list_agents...")
agents = builder.list_agents()
print("Available agents:")
for agent in agents:
    print(agent)
print("Done.")
