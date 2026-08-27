import unittest
import os
import tempfile
from unittest.mock import patch, AsyncMock
from tools.spec_validator import spec_validator

class TestSpecValidatorTool(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.spec_path = os.path.join(self.temp_dir.name, "feature_spec.md")
        with open(self.spec_path, "w", encoding="utf-8") as f:
            f.write("# Spec\nAllowed files: auth.py\nSchema: AuthToken\nGiven auth When valid Then pass\nVerification: pytest")

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_spec_validator_missing_spec_path(self):
        res = await spec_validator.ainvoke({"spec_path": ""})
        self.assertIn("Error: 'spec_path' is required", res)

    async def test_spec_validator_nonexistent_file(self):
        res = await spec_validator.ainvoke({"spec_path": "nonexistent_spec_file_12345.md"})
        self.assertIn("Path not found", res)

    async def test_spec_validator_success_pass(self):
        with patch("tools.agent_call.agent_call") as mock_agent:
            mock_agent.ainvoke = AsyncMock(return_value="""
            <spec_validation_result>
              <verdict>PASS</verdict>
              <unambiguous>true</unambiguous>
              <missing_assumptions></missing_assumptions>
              <summary>Spec is fully self-contained and verified.</summary>
            </spec_validation_result>
            """)
            res = await spec_validator.ainvoke({
                "spec_path": self.spec_path
            })
            self.assertIn("<verdict>PASS</verdict>", res)
            self.assertIn("<unambiguous>true</unambiguous>", res)

    async def test_spec_validator_fails_closed_on_agent_exception(self):
        with patch("tools.agent_call.agent_call") as mock_agent:
            mock_agent.ainvoke = AsyncMock(side_effect=RuntimeError("Worker timeout"))
            res = await spec_validator.ainvoke({
                "spec_path": self.spec_path
            })
            self.assertIn("<verdict>FAIL</verdict>", res)
            self.assertIn("Worker timeout", res)

if __name__ == "__main__":
    unittest.main()
