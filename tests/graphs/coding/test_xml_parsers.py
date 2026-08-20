import unittest
from graphs.coding.utils.xml_parsers import (
    parse_spec_validation_xml,
    parse_worker_handoff_xml,
    parse_critic_verdict_xml
)

class TestXMLParsers(unittest.TestCase):
    def test_parse_spec_validation_pass(self):
        xml_text = """
        <spec_validation_result>
          <verdict>PASS</verdict>
          <unambiguous>true</unambiguous>
          <missing_assumptions></missing_assumptions>
          <summary>Spec is 100% self-contained with explicit schemas.</summary>
        </spec_validation_result>
        """
        res = parse_spec_validation_xml(xml_text)
        self.assertEqual(res["verdict"], "PASS")
        self.assertTrue(res["passed"])
        self.assertTrue(res["unambiguous"])
        self.assertEqual(len(res["missing_assumptions"]), 0)

    def test_parse_spec_validation_fail(self):
        xml_text = """
        <spec_validation_result>
          <verdict>FAIL</verdict>
          <unambiguous>false</unambiguous>
          <missing_assumptions>
            <item>Missing database table schema for user profile</item>
            <item>Missing CLI verification command</item>
          </missing_assumptions>
          <summary>Unstated schema constraints.</summary>
        </spec_validation_result>
        """
        res = parse_spec_validation_xml(xml_text)
        self.assertEqual(res["verdict"], "FAIL")
        self.assertFalse(res["passed"])
        self.assertEqual(len(res["missing_assumptions"]), 2)
        self.assertIn("Missing CLI verification command", res["missing_assumptions"])

    def test_parse_worker_handoff(self):
        xml_text = """
        <worker_handoff>
          <status>READY_FOR_TEST</status>
          <modified_files>
            <file>src/services/auth.ts</file>
            <file>tests/auth.test.ts</file>
          </modified_files>
          <implementation_summary>Implemented JWT verification and session storage.</implementation_summary>
        </worker_handoff>
        """
        res = parse_worker_handoff_xml(xml_text)
        self.assertEqual(res["status"], "READY_FOR_TEST")
        self.assertEqual(len(res["modified_files"]), 2)
        self.assertEqual(res["modified_files"][0], "src/services/auth.ts")
        self.assertIn("JWT verification", res["implementation_summary"])

    def test_parse_critic_verdict_approve(self):
        xml_text = """
        <critic_verdict>
          <verdict>APPROVE</verdict>
          <anti_patterns_detected></anti_patterns_detected>
          <feedback_for_worker>Code is modular and conforms to error handling requirements.</feedback_for_worker>
        </critic_verdict>
        """
        res = parse_critic_verdict_xml(xml_text)
        self.assertEqual(res["verdict"], "APPROVE")
        self.assertTrue(res["passed"])
        self.assertEqual(len(res["anti_patterns_detected"]), 0)

    def test_parse_critic_verdict_reject(self):
        xml_text = """
        <critic_verdict>
          <verdict>REJECT</verdict>
          <anti_patterns_detected>
            <pattern>
              <rule>Fake It Trap</rule>
              <file>src/services/benefit.ts</file>
              <line_numbers>45-48</line_numbers>
              <evidence>return "mock_benefit_id_123"</evidence>
            </pattern>
          </anti_patterns_detected>
          <feedback_for_worker>Replace dummy return with real calculation.</feedback_for_worker>
        </critic_verdict>
        """
        res = parse_critic_verdict_xml(xml_text)
        self.assertEqual(res["verdict"], "REJECT")
        self.assertFalse(res["passed"])
        self.assertEqual(len(res["anti_patterns_detected"]), 1)
        self.assertEqual(res["anti_patterns_detected"][0]["rule"], "Fake It Trap")
        self.assertEqual(res["anti_patterns_detected"][0]["line_numbers"], "45-48")

if __name__ == "__main__":
    unittest.main()
