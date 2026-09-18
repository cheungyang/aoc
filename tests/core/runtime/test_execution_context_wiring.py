"""Regression guards for the ExecutionContext refactor (Phase 0a).

These pin the four behaviours that were previously broken or unenforceable:

1. An agent invoked under two different graphs gets two different compiled graphs,
   so its tool roster is not frozen at first build.
2. The execution context is bound *before* the graph is constructed, so tool and
   skill resolution sees the right identity.
3. A skill granted by a graph is visible and loadable to an agent whose own home
   graph is different.
4. A tool's identity comes from the context, not from a model-supplied argument.
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.runtime.agent import Agent
from core.runtime.execution_context import try_context
from core.loaders.skills_loader import SkillsLoader
from core.loaders.tools_loader import ToolsLoader
from tests.helpers import make_context, execution_context


class TestGraphBindingIsolation(unittest.IsolatedAsyncioTestCase):
    """The frozen-tool-roster bug: one compiled graph reused across graph bindings."""

    def setUp(self):
        from core.runtime.job_manager import JobManager
        JobManager._instance = None

    @patch('core.runtime.agent.LoggingHandler')
    @patch('core.runtime.graph_builder.GraphBuilder')
    async def test_agent_compiles_one_graph_per_graph_binding(self, mock_builder_class, _mock_logging):
        built_for = []

        async def _build(ctx, config):
            built_for.append(ctx.graph_id)
            graph = MagicMock()
            graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="ok")]})
            return graph

        mock_builder = MagicMock()
        mock_builder.build_graph = _build
        mock_builder_class.return_value = mock_builder

        agent = Agent("test-agent", {})

        await agent.execute("hi", session=make_context(agent_id="test-agent", graph_id="coding"))
        await agent.execute("hi", session=make_context(agent_id="test-agent", graph_id="content_creation"))
        # Re-entering the first binding must reuse the cached graph, not rebuild it.
        await agent.execute("hi", session=make_context(agent_id="test-agent", graph_id="coding"))

        self.assertEqual(built_for, ["coding", "content_creation"])
        self.assertEqual(sorted(agent._graphs.keys()), ["coding", "content_creation"])

    @patch('core.runtime.agent.LoggingHandler')
    @patch('core.runtime.graph_builder.GraphBuilder')
    async def test_graph_is_constructed_inside_the_execution_context(self, mock_builder_class, _mock_logging):
        seen = {}

        async def _build(ctx, config):
            # Graph construction resolves the tool roster and skills, so it has to run with
            # the context already bound. Before Phase 0a it ran under the caller's identity.
            seen["ambient"] = try_context()
            graph = MagicMock()
            graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="ok")]})
            return graph

        mock_builder = MagicMock()
        mock_builder.build_graph = _build
        mock_builder_class.return_value = mock_builder

        agent = Agent("test-agent", {})
        session = make_context(agent_id="test-agent", graph_id="coding")
        await agent.execute("hi", session=session)

        self.assertIs(seen["ambient"], session)

    @patch('core.runtime.agent.LoggingHandler')
    @patch('core.runtime.graph_builder.GraphBuilder')
    async def test_job_reaches_running_despite_the_reordering(self, mock_builder_class, _mock_logging):
        """`add_job` inserts the job as "queued", so the "running" transition has to happen
        after preparation. When the context (which used to stamp "running") moved ahead of
        preparation, in-flight jobs silently stayed "queued" for their whole lifetime."""
        from core.runtime.job_manager import JobManager

        observed = {}
        graph = MagicMock()

        async def _ainvoke(inputs, config=None):
            job_id = config["configurable"]["job_id"]
            observed["status"] = JobManager()._jobs[job_id].status
            return {"messages": [MagicMock(content="ok")]}

        async def _build(ctx, config):
            graph.ainvoke = AsyncMock(side_effect=_ainvoke)
            return graph

        mock_builder = MagicMock()
        mock_builder.build_graph = _build
        mock_builder_class.return_value = mock_builder

        agent = Agent("test-agent", {})
        session = make_context(agent_id="test-agent", graph_id="coding")

        await agent.execute("hi", session=session)

        self.assertEqual(observed["status"], "running")


class TestGraphGrantedSkills(unittest.TestCase):
    """A graph can grant skills to any agent it runs, not just to agents that live in it."""

    def setUp(self):
        self.loader = SkillsLoader()

    @patch('core.loaders.graphs_loader.GraphsLoader.get_graph_skills')
    @patch('core.loaders.agents_loader.AgentsLoader.get_agent')
    def test_graph_skill_reaches_agent_whose_home_graph_differs(self, mock_get_agent, mock_graph_skills):
        mock_agent = MagicMock()
        # The agent's own home graph is "main"; it owns no skills of its own.
        mock_agent.config = {"skills": [], "graph": "main"}
        mock_get_agent.return_value = mock_agent
        mock_graph_skills.side_effect = lambda g: ["coding_skill"] if g == "coding" else []

        # Running under its home graph: no extra skill.
        self.assertEqual(self.loader.get_allowed_skills(make_context(agent_id="agent1")), [])

        # Running under the coding graph: the graph's skill is granted.
        self.assertIn(
            "coding_skill",
            self.loader.get_allowed_skills(make_context(agent_id="agent1", graph_id="coding"))
        )

    @patch('core.loaders.graphs_loader.GraphsLoader.get_graph_skills')
    @patch('core.loaders.agents_loader.AgentsLoader.get_agent')
    def test_graph_skill_appears_in_overview_and_loads(self, mock_get_agent, mock_graph_skills):
        mock_agent = MagicMock()
        mock_agent.config = {"skills": [], "graph": "main"}
        mock_get_agent.return_value = mock_agent
        mock_graph_skills.side_effect = lambda g: ["coding_skill"] if g == "coding" else []

        ctx = make_context(agent_id="agent1", graph_id="coding")

        with patch.object(SkillsLoader, "_load_skills"):
            self.loader._skills_cache["coding_skill"] = {
                "name": "Coding Skill",
                "skill_id": "coding_skill",
                "description": "Writes code.",
                "path": __file__,
            }
            try:
                overview = self.loader.get_skills_overview(ctx)
                self.assertIn("coding_skill", overview)

                # And it is actually loadable, not merely advertised.
                prompt = self.loader.get_skill_prompt(ctx, "coding_skill")
                self.assertTrue(prompt.startswith("<skill>"))

                # An agent running outside that graph still cannot load it.
                denied = self.loader.get_skill_prompt(make_context(agent_id="agent1"), "coding_skill")
                self.assertIn("does not have access", denied)
            finally:
                self.loader._skills_cache.pop("coding_skill", None)


class TestToolIdentityIsNotSpoofable(unittest.TestCase):
    """Tools take their identity from the context; a model-supplied agent_id is inert."""

    def test_model_supplied_agent_id_does_not_change_the_checked_identity(self):
        from tools.filesystem import filesystem

        with patch.object(ToolsLoader, "check_permission", return_value=False) as mock_check:
            with execution_context(agent_id="software-qa"):
                # The model attempts to claim a more privileged identity.
                filesystem.invoke({
                    "agent_id": "main",
                    "instructions": [{"action": "read", "path": "/tmp/whatever.txt"}],
                })

        self.assertTrue(mock_check.called)
        checked_ctx = mock_check.call_args[0][0]
        self.assertEqual(checked_ctx.agent_id, "software-qa")

    def test_tool_refuses_to_run_without_a_context(self):
        from tools.filesystem import filesystem

        result = filesystem.invoke({
            "instructions": [{"action": "read", "path": "/tmp/whatever.txt"}],
        })
        self.assertIn("no active execution context", result)


if __name__ == "__main__":
    unittest.main()
