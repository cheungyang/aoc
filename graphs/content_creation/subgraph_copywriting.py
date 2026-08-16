from langgraph.graph import StateGraph, START, END

from typing_extensions import TypedDict, Literal

class CopywritingState(TypedDict, total=False):
    copy_path: str
    copy_text: str
    gate2_decision: Literal["approved", "revise_copy", "revise_video", "clarify"]


from typing import TypedDict, Literal


def create_copywriting_subgraph(checkpointer=None):
    from graphs.content_creation.graph import ContentCreationState
    from graphs.content_creation.nodes import (
        draft_and_save_copy_node
    )
    workflow = StateGraph(ContentCreationState)
    workflow.add_node("draft_and_save_copy", draft_and_save_copy_node)
    workflow.add_edge(START, "draft_and_save_copy")
    workflow.add_edge("draft_and_save_copy", END)
    return workflow.compile(checkpointer=checkpointer)
