import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.runtime.agent import Agent
from core.config.settings import Config
from tests.helpers import make_context

class TestGraphBuilder(unittest.IsolatedAsyncioTestCase):
     def setUp(self):
         # Create a patcher for GraphsLoader to avoid filesystem scans
         self.graphs_loader_patcher = patch('core.runtime.graph_builder.GraphsLoader')
         self.mock_graphs_loader_class = self.graphs_loader_patcher.start()
         self.mock_graphs_loader = MagicMock()
         self.mock_graphs_loader_class.return_value = self.mock_graphs_loader
         
         self.mock_create_graph = MagicMock(return_value="MockGraph")
         self.mock_graphs_loader.get_graph.return_value = {
             "create_graph": self.mock_create_graph
         }
         self.mock_graphs_loader.get_graphs_overview.return_value = "Mock Subgraphs"
         # The mutable PKM half reads real pkm/ files; default it to empty.
         self.memory_prompt_patcher = patch('core.runtime.graph_builder.get_agent_memory_prompt', return_value="")
         self.mock_memory_prompt = self.memory_prompt_patcher.start()

     def tearDown(self):
         self.graphs_loader_patcher.stop()
         self.memory_prompt_patcher.stop()


     @patch('core.runtime.graph_builder.get_agent_static_prompt')
     @patch('core.runtime.graph_builder.SkillsLoader')
     @patch('core.runtime.graph_builder.ToolsLoader')
     @patch('core.runtime.model.chat.RetryingChatGoogleGenerativeAI')
     @patch('core.runtime.graph_builder.SqliteCheckpointer')
     async def test_build_graph_success(self, mock_sqlite_checkpointer, mock_llm_class, mock_tool_loader_class, mock_skills_loader_class, mock_get_agent_static_prompt):
         # Setup mocks
         mock_llm_class.return_value = MagicMock()
         
         mock_get_agent_static_prompt.return_value = "Mock Agent Prompt"
         
         mock_tool1 = MagicMock()
         mock_tool1.name = "tool1"
         mock_loader = MagicMock()
         mock_tool_loader_class.return_value = mock_loader
         mock_loader.get_tools.return_value = [mock_tool1]
         
         mock_skills_loader = MagicMock()
         mock_skills_loader_class.return_value = mock_skills_loader
         mock_skills_loader.get_skills_overview.return_value = "Mock Skills"
         
         from core.runtime.graph_builder import GraphBuilder
         builder = GraphBuilder()
         
         # Run
         graph = await builder.build_graph(make_context(agent_id="main"), config={"tools": {"tool1": {}}})
         
         # Assertions
         self.assertEqual(graph, "MockGraph")
         self.mock_create_graph.assert_called_once_with(
             llm=mock_llm_class.return_value,
             tools=[mock_tool1.model_copy.return_value],
             prompt=unittest.mock.ANY,
             checkpointer=mock_sqlite_checkpointer.return_value,
             agent_id="main",
             config={"tools": {"tool1": {}}}
         )
 
     @patch('core.runtime.graph_builder.get_agent_static_prompt')
     @patch('core.runtime.graph_builder.SkillsLoader')
     @patch('core.runtime.graph_builder.ToolsLoader')
     @patch('core.runtime.model.chat.RetryingChatGoogleGenerativeAI')
     @patch('core.runtime.graph_builder.SqliteCheckpointer')
     async def test_build_graph_filtering(self, mock_sqlite_checkpointer, mock_llm_class, mock_tool_loader_class, mock_skills_loader_class, mock_get_agent_static_prompt):
         # Setup mocks
         mock_get_agent_static_prompt.return_value = "Mock Agent Prompt"
 
         mock_tool1 = MagicMock()
         mock_tool1.name = "tool1"
         mock_tool_loader = MagicMock()
         mock_tool_loader_class.return_value = mock_tool_loader
         mock_tool_loader.get_tools.return_value = [mock_tool1]
         
         mock_skills_loader = MagicMock()
         mock_skills_loader_class.return_value = mock_skills_loader
         mock_skills_loader.get_skills_overview.return_value = "Mock Skills"
         
         from core.runtime.graph_builder import GraphBuilder
         builder = GraphBuilder()
         graph = await builder.build_graph(make_context(agent_id="test_agent"), config={"tools": {"tool1": {}}, "skills": {"skill1": {}}})
 
         self.mock_create_graph.assert_called_once_with(
             llm=unittest.mock.ANY,
             tools=[mock_tool1.model_copy.return_value],
             prompt=unittest.mock.ANY,
             checkpointer=mock_sqlite_checkpointer.return_value,
             agent_id="test_agent",
             config={"tools": {"tool1": {}}, "skills": {"skill1": {}}}
         )
 
     @patch('core.runtime.graph_builder.get_agent_static_prompt')
     @patch('core.runtime.graph_builder.SkillsLoader')
     @patch('core.runtime.graph_builder.ToolsLoader')
     @patch('core.runtime.model.chat.RetryingChatGoogleGenerativeAI')
     @patch('core.runtime.graph_builder.SqliteCheckpointer')
     @patch('core.runtime.graph_builder.try_context')
     @patch('core.runtime.graph_builder.JobManager')
     @patch('core.runtime.graph_builder.interrupt')
     async def test_build_graph_wraps_tools(self, mock_interrupt, mock_job_manager_class, mock_try_context, mock_sqlite_checkpointer, mock_llm_class, mock_tool_loader_class, mock_skills_loader_class, mock_get_agent_static_prompt):
         # Setup mocks
         mock_get_agent_static_prompt.return_value = "Mock Agent Prompt"
         
         mock_tool1 = MagicMock()
         mock_tool1.name = "tool1"
         original_run = MagicMock(return_value="tool_output")
         mock_tool1._run = original_run
         mock_tool1._arun = None

         # build_graph wraps a copy so the shared @tool singleton is never mutated.
         mock_tool_copy = MagicMock()
         mock_tool_copy.name = "tool1"
         mock_tool_copy._run = MagicMock(return_value="tool_output")
         mock_tool_copy._arun = None
         mock_tool1.model_copy.return_value = mock_tool_copy
         
         mock_loader = MagicMock()
         mock_tool_loader_class.return_value = mock_loader
         mock_loader.get_tools.return_value = [mock_tool1]
         
         mock_skills_loader = MagicMock()
         mock_skills_loader_class.return_value = mock_skills_loader
         mock_skills_loader.get_skills_overview.return_value = "Mock Skills"
         
         from core.runtime.graph_builder import GraphBuilder
         builder = GraphBuilder()
         
         # Run
         await builder.build_graph(make_context(agent_id="main"), config={"tools": {"tool1": {}}})
         
         # Get the wrapped tool passed to create_graph
         wrapped_tools = self.mock_create_graph.call_args.kwargs["tools"]
         wrapped_tool = wrapped_tools[0]

         # The copy is what gets wrapped; the original is left untouched.
         self.assertIs(wrapped_tool, mock_tool_copy)
         self.assertIs(mock_tool1._run, original_run)
         
         # Test normal execution
         mock_try_context.return_value = MagicMock(job_id="job1")
         mock_job_manager = MagicMock()
         mock_job_manager_class.return_value = mock_job_manager
         mock_job_manager.honor_kill.return_value = False
         
         result = wrapped_tool._run()
         self.assertEqual(result, "tool_output")
         mock_job_manager.honor_kill.assert_called_with("job1")
         mock_interrupt.assert_not_called()
         
         # Test execution when a kill was requested
         mock_job_manager.honor_kill.return_value = True
         
         wrapped_tool._run()
         
         mock_interrupt.assert_called_once_with("Job was killed")
 
     @patch('core.runtime.graph_builder.get_agent_static_prompt')
     @patch('core.runtime.graph_builder.SkillsLoader')
     @patch('core.runtime.graph_builder.ToolsLoader')
     @patch('core.runtime.graph_builder.SqliteCheckpointer')
     async def test_build_graph_ollama(self, mock_sqlite_checkpointer, mock_tool_loader_class, mock_skills_loader_class, mock_get_agent_static_prompt):
         # Setup mocks
         mock_get_agent_static_prompt.return_value = "Mock Agent Prompt"
         
         mock_tool1 = MagicMock()
         mock_tool1.name = "tool1"
         mock_loader = MagicMock()
         mock_tool_loader_class.return_value = mock_loader
         mock_loader.get_tools.return_value = [mock_tool1]
         
         mock_skills_loader = MagicMock()
         mock_skills_loader_class.return_value = mock_skills_loader
         mock_skills_loader.get_skills_overview.return_value = "Mock Skills"
         
         from core.runtime.graph_builder import GraphBuilder
         builder = GraphBuilder()
         
         # Mock sys.modules to handle missing langchain_ollama
         import sys
         mock_ollama = MagicMock()
         mock_ollama_class = mock_ollama.ChatOllama
         mock_ollama_class.return_value = MagicMock()
         
         with patch.dict('sys.modules', {'langchain_ollama': mock_ollama}):
             # Run
             graph = await builder.build_graph(make_context(agent_id="main"), config={"provider": "ollama", "model": "gemma:4b", "tools": {"tool1": {}}})
         
         # Assertions
         self.assertEqual(graph, "MockGraph")
         self.mock_create_graph.assert_called_once_with(
             llm=mock_ollama_class.return_value,
             # build_graph wraps a copy of each tool, never the shared singleton.
             tools=[mock_tool1.model_copy.return_value],
             prompt=unittest.mock.ANY,
             checkpointer=mock_sqlite_checkpointer.return_value,
             agent_id="main",
             config={"provider": "ollama", "model": "gemma:4b", "tools": {"tool1": {}}}
         )

     def _local_provider_mocks(self, mock_tool_loader_class, mock_skills_loader_class, mock_get_agent_static_prompt):
          """Shared setup for the provider: local tests."""
          mock_get_agent_static_prompt.return_value = "Mock Agent Prompt"

          mock_tool1 = MagicMock()
          mock_tool1.name = "tool1"
          mock_loader = MagicMock()
          mock_tool_loader_class.return_value = mock_loader
          mock_loader.get_tools.return_value = [mock_tool1]

          mock_skills_loader = MagicMock()
          mock_skills_loader_class.return_value = mock_skills_loader
          mock_skills_loader.get_skills_overview.return_value = "Mock Skills"

          mock_openai = MagicMock()
          return mock_tool1, mock_openai, mock_openai.ChatOpenAI

     @patch('core.runtime.graph_builder.get_agent_static_prompt')
     @patch('core.runtime.graph_builder.SkillsLoader')
     @patch('core.runtime.graph_builder.ToolsLoader')
     @patch('core.runtime.graph_builder.SqliteCheckpointer')
     async def test_build_graph_local_provider(self, mock_sqlite_checkpointer, mock_tool_loader_class, mock_skills_loader_class, mock_get_agent_static_prompt):
          """A tier under provider: local resolves to the on-device model."""
          from core.config import models

          mock_tool1, mock_openai, mock_openai_class = self._local_provider_mocks(
              mock_tool_loader_class, mock_skills_loader_class, mock_get_agent_static_prompt
          )

          from core.runtime.graph_builder import GraphBuilder
          builder = GraphBuilder()

          with patch.dict('sys.modules', {'langchain_openai': mock_openai}):
              graph = await builder.build_graph(
                  make_context(agent_id="main"),
                  config={"provider": "local", "model": "FLASH", "tools": {"tool1": {}}}
              )

          self.assertEqual(graph, "MockGraph")

          kwargs = mock_openai_class.call_args.kwargs
          # The agent asked for a tier, not a build id; the local table supplies
          # the id. This is what makes moving an agent on-device a one-key change.
          self.assertEqual(kwargs["model"], models.LOCAL_TIERS["FLASH"])
          self.assertNotEqual(kwargs["model"], models.FLASH)

          self.assertEqual(kwargs["base_url"], Config().local_llm_base_url)
          self.assertEqual(kwargs["timeout"], Config().local_llm_timeout)
          # The server handles one request at a time, so a retry joins the back
          # of the queue rather than finding spare capacity.
          self.assertEqual(kwargs["max_retries"], 0)
          # Without this there is no usage metadata under streaming, and the
          # token log silently records nothing.
          self.assertTrue(kwargs["stream_usage"])

          self.mock_create_graph.assert_called_once_with(
              llm=mock_openai_class.return_value,
              tools=[mock_tool1.model_copy.return_value],
              prompt=unittest.mock.ANY,
              checkpointer=mock_sqlite_checkpointer.return_value,
              agent_id="main",
              config={"provider": "local", "model": "FLASH", "tools": {"tool1": {}}}
          )

     @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-a-real-paid-credential"})
     @patch('core.runtime.graph_builder.get_agent_static_prompt')
     @patch('core.runtime.graph_builder.SkillsLoader')
     @patch('core.runtime.graph_builder.ToolsLoader')
     @patch('core.runtime.graph_builder.SqliteCheckpointer')
     async def test_local_provider_never_sends_the_openai_api_key(self, mock_sqlite_checkpointer, mock_tool_loader_class, mock_skills_loader_class, mock_get_agent_static_prompt):
          """ChatOpenAI falls back to OPENAI_API_KEY from the environment when no
          key is passed, and .env exports one. Omitting the argument would post a
          paid credential to an unauthenticated socket on this machine, and
          nothing downstream would report it -- the request would simply succeed.
          """
          _, mock_openai, mock_openai_class = self._local_provider_mocks(
              mock_tool_loader_class, mock_skills_loader_class, mock_get_agent_static_prompt
          )

          from core.runtime.graph_builder import GraphBuilder
          builder = GraphBuilder()

          with patch.dict('sys.modules', {'langchain_openai': mock_openai}):
              await builder.build_graph(
                  make_context(agent_id="main"),
                  config={"provider": "local", "model": "FLASH_LITE", "tools": {}}
              )

          kwargs = mock_openai_class.call_args.kwargs
          self.assertIn("api_key", kwargs, "api_key must be passed explicitly, never inherited")
          self.assertNotEqual(kwargs["api_key"], "sk-a-real-paid-credential")
          # An empty string is rejected by the openai client, so the placeholder
          # has to be a non-empty constant.
          self.assertTrue(kwargs["api_key"])

     @patch('core.runtime.graph_builder.get_agent_static_prompt')
     @patch('core.runtime.graph_builder.SkillsLoader')
     @patch('core.runtime.graph_builder.ToolsLoader')
     @patch('core.runtime.graph_builder.SqliteCheckpointer')
     async def test_local_provider_rejects_the_image_tier(self, mock_sqlite_checkpointer, mock_tool_loader_class, mock_skills_loader_class, mock_get_agent_static_prompt):
          """The device generates no images. Failing at build time keeps the
          error next to the config that asked for it."""
          _, mock_openai, _ = self._local_provider_mocks(
              mock_tool_loader_class, mock_skills_loader_class, mock_get_agent_static_prompt
          )

          from core.runtime.graph_builder import GraphBuilder
          builder = GraphBuilder()

          with patch.dict('sys.modules', {'langchain_openai': mock_openai}):
              with self.assertRaises(ValueError):
                  await builder.build_graph(
                      make_context(agent_id="main"),
                      config={"provider": "local", "model": "IMAGE", "tools": {}}
                  )

     @patch('core.runtime.graph_builder.get_agent_static_prompt')
     @patch('core.runtime.graph_builder.SkillsLoader')
     @patch('core.runtime.graph_builder.get_knowledge_prompt')
     def test_get_prompt_template_keeps_braces_as_text(self, mock_get_knowledge_prompt, mock_skills_loader_class, mock_get_agent_static_prompt):
          mock_get_agent_static_prompt.return_value = "Prompt with braces {} and {variable}"
          mock_skills_loader = MagicMock()
          mock_skills_loader_class.return_value = mock_skills_loader
          mock_skills_loader.get_skills_overview.return_value = "Skills with braces {}"
          mock_get_knowledge_prompt.return_value = "Knowledge with braces {}"
          
          from core.runtime.graph_builder import GraphBuilder
          builder = GraphBuilder()
          
          prompt_fn = builder._get_prompt_template(make_context(agent_id="main"))
          self.assertTrue(callable(prompt_fn))
          
          # Call the dynamic prompt function with a mock state
          from langchain_core.messages import HumanMessage
          messages = prompt_fn({"messages": [HumanMessage(content="test message")]})
          
          # Verify that it compiled and returned formatted messages without template resolution errors
          self.assertTrue(any("Prompt with braces {} and {variable}" in msg.content for msg in messages))
          self.assertTrue(any("Skills with braces {}" in msg.content for msg in messages))
          self.assertTrue(any("Mock Subgraphs" in msg.content for msg in messages))
          self.assertTrue(any("Knowledge with braces {}" in msg.content for msg in messages))
          # The formatting rules' template holds the very braces the model reads.
          self.assertTrue(any('<poll allow_multiple="{true_or_false}">' in msg.content for msg in messages))

     @patch('core.runtime.graph_builder.get_agent_static_prompt')
     @patch('core.runtime.graph_builder.SkillsLoader')
     @patch('core.runtime.graph_builder.get_knowledge_prompt')
     def test_get_prompt_template_formats_messages(self, mock_get_knowledge_prompt, mock_skills_loader_class, mock_get_agent_static_prompt):
          mock_get_agent_static_prompt.return_value = "Agent System Prompt"
          mock_skills_loader = MagicMock()
          mock_skills_loader_class.return_value = mock_skills_loader
          mock_skills_loader.get_skills_overview.return_value = "Skills Prompt"
          mock_get_knowledge_prompt.return_value = "Knowledge Prompt"

          from core.runtime.graph_builder import GraphBuilder
          from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
          builder = GraphBuilder()

          prompt_fn = builder._get_prompt_template(make_context(agent_id="main"))

          input_messages = [
              HumanMessage(content="Hello"),
              AIMessage(content="Hi there")
          ]

          formatted = prompt_fn({"messages": input_messages})

          # Verify system messages and input messages are formatted
          self.assertTrue(any(isinstance(m, SystemMessage) and "Agent System Prompt" in m.content for m in formatted))
          self.assertEqual(formatted[-2], input_messages[0])
          self.assertEqual(formatted[-1], input_messages[1])

     @patch('core.runtime.graph_builder.get_tool_rules_prompt')
     @patch('core.runtime.graph_builder.get_agent_static_prompt')
     @patch('core.runtime.graph_builder.SkillsLoader')
     @patch('core.runtime.graph_builder.get_knowledge_prompt')
     @patch('core.runtime.graph_builder.get_channel_prompt')
     @patch('core.runtime.graph_builder.get_formatting_prompt')
     def test_prompt_template_ordering_for_caching(self, mock_formatting, mock_channel, mock_knowledge, mock_skills_loader_class, mock_agent, mock_tool_rules):
          mock_formatting.return_value = "FORMATTING_RULES"
          mock_agent.return_value = "AGENT_PERSONA"
          mock_tool_rules.return_value = "TOOL_RULES"
          mock_skills_loader = MagicMock()
          mock_skills_loader_class.return_value = mock_skills_loader
          mock_skills_loader.get_skills_overview.return_value = "SKILLS_OVERVIEW"
          self.mock_graphs_loader.get_graphs_overview.return_value = "SUBGRAPHS_OVERVIEW"
          mock_knowledge.return_value = "KNOWLEDGE_CONTEXT"
          mock_channel.return_value = "CHANNEL_CONTEXT"
          self.mock_memory_prompt.return_value = "AGENT_MEMORY"

          from core.runtime.graph_builder import GraphBuilder
          from langchain_core.messages import HumanMessage, SystemMessage
          builder = GraphBuilder()
          prompt_fn = builder._get_prompt_template(make_context(agent_id="main"))
          formatted = prompt_fn({"messages": [HumanMessage(content="Hello")]})

          system_contents = [m.content for m in formatted if isinstance(m, SystemMessage)]
          self.assertEqual(system_contents, [
              "FORMATTING_RULES",
              "AGENT_PERSONA",
              "TOOL_RULES",
              "SKILLS_OVERVIEW",
              "SUBGRAPHS_OVERVIEW",
              "KNOWLEDGE_CONTEXT",
              "AGENT_MEMORY",
              "CHANNEL_CONTEXT"
          ])
          mock_tool_rules.assert_called_with("main")


