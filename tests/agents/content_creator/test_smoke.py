"""Coco's smoke test: one content run started, taken through both approval
gates and finished, by `graph_call` under Coco's own session.

In #content-creation the Concierge's router hands each message to Coco by
delegation, so every user turn reaches Coco on `content-creator:tool:content-creation`
(core/runtime/delegation.py). The run's checkpoint is keyed to that session, so
a reply arriving as a new delegated turn resumes the run where it paused. A run
paused under the Concierge's old session (`content_creation:main:discord:...`)
is not visible from here, which is why in-flight runs are drained before the switch.

The graph's paid generators are doubles, as in
tests/graphs/content_creation/test_hitl_and_routing.py.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from langgraph.checkpoint.memory import MemorySaver

from core.runtime.foundation.context import current_execution_context
from core.runtime.foundation.sessions import SessionManager
from graphs.content_creation.adapters import format_output, prepare_input
from graphs.content_creation.graph import create_graph
from tools.graph_call import graph_call

COCO_THREAD = "content_creation:content-creator:tool:content-creation"


def _coco_turn():
    """A fresh context per turn, the one the router's delegation gives Coco."""
    return SessionManager.get_session(agent_id="content-creator", source="tool", channel="content-creation")


def _worker_replies(plot, copy):
    """The graph's drafting and audit calls to worker agents (`agent_call`)."""
    async def reply(args):
        prompt = str(args.get("prompt", "")).lower()
        if "qc playbook" in prompt or "audit" in prompt or "brand editor" in prompt:
            return "<payload>VERDICT: APPROVED\nPassed.</payload>"
        if "copy" in prompt:
            return f"<payload>{json.dumps(copy)}</payload>"
        return f"<payload>{plot}\nOverlay Text: grape</payload>"
    return reply


def _writes_file(content):
    """A generator double: writes its output file and returns its path."""
    async def write(args):
        path = args.get("output_path") or args.get("output_video_path")
        with open(path, "wb") as f:
            f.write(content)
        return f"<payload>{path}</payload>"
    return write


class TestCocoDrivesARun(unittest.IsolatedAsyncioTestCase):

    async def _turn(self, query):
        """One of Coco's turns: `graph_call` as its model would call it."""
        token = current_execution_context.set(_coco_turn())
        try:
            return await graph_call.ainvoke({"graph_name": "content_creation", "query": query})
        finally:
            current_execution_context.reset(token)

    async def test_start_then_gate_1_then_gate_2_then_done(self):
        with tempfile.TemporaryDirectory() as project:
            output = os.path.join(project, "words", "grape")
            os.makedirs(output)
            with open(os.path.join(output, "grape.m4a"), "wb") as f:
                f.write(b"AUDIO")

            graph = create_graph(checkpointer=MemorySaver())
            loader = MagicMock()
            loader.get_graph.return_value = {"graph": graph, "prepare_input": prepare_input, "format_output": format_output}
            coco = {"configurable": {"thread_id": COCO_THREAD}}

            with patch("tools.graph_call.GraphsLoader", return_value=loader), \
                 patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as image, \
                 patch("graphs.content_creation.nodes.production.render_plate.generate_animation_veo3") as veo, \
                 patch("graphs.content_creation.nodes.production.remix_video.remix_video") as remix, \
                 patch("graphs.content_creation.nodes.production.produce_deliverables_node.verify_video_task",
                       new_callable=AsyncMock) as verify, \
                 patch("tools.agent_call.agent_call") as worker:
                image.ainvoke = AsyncMock(side_effect=_writes_file(b"IMG"))
                veo.ainvoke = AsyncMock(side_effect=_writes_file(b"RAW"))
                remix.ainvoke = AsyncMock(side_effect=_writes_file(b"REMIX"))
                verify.return_value = {"video_qc_passed": True, "extracted_frames_path": ["frame.jpg"]}
                worker.ainvoke = AsyncMock(side_effect=_worker_replies("# Grape Plot", {"caption": "Grape"}))

                # Start: the project mapped to its paths, as AGENTS.md says.
                card = await self._turn(f"topic: grape, project_path: {project}, output_path: {output}")
                self.assertIn("HITL GATE 1", card)
                self.assertIn("<images>", card)  # the block that attaches the image in Discord
                self.assertEqual(graph.get_state(coco).next, ("process_gate1_decision",))

                # Gate 1: the user's reply, passed on unchanged in a new turn.
                card = await self._turn("approve")
                self.assertIn("HITL GATE 2", card)
                self.assertEqual(graph.get_state(coco).next, ("process_gate2_decision",))

                # Gate 2: approved, and the run is over.
                await self._turn("approve")
                done = graph.get_state(coco)
                self.assertEqual(done.next, ())
                self.assertEqual(done.values.get("gate1_decision"), "approved")
                self.assertEqual(done.values.get("gate2_decision"), "approved")
                self.assertTrue(os.path.isfile(os.path.join(output, "grape_video.mp4")))

            image.ainvoke.assert_awaited_once()
            veo.ainvoke.assert_awaited_once()

    async def test_the_concierges_old_thread_is_not_the_one_resumed(self):
        """Why in-flight runs are drained before the switch."""
        ctx = _coco_turn()
        self.assertEqual(ctx.get_session_thread_id("content_creation"), COCO_THREAD)
        old = SessionManager.get_session(agent_id="main", source="discord", channel="content-creation")
        self.assertNotEqual(old.get_session_thread_id("content_creation"), COCO_THREAD)


if __name__ == "__main__":
    unittest.main()
