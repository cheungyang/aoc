import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.agent import Agent

class TestGraphBuilder(unittest.IsolatedAsyncioTestCase):
     def setUp(self):
         # Create a patcher for GraphsLoader to avoid filesystem scans
         self.graphs_loader_patcher = patch('core.loaders.graphs_loader.GraphsLoader')
         self.mock_graphs_loader_class = self.graphs_loader_patcher.start()
         self.mock_graphs_loader = MagicMock()
         self.mock_graphs_loader_class.return_value = self.mock_graphs_loader
         
         self.mock_create_graph = MagicMock(return_value="MockGraph")
         self.mock_graphs_loader.get_graph.return_value = {
             "create_graph": self.mock_create_graph
         }
         self.mock_graphs_loader.get_graphs_overview.return_value = "Mock Subgraphs"

     def tearDown(self):
         self.graphs_loader_patcher.stop()


     @patch('core.agent.graph_builder.get_agent_prompt')
     @patch('core.agent.graph_builder.SkillsLoader')
     @patch('core.agent.graph_builder.ToolsLoader')
     @patch('langchain_google_genai.ChatGoogleGenerativeAI')
     @patch('core.agent.graph_builder.SqliteCheckpointer')
     async def test_build_graph_success(self, mock_sqlite_checkpointer, mock_llm_class, mock_tool_loader_class, mock_skills_loader_class, mock_get_agent_prompt):
         # Setup mocks
         mock_llm_class.return_value = MagicMock()
         
         mock_get_agent_prompt.return_value = "Mock Agent Prompt"
         
         mock_tool1 = MagicMock()
         mock_tool1.name = "tool1"
         mock_loader = MagicMock()
         mock_tool_loader_class.return_value = mock_loader
         mock_loader.get_tools.return_value = [mock_tool1]
         
         mock_skills_loader = MagicMock()
         mock_skills_loader_class.return_value = mock_skills_loader
         mock_skills_loader.get_skills_overview.return_value = "Mock Skills"
         
         agent = Agent("main", config={"tools": {"tool1": {}}})
         
         # Run
         graph = await agent._build_graph()
         
         # Assertions
         self.assertEqual(graph, "MockGraph")
         self.mock_create_graph.assert_called_once_with(
             llm=mock_llm_class.return_value,
             tools=[mock_tool1],
             prompt=unittest.mock.ANY,
             checkpointer=mock_sqlite_checkpointer.return_value,
             agent_id="main",
             config={"tools": {"tool1": {}}}
         )
 
     @patch('core.agent.graph_builder.get_agent_prompt')
     @patch('core.agent.graph_builder.SkillsLoader')
     @patch('core.agent.graph_builder.ToolsLoader')
     @patch('langchain_google_genai.ChatGoogleGenerativeAI')
     @patch('core.agent.graph_builder.SqliteCheckpointer')
     async def test_build_graph_filtering(self, mock_sqlite_checkpointer, mock_llm_class, mock_tool_loader_class, mock_skills_loader_class, mock_get_agent_prompt):
         # Setup mocks
         mock_get_agent_prompt.return_value = "Mock Agent Prompt"
 
         mock_tool1 = MagicMock()
         mock_tool1.name = "tool1"
         mock_tool_loader = MagicMock()
         mock_tool_loader_class.return_value = mock_tool_loader
         mock_tool_loader.get_tools.return_value = [mock_tool1]
         
         mock_skills_loader = MagicMock()
         mock_skills_loader_class.return_value = mock_skills_loader
         mock_skills_loader.get_skills_overview.return_value = "Mock Skills"
         
         agent = Agent("test_agent", config={"tools": {"tool1": {}}, "skills": ["skill1"]})
 
         graph = await agent._build_graph()
 
         self.mock_create_graph.assert_called_once_with(
             llm=unittest.mock.ANY,
             tools=[mock_tool1],
             prompt=unittest.mock.ANY,
             checkpointer=mock_sqlite_checkpointer.return_value,
             agent_id="test_agent",
             config={"tools": {"tool1": {}}, "skills": ["skill1"]}
         )

     @patch('core.agent.graph_builder.get_agent_prompt')
     @patch('core.agent.graph_builder.SkillsLoader')
     @patch('core.agent.graph_builder.ToolsLoader')
     @patch('langchain_google_genai.ChatGoogleGenerativeAI')
     @patch('core.agent.graph_builder.SqliteCheckpointer')
     @patch('core.agent.graph_builder.current_job_id')
     @patch('core.agent.graph_builder.JobManager')
     @patch('core.agent.graph_builder.interrupt')
     async def test_build_graph_wraps_tools(self, mock_interrupt, mock_job_manager_class, mock_current_job_id, mock_sqlite_checkpointer, mock_llm_class, mock_tool_loader_class, mock_skills_loader_class, mock_get_agent_prompt):
         # Setup mocks
         mock_get_agent_prompt.return_value = "Mock Agent Prompt"
         
         mock_tool1 = MagicMock()
         mock_tool1.name = "tool1"
         mock_tool1._run = MagicMock(return_value="tool_output")
         mock_tool1._arun = None
         
         mock_loader = MagicMock()
         mock_tool_loader_class.return_value = mock_loader
         mock_loader.get_tools.return_value = [mock_tool1]
         
         mock_skills_loader = MagicMock()
         mock_skills_loader_class.return_value = mock_skills_loader
         mock_skills_loader.get_skills_overview.return_value = "Mock Skills"
         
         agent = Agent("main", config={"tools": {"tool1": {}}})
         
         # Run
         await agent._build_graph()
         
         # Get the wrapped tool passed to create_graph
         wrapped_tools = self.mock_create_graph.call_args.kwargs["tools"]
         wrapped_tool = wrapped_tools[0]
         
         # Test normal execution
         mock_current_job_id.get.return_value = "job1"
         mock_job_manager = MagicMock()
         mock_job_manager_class.return_value = mock_job_manager
         mock_job = MagicMock()
         mock_job.status = "running"
         mock_job_manager._jobs = {"job1": mock_job}
         
         result = wrapped_tool._run()
         self.assertEqual(result, "tool_output")
         mock_interrupt.assert_not_called()
         
         # Test execution when job is killing
         mock_job.status = "killing"
         
         wrapped_tool._run()
         
         mock_job_manager.update_job.assert_called_with("job1", "killed")
         mock_interrupt.assert_called_once_with("Job was killed")

     @patch('core.agent.graph_builder.get_agent_prompt')
     @patch('core.agent.graph_builder.SkillsLoader')
     @patch('core.agent.graph_builder.ToolsLoader')
     @patch('core.agent.graph_builder.SqliteCheckpointer')
     async def test_build_graph_ollama(self, mock_sqlite_checkpointer, mock_tool_loader_class, mock_skills_loader_class, mock_get_agent_prompt):
         # Setup mocks
         mock_get_agent_prompt.return_value = "Mock Agent Prompt"
         
         mock_tool1 = MagicMock()
         mock_tool1.name = "tool1"
         mock_loader = MagicMock()
         mock_tool_loader_class.return_value = mock_loader
         mock_loader.get_tools.return_value = [mock_tool1]
         
         mock_skills_loader = MagicMock()
         mock_skills_loader_class.return_value = mock_skills_loader
         mock_skills_loader.get_skills_overview.return_value = "Mock Skills"
         
         agent = Agent("main", config={"provider": "ollama", "model": "gemma:4b", "tools": {"tool1": {}}})
         
         # Mock sys.modules to handle missing langchain_ollama
         import sys
         mock_ollama = MagicMock()
         mock_ollama_class = mock_ollama.ChatOllama
         mock_ollama_class.return_value = MagicMock()
         
         with patch.dict('sys.modules', {'langchain_ollama': mock_ollama}):
             # Run
             graph = await agent._build_graph()
         
         # Assertions
         self.assertEqual(graph, "MockGraph")
         self.mock_create_graph.assert_called_once_with(
             llm=mock_ollama_class.return_value,
             tools=[mock_tool1],
             prompt=unittest.mock.ANY,
             checkpointer=mock_sqlite_checkpointer.return_value,
             agent_id="main",
             config={"provider": "ollama", "model": "gemma:4b", "tools": {"tool1": {}}}
         )

     @patch('core.agent.graph_builder.get_agent_prompt')
     @patch('core.agent.graph_builder.SkillsLoader')
     @patch('core.agent.graph_builder.get_knowledge_prompt')
     def test_get_prompt_template_escapes_braces(self, mock_get_knowledge_prompt, mock_skills_loader_class, mock_get_agent_prompt):
          mock_get_agent_prompt.return_value = "Prompt with braces {} and {variable}"
          mock_skills_loader = MagicMock()
          mock_skills_loader_class.return_value = mock_skills_loader
          mock_skills_loader.get_skills_overview.return_value = "Skills with braces {}"
          mock_get_knowledge_prompt.return_value = "Knowledge with braces {}"
          
          from core.agent.graph_builder import GraphBuilder
          builder = GraphBuilder()
          
          prompt_fn = builder._get_prompt_template("main")
          self.assertTrue(callable(prompt_fn))
          
          # Call the dynamic prompt function with a mock state
          from langchain_core.messages import HumanMessage
          messages = prompt_fn({"messages": [HumanMessage(content="test message")]})
          
          # Verify that it compiled and returned formatted messages without template resolution errors
          self.assertTrue(any("Prompt with braces {} and {variable}" in msg.content for msg in messages))
          self.assertTrue(any("Skills with braces {}" in msg.content for msg in messages))
          self.assertTrue(any("Mock Subgraphs" in msg.content for msg in messages))
          self.assertTrue(any("Knowledge with braces {}" in msg.content for msg in messages))

     @patch('core.agent.graph_builder.get_agent_prompt')
     @patch('core.agent.graph_builder.SkillsLoader')
     @patch('core.agent.graph_builder.get_knowledge_prompt')
     def test_get_prompt_template_prunes_long_context(self, mock_get_knowledge_prompt, mock_skills_loader_class, mock_get_agent_prompt):
          mock_get_agent_prompt.return_value = "Agent System Prompt"
          mock_skills_loader = MagicMock()
          mock_skills_loader_class.return_value = mock_skills_loader
          mock_skills_loader.get_skills_overview.return_value = "Skills Prompt"
          mock_get_knowledge_prompt.return_value = "Knowledge Prompt"

          from core.agent.graph_builder import GraphBuilder
          from core.util.config import Config
          from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
          builder = GraphBuilder()

          # Configure pruner with low max_tokens via global Config
          Config().context_max_tokens = 1000
          Config().context_window_messages = 4

          with patch('core.agent.context_pruner.ContextPruner._summarize_with_graph_worker', return_value="Summary of previous turns."):
              prompt_fn = builder._get_prompt_template("main")

              # Create 20 messages exceeding 1000 tokens
              long_messages = []
              for i in range(10):
                  long_messages.append(HumanMessage(content=f"Query {i}: " + ("text " * 100)))
                  long_messages.append(AIMessage(content=f"Reply {i}: " + ("analysis " * 100)))

              formatted = prompt_fn({"messages": long_messages})

          # Verify that messages were pruned and include the summary tag
          has_summary = any(isinstance(m, SystemMessage) and "<conversation_summary>" in m.content for m in formatted)
          self.assertTrue(has_summary, "Expected conversation summary in formatted prompt messages")
          # Total user/assistant message count in formatted output should be bounded
          user_ai_msgs = [m for m in formatted if isinstance(m, (HumanMessage, AIMessage))]
          self.assertLessEqual(len(user_ai_msgs), 5)

     @patch('core.agent.graph_builder.get_agent_prompt')
     @patch('core.agent.graph_builder.SkillsLoader')
     @patch('core.agent.graph_builder.get_knowledge_prompt')
     @patch('core.agent.graph_builder.get_channel_prompt')
     @patch('core.agent.graph_builder.get_formatting_prompt')
     def test_prompt_template_ordering_for_caching(self, mock_formatting, mock_channel, mock_knowledge, mock_skills_loader_class, mock_agent):
          mock_formatting.return_value = "FORMATTING_RULES"
          mock_agent.return_value = "AGENT_PERSONA"
          mock_skills_loader = MagicMock()
          mock_skills_loader_class.return_value = mock_skills_loader
          mock_skills_loader.get_skills_overview.return_value = "SKILLS_OVERVIEW"
          self.mock_graphs_loader.get_graphs_overview.return_value = "SUBGRAPHS_OVERVIEW"
          mock_knowledge.return_value = "KNOWLEDGE_CONTEXT"
          mock_channel.return_value = "CHANNEL_CONTEXT"

          from core.agent.graph_builder import GraphBuilder
          from langchain_core.messages import HumanMessage, SystemMessage
          builder = GraphBuilder()
          prompt_fn = builder._get_prompt_template("main")
          formatted = prompt_fn({"messages": [HumanMessage(content="Hello")]})

          system_contents = [m.content for m in formatted if isinstance(m, SystemMessage)]
          self.assertEqual(system_contents, [
              "FORMATTING_RULES",
              "AGENT_PERSONA",
              "SKILLS_OVERVIEW",
              "SUBGRAPHS_OVERVIEW",
              "KNOWLEDGE_CONTEXT",
              "CHANNEL_CONTEXT"
          ])

if __name__ == "__main__":
    unittest.main()
