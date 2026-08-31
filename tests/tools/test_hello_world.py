import unittest
import os
import sys

# Inject root (2 levels deep)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.hello_world import hello_world
from core.util import format_tool_response


class TestHelloWorldTool(unittest.TestCase):

    def test_hello_world_func(self):
        result = hello_world.func()
        expected = format_tool_response("hello_world", payload="Hello world, Alva!", errors="None")
        self.assertEqual(result, expected)
        self.assertIn("<payload>Hello world, Alva!</payload>", result)
        self.assertIn("<errors>None</errors>", result)

    def test_hello_world_invoke(self):
        result = hello_world.invoke({})
        expected = format_tool_response("hello_world", payload="Hello world, Alva!", errors="None")
        self.assertEqual(result, expected)
        self.assertIn("<payload>Hello world, Alva!</payload>", result)
        self.assertIn("<errors>None</errors>", result)


if __name__ == '__main__':
    unittest.main()