def _voice_ctx(**kwargs):
    """A voice turn's context, as the room's agent gets it from the router."""
    from core.runtime.foundation.sessions import SessionManager
    return SessionManager.get_session(agent_id="home-steward", source="tool", channel="home-automation",
                                      surface="voice", **kwargs)


def _typed_ctx():
    """A typed turn's context, as the router hands it to the room's agent: the text surface."""
    from core.runtime.foundation.sessions import SessionManager
    return SessionManager.get_session(agent_id="home-steward", source="tool", channel="home-automation",
                                      surface="text")


AGENT = {"channels": ["home-automation"]}


class TestLightCall(unittest.TestCase):
    """Which turns' first model call goes to the light model (graphs/voice)."""

    def light(self, ctx, config=AGENT, setting="FLASH_LITE:minimal"):
        from core.runtime.graph_builder import light_call
        with Config().overriding(voice_light_model=setting):
            return light_call(ctx, config)

    def test_a_voice_turn_of_the_agent_you_talk_to(self):
        self.assertEqual(self.light(_voice_ctx()), ("FLASH_LITE", "minimal"))
        self.assertEqual(self.light(_voice_ctx(), setting=" flash:Minimal "), ("FLASH", "minimal"))

    def test_off_unless_set(self):
        from core.runtime.graph_builder import light_call
        with patch.dict(os.environ):
            os.environ.pop("VOICE_LIGHT_MODEL", None)
            self.assertIsNone(light_call(_voice_ctx(), AGENT))
        for setting in ("off", "OFF", ""):
            with self.subTest(setting=setting):
                self.assertIsNone(self.light(_voice_ctx(), setting=setting))

    def test_no_other_turn_gets_one(self):
        cases = {
            "a typed turn": (_typed_ctx(), AGENT),
            "a stateless context": (_voice_ctx(stateless=True), AGENT),
            "an agent another agent called": (_voice_ctx(called_by_agent=True), AGENT),
            "a graph step": (_voice_ctx(graph_id="coding"), AGENT),
            "a stateless agent": (_voice_ctx(), {**AGENT, "stateless": True}),
            "an agent on a graph of its own": (_voice_ctx(), {**AGENT, "graph": "coding"}),
            "an agent off Gemini": (_voice_ctx(), {**AGENT, "provider": "local"}),
        }
        for name, (ctx, config) in cases.items():
            with self.subTest(name):
                self.assertIsNone(self.light(ctx, config))

    def test_a_setting_that_isnt_tier_level_is_off_and_said_once(self):
        import io
        from contextlib import redirect_stderr
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertIsNone(self.light(_voice_ctx(), setting="PRO:minimal"))
            self.assertIsNone(self.light(_voice_ctx(), setting="PRO:minimal"))
        self.assertEqual(err.getvalue().count("VOICE_LIGHT_MODEL='PRO:minimal' is off"), 1, err.getvalue())


