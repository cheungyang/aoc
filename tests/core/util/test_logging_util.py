import os
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.util.logging_util import extract_model_name, format_tool_extra_str
from tests.helpers import LLM_USAGE as USAGE, llm_result as _llm_result


class TestModelName(unittest.TestCase):
    """The `model` column was always 'unknown': genai's llm_output has no model_name."""

    def test_reads_model_from_response_metadata(self):
        result = _llm_result(
            usage=USAGE,
            response_metadata={"model_name": "gemini-3.1-pro-preview"},
            llm_output={"prompt_feedback": {}},
        )
        self.assertEqual(extract_model_name(result), "gemini-3.1-pro-preview")

    def test_reads_model_key_when_model_name_absent(self):
        result = _llm_result(usage=USAGE, response_metadata={"model": "qwen3:8b"})
        self.assertEqual(extract_model_name(result), "qwen3:8b")

    def test_reads_generation_info(self):
        result = _llm_result(usage=USAGE, generation_info={"model_name": "gemini-flash"})
        self.assertEqual(extract_model_name(result), "gemini-flash")

    def test_falls_back_to_llm_output(self):
        result = _llm_result(usage=USAGE, llm_output={"model_name": "gpt-x"})
        self.assertEqual(extract_model_name(result), "gpt-x")

    def test_unknown_when_nowhere(self):
        result = _llm_result(usage=USAGE, llm_output=None)
        self.assertEqual(extract_model_name(result), "unknown")

    def test_response_metadata_wins_over_llm_output(self):
        result = _llm_result(
            usage=USAGE,
            response_metadata={"model_name": "from-message"},
            llm_output={"model_name": "from-llm-output"},
        )
        self.assertEqual(extract_model_name(result), "from-message")


class TestFormatToolExtraStr(unittest.TestCase):
    def test_format_filesystem_multiple_instructions_dict(self):
        input_data = {
            "instructions": [
                {"action": "ls", "path": "{directory1}"},
                {"action": "read", "path": "{file2}"}
            ]
        }
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, ' [action "ls" on {directory1}, "read" on {file2}]')

    def test_format_filesystem_multiple_instructions_str(self):
        input_str = "{'instructions': [{'action': 'ls', 'path': '/var/log'}, {'action': 'read', 'path': '/etc/hosts'}]}"
        res = format_tool_extra_str(input_str)
        self.assertEqual(res, ' [action "ls" on /var/log, "read" on /etc/hosts]')

    def test_format_filesystem_single_instruction(self):
        input_data = {"instructions": [{"action": "ls", "path": "/tmp"}]}
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, ' [action "ls" on /tmp]')

    def test_format_filesystem_action_only(self):
        input_data = {"instructions": [{"action": "list_all"}]}
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, ' [action "list_all"]')

    def test_format_filesystem_path_only(self):
        input_data = {"instructions": [{"path": "/tmp/test"}]}
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, ' [action on /tmp/test]')

    def test_format_filesystem_serialized_json_instructions(self):
        input_data = {'instructions': '[{"action": "ls", "path": "/tmp"}]'}
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, ' [action "ls" on /tmp]')

    def test_format_action_path_skill_id(self):
        input_data = {"action": "create", "path": "/tmp", "skill_id": "skill_123"}
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, " [action: create, path: /tmp, skill_id: skill_123]")

    def test_format_path_only(self):
        input_str = "{'command': 'status', 'path': '/repo'}"
        res = format_tool_extra_str(input_str)
        self.assertEqual(res, " [path: /repo]")

    def test_format_skill_id_only(self):
        input_data = {"skill_id": "code_search", "agent_id": "main"}
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, " [skill_id: code_search]")

    def test_format_action_only(self):
        input_str = "{'action': 'search', 'query': 'fix bug'}"
        res = format_tool_extra_str(input_str)
        self.assertEqual(res, " [action: search]")

    def test_format_no_matching_keys(self):
        input_data = {"query": "python langgraph"}
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, "")

    def test_format_graph_name(self):
        input_data = {"graph_name": "coding", "query": "implement feature"}
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, " [graph_id: coding]")

    def test_format_graph_id(self):
        input_str = "{'graph_id': 'content_creation', 'query': 'create post'}"
        res = format_tool_extra_str(input_str)
        self.assertEqual(res, " [graph_id: content_creation]")

    def test_format_subgraph_name(self):
        input_data = {"subgraph_name": "coding", "query": "fix bug"}
        res = format_tool_extra_str(input_data)
        self.assertEqual(res, " [graph_id: coding]")

    def test_format_agent_call_with_tool_name(self):
        input_data = {"agent_id": "graph-worker", "prompt": "run task", "channel": "dev"}
        res = format_tool_extra_str(input_data, tool_name="agent_call")
        self.assertEqual(res, " [agent_id: graph-worker]")

    def test_format_agent_id_standalone(self):
        input_str = "{'agent_id': 'graph-worker', 'prompt': 'run task'}"
        res = format_tool_extra_str(input_str)
        self.assertEqual(res, " [agent_id: graph-worker]")

    def test_format_empty_or_none_or_invalid_inputs(self):
        self.assertEqual(format_tool_extra_str(None), "")
        self.assertEqual(format_tool_extra_str({}), "")
        self.assertEqual(format_tool_extra_str(""), "")
        self.assertEqual(format_tool_extra_str("invalid {string"), "")
        self.assertEqual(format_tool_extra_str(12345), "")


if __name__ == "__main__":
    unittest.main()
