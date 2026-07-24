from langgraph.graph import StateGraph, START, END
from typing import TypedDict, Annotated, Sequence
from langchain_core.messages import BaseMessage, AIMessage
import operator

class MessagesState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]

def dummy_node(state: MessagesState):
    return {
        "messages": [AIMessage(content="Hello from the test subgraph!")]
    }

workflow = StateGraph(MessagesState)
workflow.add_node("dummy", dummy_node)
workflow.add_edge(START, "dummy")
workflow.add_edge("dummy", END)

graph = workflow.compile()
