import unittest
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.agent import Agent

class TestAgent(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from core.agent.job_manager import JobManager
        JobManager._instance = None


    @patch('core.agent.agent.LoggingHandler')
    async def test_execute_success(self, mock_logging_handler_class):
        # Setup mocks
        mock_logging_handler = MagicMock()
        mock_logging_handler_class.return_value = mock_logging_handler

        # Graph invoke result
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Reply text")]})

        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        # Run
        reply = await agent.execute("hello", "session1")
        
        # Assertions
        mock_graph.ainvoke.assert_called_once()
        self.assertEqual(reply, "Reply text")

    

    @patch('core.agent.agent.LoggingHandler')
    async def test_execute_invoke_failure(self, mock_logging_handler_class):
        # Graph invoke throws exception
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(side_effect=Exception("Invoke failed"))

        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        # Run
        reply = await agent.execute("hello", "session1")
        
        # Assertions
        self.assertEqual(reply, "Sorry, I encountered an error processing the request.")

    @patch('core.agent.agent.LoggingHandler')
    @patch('core.agent.session_manager.SessionManager.get_session_id')
    async def test_execute_parsing_failure(self, mock_get_session_id, mock_logging_handler_class):
        # Graph invoke succeeds but returns empty messages list (causing IndexError)
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": []})

        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        # Run and Expect IndexError
        with self.assertRaises(IndexError):
            await agent.execute("hello", "session1")

    @patch('core.agent.agent.LoggingHandler')
    @patch('core.knowledge.memory.sqlite_checkpointer.SqliteCheckpointer.delete_thread')
    async def test_execute_retry_on_corrupt_checkpointer(self, mock_delete_thread, mock_logging_handler_class):
        # Graph invoke throws corrupt checkpointer exception on first call, succeeds on second
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock()
        mock_graph.ainvoke.side_effect = [
            Exception("Found AIMessages with tool_calls that do not have a corresponding ToolMessage"),
            {"messages": [MagicMock(content="Success after retry")]}
        ]

        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        # Run
        reply = await agent.execute("hello", "session1")
        
        # Assertions
        self.assertEqual(mock_graph.ainvoke.call_count, 2)
        mock_delete_thread.assert_called_once_with("test-agent:session1")
        self.assertEqual(reply, "Success after retry")

    @patch('core.agent.agent.LoggingHandler')
    async def test_execute_list_content_with_none(self, mock_logging_handler_class):
        # Graph invoke result with a list content containing None
        mock_graph = MagicMock()
        mock_message = MagicMock()
        mock_message.content = [
            {"type": "text", "text": "Part 1 "},
            {"type": "text", "text": None},
            {"type": "text", "text": "Part 2"}
        ]
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [mock_message]})

        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        # Run
        reply = await agent.execute("hello", "session1")
        
        # Assertions
        self.assertEqual(reply, "Part 1 Part 2")


    async def test_execute_empty_content(self):
        agent = Agent("test-agent", {})
        
        # Run with empty string
        reply = await agent.execute("", "session1")
        self.assertEqual(reply, "I cannot process empty messages. Please provide some text.")
        
        # Run with whitespace
        reply = await agent.execute("   ", "session1")
        self.assertEqual(reply, "I cannot process empty messages. Please provide some text.")

    @patch('core.agent.agent.LoggingHandler')
    async def test_execute_list_content(self, mock_logging_handler_class):
        # Setup mocks
        mock_logging_handler = MagicMock()
        mock_logging_handler_class.return_value = mock_logging_handler

        # Graph invoke result
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Reply text")]})

        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        # Run with list content
        list_content = [{"type": "text", "text": "hello"}]
        reply = await agent.execute(list_content, "session1")
        
        # Assertions
        mock_graph.ainvoke.assert_called_once()
        self.assertEqual(reply, "Reply text")
        
        # Verify inputs passed to graph
        args, kwargs = mock_graph.ainvoke.call_args
        self.assertEqual(args[0]["messages"][0]["content"], list_content)

    async def test_execute_empty_list_content(self):
        agent = Agent("test-agent", {})
        
        # Run with empty list
        reply = await agent.execute([], "session1")
        self.assertEqual(reply, "I cannot process empty messages. Please provide some text.")


    @patch('core.agent.agent.LoggingHandler')
    @patch('core.agent.agent.os.path.exists')
    @patch('discord.File')
    async def test_execute_sends_images(self, mock_discord_file, mock_exists, mock_logging_handler_class):
        mock_exists.return_value = True
        mock_file_instance = MagicMock()
        mock_discord_file.return_value = mock_file_instance

        # Graph invoke result with <images> tag
        mock_graph = MagicMock()
        reply_with_images = """Here is an image.
<images>
  <image path="assets/test.png"/>
</images>"""
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content=reply_with_images)]})

        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        mock_channel = AsyncMock()
        
        # Run
        reply = await agent.execute("hello", source="discord", channel=mock_channel)
        
        # Assertions
        mock_graph.ainvoke.assert_called_once()
        self.assertEqual(reply, "Here is an image.")
        
        # Verify channel.send was called with files
        mock_channel.send.assert_called_once_with("Here is an image.", files=[mock_file_instance])
        mock_discord_file.assert_called_once_with("assets/test.png")

    @patch('core.agent.agent.LoggingHandler')
    @patch('core.agent.agent.os.path.exists')
    async def test_execute_sends_missing_image_message(self, mock_exists, mock_logging_handler_class):
        mock_exists.return_value = False

        # Graph invoke result with <images> tag
        mock_graph = MagicMock()
        reply_with_images = """Here is an image.
<images>
  <image path="assets/test.png"/>
</images>"""
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content=reply_with_images)]})

        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        mock_channel = AsyncMock()
        
        # Run
        reply = await agent.execute("hello", source="discord", channel=mock_channel)
        
        # Assertions
        mock_graph.ainvoke.assert_called_once()
        self.assertEqual(reply, "Here is an image.")
        
        # Verify channel.send was called twice: once for message, once for error
        self.assertEqual(mock_channel.send.call_count, 2)
        mock_channel.send.assert_any_call("Here is an image.")
        mock_channel.send.assert_any_call("Image file not found: assets/test.png")

    @patch('core.agent.agent.LoggingHandler')
    @patch('core.agent.agent.os.path.exists')
    @patch('discord.File')
    async def test_execute_sends_images_only(self, mock_discord_file, mock_exists, mock_logging_handler_class):
        mock_exists.return_value = True
        mock_file_instance = MagicMock()
        mock_discord_file.return_value = mock_file_instance

        # Graph invoke result with ONLY <images> tag
        mock_graph = MagicMock()
        reply_with_images = """<images>
  <image path="assets/test.png"/>
</images>"""
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content=reply_with_images)]})

        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        mock_channel = AsyncMock()
        
        # Run
        reply = await agent.execute("hello", source="discord", channel=mock_channel)
        
        # Assertions
        mock_graph.ainvoke.assert_called_once()
        self.assertEqual(reply, "")
        
        # Verify channel.send was called once with empty content and files
        mock_channel.send.assert_called_once_with("", files=[mock_file_instance])
        mock_discord_file.assert_called_once_with("assets/test.png")

    @patch('core.agent.agent.LoggingHandler')
    @patch('core.agent.agent.os.path.exists')
    @patch('discord.File')
    async def test_execute_sends_videos(self, mock_discord_file, mock_exists, mock_logging_handler_class):
        mock_exists.return_value = True
        mock_file_instance = MagicMock()
        mock_discord_file.return_value = mock_file_instance

        # Graph invoke result with <videos> tag
        mock_graph = MagicMock()
        reply_with_videos = """Here is a video.
<videos>
  <video path="assets/test.mp4"/>
</videos>"""
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content=reply_with_videos)]})

        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        mock_channel = AsyncMock()
        
        # Run
        reply = await agent.execute("hello", source="discord", channel=mock_channel)
        
        # Assertions
        mock_graph.ainvoke.assert_called_once()
        self.assertEqual(reply, "Here is a video.")
        
        # Verify channel.send was called with files
        mock_channel.send.assert_called_once_with("Here is a video.", files=[mock_file_instance])
        mock_discord_file.assert_called_once_with("assets/test.mp4")

    @patch('core.agent.agent.LoggingHandler')
    @patch('core.agent.agent.os.path.exists')
    async def test_execute_sends_missing_video_message(self, mock_exists, mock_logging_handler_class):
        mock_exists.return_value = False

        # Graph invoke result with <videos> tag
        mock_graph = MagicMock()
        reply_with_videos = """Here is a video.
<videos>
  <video path="assets/test.mp4"/>
</videos>"""
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content=reply_with_videos)]})

        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        mock_channel = AsyncMock()
        
        # Run
        reply = await agent.execute("hello", source="discord", channel=mock_channel)
        
        # Assertions
        mock_graph.ainvoke.assert_called_once()
        self.assertEqual(reply, "Here is a video.")
        
        # Verify channel.send was called twice: once for message, once for error
        self.assertEqual(mock_channel.send.call_count, 2)
        mock_channel.send.assert_any_call("Here is a video.")
        mock_channel.send.assert_any_call("Video file not found: assets/test.mp4")

    @patch('core.agent.agent.LoggingHandler')
    @patch('core.agent.agent.current_job_id')
    @patch('core.agent.job_manager.JobManager')
    async def test_execute_handles_killed_status(self, mock_job_manager_class, mock_current_job_id, mock_logging_handler_class):
        # Setup mocks
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Reply text")]})
        mock_graph.get_state.return_value = MagicMock(next=["some_node"])
        
        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        mock_job_manager = MagicMock()
        mock_job_manager_class.return_value = mock_job_manager
        mock_job = MagicMock()
        mock_job.status = "killed"
        mock_job_manager._jobs = {"test-agent:job:123": mock_job}
        
        # We need to mock new_job_id to return a fixed id
        mock_job_manager.new_job_id.return_value = "test-agent:job:123"
        
        # Run
        await agent.execute("hello", "session1")
        
        # Assertions
        # It should NOT update job to partial because it is killed
        mock_job_manager.update_job.assert_called_with("test-agent:job:123", "running")
        
        # And not called with "partial" or "completed"
        calls = mock_job_manager.update_job.call_args_list
        status_updates = [call[0][1] for call in calls]
        self.assertNotIn("partial", status_updates)
        self.assertNotIn("completed", status_updates)

    @patch('core.agent.agent.LoggingHandler')
    @patch('core.agent.job_manager.JobManager')
    async def test_execute_passes_initial_prompt_to_job_manager(self, mock_job_manager_class, mock_logging_handler_class):
        mock_job_manager = MagicMock()
        mock_job_manager_class.return_value = mock_job_manager
        mock_job_manager.new_job_id.return_value = "test-job-id"
        
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Reply text")]})
        
        agent = Agent("test-agent", {})
        agent.graph = mock_graph
        
        await agent.execute("hello world", "session1")
        
        mock_job_manager.add_job.assert_called_once_with(
            "test-job-id", "test-agent", "test-agent:session1", initial_prompt="hello world"
        )

    @patch('core.agent.agent.LoggingHandler')
    async def test_execute_suppresses_channel_send_for_tool_and_subgraph(self, mock_logging_handler_class):
        """
        Tests the problem & fix:
        Sub-agents invoked via tools (source="tool", e.g., agent_call) or subgraphs (source="subgraph")
        must NOT post messages directly to Discord, even if a channel is passed for session context.
        Only the orchestrator (or direct user/scheduled invocations) should send messages to Discord.
        """
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Subagent internal response")]})

        agent = Agent("sub-agent", {})
        agent.graph = mock_graph

        mock_channel = AsyncMock()

        # 1. When source="tool" (e.g., invoked via agent_call)
        tool_reply = await agent.execute("run subtask", source="tool", channel=mock_channel)
        self.assertEqual(tool_reply, "Subagent internal response")
        mock_channel.send.assert_not_called()

        # 2. When source="subgraph" (e.g., invoked within a graph node)
        subgraph_reply = await agent.execute("critique prompt", source="subgraph", channel=mock_channel)
        self.assertEqual(subgraph_reply, "Subagent internal response")
        mock_channel.send.assert_not_called()

    @patch('core.agent.agent.LoggingHandler')
    async def test_execute_sends_channel_message_for_scheduled_and_discord(self, mock_logging_handler_class):
        """
        Verifies that direct user interactions (source="discord") and automated cron tasks
        (source="scheduled") DO post messages to the Discord channel.
        """
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Scheduled report")]})

        agent = Agent("cron-agent", {})
        agent.graph = mock_graph

        mock_channel = AsyncMock()

        # 1. When source="scheduled"
        reply = await agent.execute("daily check", source="scheduled", channel=mock_channel)
        self.assertEqual(reply, "Scheduled report")
        mock_channel.send.assert_called_once_with("Scheduled report")

        # 2. When source="discord"
        mock_channel.reset_mock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Direct user response")]})
        reply_discord = await agent.execute("user query", source="discord", channel=mock_channel)
        self.assertEqual(reply_discord, "Direct user response")
        mock_channel.send.assert_called_once_with("Direct user response")

    @patch('core.agent.session_manager.SessionManager.clear_session')
    async def test_execute_new_command(self, mock_clear_session):
        mock_clear_session.return_value = "archived"
        agent = Agent("test-agent", {})
        mock_channel = AsyncMock()

        await agent.execute("[new]", source="discord", channel=mock_channel)
        mock_clear_session.assert_called_once()
        mock_channel.send.assert_called_once_with("Session context cleared. archived")

    @patch('core.agent.session_manager.SessionManager.clear_sessions')
    async def test_execute_newall_command(self, mock_clear_sessions):
        mock_clear_sessions.return_value = "all archived"
        agent = Agent("test-agent", {})
        mock_channel = AsyncMock()

        await agent.execute("[newall]", source="discord", channel=mock_channel)
        mock_clear_sessions.assert_called_once()
        mock_channel.send.assert_called_once_with("All session contexts cleared. all archived")

    @patch('os.execv')
    async def test_execute_restart_command(self, mock_execv):
        agent = Agent("test-agent", {})
        mock_channel = AsyncMock()

        await agent.execute("[restart]", source="discord", channel=mock_channel)
        mock_channel.send.assert_called_once_with("System is restarting...")
        mock_execv.assert_called_once_with(sys.executable, [sys.executable] + sys.argv)

    @patch('os.execv')
    async def test_execute_restart_command_no_channel(self, mock_execv):
        agent = Agent("test-agent", {})

        await agent.execute("[restart]", source="discord", channel=None)
        mock_execv.assert_called_once_with(sys.executable, [sys.executable] + sys.argv)

if __name__ == "__main__":
    unittest.main()




