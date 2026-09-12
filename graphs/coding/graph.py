"""The coding graph: one reconciliation tick.

There is one topology. A tick advances what it safely can and returns; "retry"
is another tick. The previous interrupt-driven graph has been removed — its
durable state lived in a checkpoint keyed by the caller's session, which is why
a halted run could not be resumed from anywhere else (C4/C5).
"""
from langgraph.graph import StateGraph, START, END

from graphs.coding.schemas import CodingState

from graphs.coding.nodes.scheduler import scheduler_node
from graphs.coding.nodes.implement import implement_node
from graphs.coding.nodes.verify import verify_node
from graphs.coding.nodes.audit import audit_node
from graphs.coding.nodes.publish import publish_node
from graphs.coding.nodes.sync_review import sync_review_node

from graphs.coding.adapters import prepare_input, format_output


def create_graph(checkpointer=None, **kwargs):
    """Compiles the tick reconciler of §4.2.

    `checkpointer` is accepted and ignored: the loader passes one to every
    graph, but durable state here lives in the manifest, git and GitHub. It is
    not silently honoured, because a checkpoint would reintroduce exactly the
    resume problem this design removed.
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

    # The audit is advisory, so it has no way back to the worker: its verdict
    # rides along to the PR and the tick carries on to publish.
    workflow.add_conditional_edges(
        "audit", _router({"publish"}), ["publish", END]
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


# Default compiled instance
graph = create_graph()
