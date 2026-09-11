from langgraph.graph import StateGraph, START, END

from graphs.coding.schemas import CodingState

# v2 (tick reconciler) nodes
from graphs.coding.nodes.scheduler import scheduler_node
from graphs.coding.nodes.implement import implement_node
from graphs.coding.nodes.verify import verify_node
from graphs.coding.nodes.audit import audit_node
from graphs.coding.nodes.publish import publish_node
from graphs.coding.nodes.sync_review import sync_review_node

# v1 (interrupt-driven) nodes, kept for one release behind `create_graph(topology="v1")`
from graphs.coding.nodes.dag_scheduler import dag_scheduler_node
from graphs.coding.nodes.provisioner import provisioner_node
from graphs.coding.nodes.worker_node import worker_node
from graphs.coding.nodes.tester_node import tester_node
from graphs.coding.nodes.critic_node import critic_node
from graphs.coding.nodes.hitl_gate import hitl_gate_node, process_hitl_decision_node
from graphs.coding.nodes.git_handoff import git_handoff_node
from graphs.coding.nodes.termination_node import termination_node

# Import adapters
from graphs.coding.adapters import prepare_input, format_output


def create_graph(checkpointer=None, topology: str = "v2", **kwargs):
    """Compiles the coding graph.

    `topology="v2"` (the default) builds the tick reconciler: six nodes, no
    interrupts, no checkpointer. A tick advances what it can and returns; retry
    is another tick.

    `topology="v1"` builds the previous interrupt-driven graph, kept for one
    release as an escape hatch. It is a code-level argument rather than a
    config field on purpose — which graph gets built is the graph module's
    decision, and a JSON field only added a way for the two to disagree.
    """
    if str(topology).lower() == "v1":
        return _create_v1_graph(checkpointer)
    return _create_v2_graph()


def _create_v2_graph():
    """The tick reconciler of §4.2.

    No checkpointer on purpose: durable state lives in the manifest, git and
    GitHub. A checkpoint keyed by the caller's session was the reason a halted
    run could not be resumed from anywhere else (C4/C5).
    """
    workflow = StateGraph(CodingState)

    workflow.add_node("scheduler", scheduler_node)
    workflow.add_node("implement", implement_node)
    workflow.add_node("verify", verify_node)
    workflow.add_node("audit", audit_node)
    workflow.add_node("publish", publish_node)
    workflow.add_node("sync_review", sync_review_node)

    workflow.add_edge(START, "scheduler")

    # `route` is a channel, so it survives until a node overwrites it. Every
    # router below therefore reads it as an explicit instruction and ends the
    # tick when it is not one of its own options — a node that forgets to set a
    # route stops the tick instead of re-running whatever the last one asked for.
    def _router(allowed):
        def route(state: CodingState):
            value = state.get("route")
            return value if value in allowed else END
        return route

    workflow.add_conditional_edges(
        "scheduler", _router({"implement", "publish", "sync_review"}),
        ["implement", "publish", "sync_review", END]
    )

    # A worker that produced nothing ends the tick rather than testing an
    # unchanged tree; the next tick retries within the implement budget.
    workflow.add_conditional_edges(
        "implement", _router({"verify"}), ["verify", END]
    )

    workflow.add_conditional_edges(
        "verify", _router({"audit", "implement"}), ["implement", "audit", END]
    )

    workflow.add_conditional_edges(
        "audit", _router({"publish", "implement"}), ["implement", "publish", END]
    )

    # publish never loops in-process: a transient failure is retried by the next
    # tick, so one tick cannot spin on a GitHub outage.
    workflow.add_conditional_edges(
        "publish", _router({"scheduler"}), ["scheduler", END]
    )

    workflow.add_conditional_edges(
        "sync_review", _router({"implement", "publish", "scheduler"}),
        ["implement", "publish", "scheduler", END]
    )


    return workflow.compile(checkpointer=None)


def _create_v1_graph(checkpointer=None):
    """The previous topology: LLM and git fused, approval via a LangGraph interrupt."""
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
    workflow.add_node("termination_node", termination_node)

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

    # Router 3: tester_node -> critic_node or worker_node (retry) or termination_node (abort/failure)
    def tester_router(state: CodingState):
        if state.get("test_run_passed"):
            return "critic_node"
        
        # Test failed
        attempts = state.get("attempt_count", 1)
        max_retries = state.get("max_retries", 3)
        if attempts < max_retries:
            return "worker_node"
        return "termination_node"

    workflow.add_conditional_edges(
        "tester_node",
        tester_router,
        ["critic_node", "worker_node", "termination_node"]
    )

    # Router 4: critic_node -> hitl_gate or worker_node (retry) or termination_node (abort/failure)
    def critic_router(state: CodingState):
        if state.get("critic_passed"):
            return "hitl_gate"
        
        # Critic rejected
        attempts = state.get("attempt_count", 1)
        max_retries = state.get("max_retries", 3)
        if attempts < max_retries:
            return "worker_node"
        return "termination_node"

    workflow.add_conditional_edges(
        "critic_node",
        critic_router,
        ["hitl_gate", "worker_node", "termination_node"]
    )

    # HITL Gate presentation leads into process_hitl_decision upon resumption
    workflow.add_edge("hitl_gate", "process_hitl_decision")

    # Router 5: process_hitl_decision -> git_handoff or worker_node (revision) or termination_node (abort)
    def hitl_router(state: CodingState):
        decision = state.get("hitl_decision", "")
        if decision == "approved":
            return "git_handoff"
        elif decision == "revise":
            return "worker_node"
        return "termination_node"

    workflow.add_conditional_edges(
        "process_hitl_decision",
        hitl_router,
        ["git_handoff", "worker_node", "termination_node"]
    )

    # Router 6: git_handoff -> dag_scheduler (check next task in queue)
    workflow.add_edge("git_handoff", "dag_scheduler")

    # Router 7: termination_node -> END
    workflow.add_edge("termination_node", END)


    return workflow.compile(
        checkpointer=checkpointer,
        interrupt_after=["hitl_gate"]
    )

# Default compiled instance
graph = create_graph()
