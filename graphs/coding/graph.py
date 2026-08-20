import os
from typing import Dict, Any, Optional
from langgraph.graph import StateGraph, START, END
from graphs.coding.schemas import CodingState

# Import all nodes
from graphs.coding.nodes.dag_scheduler import dag_scheduler_node
from graphs.coding.nodes.provisioner import provisioner_node
from graphs.coding.nodes.worker_node import worker_node
from graphs.coding.nodes.tester_node import tester_node
from graphs.coding.nodes.critic_node import critic_node
from graphs.coding.nodes.hitl_gate import hitl_gate_node, process_hitl_decision_node
from graphs.coding.nodes.git_handoff import git_handoff_node

# Import adapters
from graphs.coding.adapters import prepare_input, format_output


def create_graph(checkpointer=None, **kwargs):
    """
    Compiles the autonomous EGM Stateless Coding Graph:
    DAG Scheduler -> Node 1 (Provisioner) -> Node 2 (Coder Worker) ->
    Node 3 (Deterministic Tester) -> Node 4 (Critic QA) ->
    Node 5 (HITL Gate) -> Node 6 (Git Handoff & Teardown).
    """
    if checkpointer is None:
        try:
            from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
            checkpointer = SqliteCheckpointer()
        except Exception:
            from langgraph.checkpoint.memory import MemorySaver
            checkpointer = MemorySaver()
    elif checkpointer is False:
        checkpointer = None

    workflow = StateGraph(CodingState)

    # 1. Register Nodes
    workflow.add_node("dag_scheduler", dag_scheduler_node)
    workflow.add_node("provisioner", provisioner_node)
    workflow.add_node("worker_node", worker_node)
    workflow.add_node("tester_node", tester_node)
    workflow.add_node("critic_node", critic_node)
    workflow.add_node("hitl_gate", hitl_gate_node)
    workflow.add_node("process_hitl_decision", process_hitl_decision_node)
    workflow.add_node("git_handoff", git_handoff_node)

    # 2. Graph Wiring
    workflow.add_edge(START, "dag_scheduler")

    # Router 1: dag_scheduler -> provisioner or END
    def scheduler_router(state: CodingState):
        if state.get("error_message") and not state.get("current_task"):
            return END
        if state.get("current_task"):
            return "provisioner"
        return END

    workflow.add_conditional_edges(
        "dag_scheduler",
        scheduler_router,
        ["provisioner", END]
    )

    # Router 2: provisioner -> worker_node or END
    def provisioner_router(state: CodingState):
        if state.get("error_message"):
            return END
        return "worker_node"

    workflow.add_conditional_edges(
        "provisioner",
        provisioner_router,
        ["worker_node", END]
    )

    # Worker always hands off to Deterministic Tester
    workflow.add_edge("worker_node", "tester_node")

    # Router 3: tester_node -> critic_node or worker_node (retry) or END (abort)
    def tester_router(state: CodingState):
        if state.get("test_run_passed"):
            return "critic_node"
        
        # Test failed
        attempts = state.get("attempt_count", 1)
        max_retries = state.get("max_retries", 3)
        if attempts < max_retries:
            return "worker_node"
        return END

    workflow.add_conditional_edges(
        "tester_node",
        tester_router,
        ["critic_node", "worker_node", END]
    )

    # Router 4: critic_node -> hitl_gate or worker_node (retry) or END (abort)
    def critic_router(state: CodingState):
        if state.get("critic_passed"):
            return "hitl_gate"
        
        # Critic rejected
        attempts = state.get("attempt_count", 1)
        max_retries = state.get("max_retries", 3)
        if attempts < max_retries:
            return "worker_node"
        return END

    workflow.add_conditional_edges(
        "critic_node",
        critic_router,
        ["hitl_gate", "worker_node", END]
    )

    # HITL Gate presentation leads into process_hitl_decision upon resumption
    workflow.add_edge("hitl_gate", "process_hitl_decision")

    # Router 5: process_hitl_decision -> git_handoff or worker_node (revision) or END (abort)
    def hitl_router(state: CodingState):
        decision = state.get("hitl_decision", "")
        if decision == "approved":
            return "git_handoff"
        elif decision == "revise":
            return "worker_node"
        return END

    workflow.add_conditional_edges(
        "process_hitl_decision",
        hitl_router,
        ["git_handoff", "worker_node", END]
    )

    # Router 6: git_handoff -> dag_scheduler (check next task in queue)
    workflow.add_edge("git_handoff", "dag_scheduler")

    return workflow.compile(
        checkpointer=checkpointer,
        interrupt_after=["hitl_gate"]
    )

# Default compiled instance
graph = create_graph()