class TestTheVoiceGraphIsBuiltForLightCalls(unittest.IsolatedAsyncioTestCase):

    async def _build(self, ctx, graphs):
        from core.runtime.graph_builder import GraphBuilder
        loader = MagicMock()
        loader.get_graph.side_effect = graphs.get
        with patch("core.runtime.graph_builder.GraphsLoader", return_value=loader), \
             patch("core.runtime.graph_builder.ToolsLoader") as tools, \
             patch("core.runtime.graph_builder.chat_model", return_value="LLM"), \
             patch("core.runtime.graph_builder.SqliteCheckpointer"), \
             Config().overriding(voice_light_model="FLASH_LITE:minimal"):
            tools.return_value.get_tools.return_value = []
            return await GraphBuilder().build_graph(ctx, AGENT)

    def _graphs(self, voice=True):
        graphs = {"main": {"create_graph": MagicMock(return_value="main graph")}}
        if voice:
            graphs["voice"] = {"create_graph": MagicMock(return_value="voice graph")}
        return graphs

    async def test_a_light_call_builds_graphs_voice_with_the_light_model(self):
        graphs = self._graphs()
        self.assertEqual(await self._build(_voice_ctx(), graphs), "voice graph")
        self.assertEqual(graphs["voice"]["create_graph"].call_args.kwargs["light"], ("FLASH_LITE", "minimal"))
        graphs["main"]["create_graph"].assert_not_called()

    async def test_any_other_turn_builds_graphs_main_as_before(self):
        graphs = self._graphs()
        self.assertEqual(await self._build(_typed_ctx(), graphs), "main graph")
        self.assertNotIn("light", graphs["main"]["create_graph"].call_args.kwargs)

    async def test_without_graphs_voice_the_turn_runs_on_main(self):
        import io
        from contextlib import redirect_stderr
        graphs, err = self._graphs(voice=False), io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(await self._build(_voice_ctx(), graphs), "main graph")
        self.assertNotIn("light", graphs["main"]["create_graph"].call_args.kwargs)
        self.assertIn("graphs/voice isn't there: home-steward's voice turns run without the light call", err.getvalue())

    async def test_a_light_call_turn_has_a_compiled_graph_of_its_own(self):
        from core.runtime.agent import Agent
        agent, built = Agent("home-steward", AGENT), []

        async def build(builder, ctx, config):
            built.append(ctx.get_surface())
            return object()

        with patch("core.runtime.agent.GraphBuilder.build_graph", build), \
             Config().overriding(voice_light_model="FLASH_LITE:minimal"):
            voice = await agent._get_graph(_voice_ctx())
            self.assertIs(await agent._get_graph(_voice_ctx()), voice, "built once")
            typed = await agent._get_graph(_typed_ctx())
            with Config().overriding(voice_light_model="FLASH:minimal"):
                other = await agent._get_graph(_voice_ctx())
        self.assertIsNot(voice, typed)
        self.assertIsNot(voice, other, "another light model, another graph")
        self.assertEqual(built, ["voice", "text", "voice"])


if __name__ == "__main__":
    unittest.main()
