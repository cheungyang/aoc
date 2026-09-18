import unittest
import os
import sys
import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from core.loaders.agents_loader import AgentsLoader

class TestGraphWorkerAgent(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        AgentsLoader._instance = None

    def test_graph_worker_config_exists_and_valid(self):
        loader = AgentsLoader()
        agent = loader.get_agent("graph-worker")
        self.assertIsNotNone(agent)
        self.assertEqual(agent.agent_id, "graph-worker")
        self.assertEqual(agent.config.get("name"), "Graph Worker")
        
        # Verify allowed channels includes wildcard '*'
        allowed_channels = agent.config.get("channels", [])
        self.assertIn("*", allowed_channels)

    def test_graph_worker_tools_configured(self):
        """The worker's roster comes from the graph it is bound to, not from its own config.

        `agent.json` deliberately declares an empty `tools` map: graph-worker is a
        stateless execution node that inherits the caller's graph grants. So the
        thing worth asserting is the *effective* roster under the content_creation
        binding — the media tools the nodes delegate for.
        """
        from core.runtime.session_manager import SessionManager
        from core.loaders.tools_loader import ToolsLoader

        loader = AgentsLoader()
        agent = loader.get_agent("graph-worker")

        # The key must be declared explicitly, not defaulted in by the reader.
        self.assertIn("tools", agent.config)
        self.assertIsInstance(agent.config["tools"], dict)
        self.assertTrue(agent.config.get("stateless"))

        ctx = SessionManager().get_session(
            agent_id="graph-worker",
            source="tool",
            channel="content-creation",
            stateless=True,
            graph_id="content_creation"
        )
        tools_loader = ToolsLoader()
        tools_loader.clear_permissions_cache()

        for tool_id in [
            "generate_image",
            "generate_animation_veo3",
            "remix_video",
            "extract_video_frames",
            "audio_stream_probe",
            "video_ocr_validator",
        ]:
            self.assertTrue(
                tools_loader.check_permission(ctx, tool_id),
                f"graph-worker cannot use '{tool_id}' inside the content_creation graph"
            )

        # Filesystem access is scoped to the content workspace, with write rights.
        workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        granted_dir = os.path.join(workspace_root, "pkm", "wiki", "software")
        for action in ["read", "write", "overwrite", "append", "read_image"]:
            self.assertTrue(
                tools_loader.check_permission(ctx, "filesystem", action, path=os.path.join(granted_dir, "note.md")),
                f"graph-worker lost filesystem '{action}' on the content workspace"
            )
        self.assertFalse(
            tools_loader.check_permission(ctx, "filesystem", "write", path=os.path.join(workspace_root, "core", "agent", "agent.py")),
            "graph-worker must not be able to write outside its granted paths"
        )

        # A stateless execution node must not be able to fan out to other agents.
        self.assertFalse(tools_loader.check_permission(ctx, "agent_call"))

    async def test_agent_call_permissions_for_graph_worker(self):
        from tools.agent_call import agent_call
        from unittest.mock import patch, AsyncMock

        loader = AgentsLoader()
        agent = loader.get_agent("graph-worker")
        
        with patch.object(agent, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = "<payload>executed successfully</payload>"
            res = await agent_call.ainvoke({
                "agent_id": "graph-worker",
                "prompt": "<playbook>Role</playbook><current_state>State</current_state><assigned_task>Task</assigned_task>",
                "channel": "content-creation"
            })
            self.assertIn("executed successfully", res)
            mock_exec.assert_called_once()

if __name__ == "__main__":
    unittest.main()
