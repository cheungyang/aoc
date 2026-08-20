def build_critic_prompt(spec_text: str, git_diff_text: str) -> str:
    """Builds the Goldfish 2 Critic prompt for anti-pattern code auditing."""
    return f"""<playbook>
Role: Independent QA & Anti-Pattern Auditor (Goldfish 2)
Task: Review git diff against the master spec. Hunt for LLM short-cuts and anti-patterns.
Constraint: Zero prior memory. If the implementation takes shortcuts, you MUST REJECT.
</playbook>

<master_spec>
{spec_text}
</master_spec>

<git_diff>
{git_diff_text}
</git_diff>

<anti_pattern_checklist>
1. Fake It Trap: Hardcoded dummy responses or mock returns instead of real business/data logic.
2. Happy Path Bias: Missing error handlers, missing null/undefined checks, unhandled edge cases.
3. Silent Failure: Swallowed catch/except blocks with no logging or error bubbling.
4. Bloated Files: Modified files exceeding 150 lines without modularization.
</anti_pattern_checklist>

<output_format>
Output ONLY the <critic_verdict> XML block conforming to:
<critic_verdict>
  <verdict>APPROVE | REJECT</verdict>
  <anti_patterns_detected>
    <pattern>
      <rule>Fake It Trap | Happy Path Bias | Silent Failure | Bloated Files</rule>
      <file>path/to/file</file>
      <line_numbers>12-18</line_numbers>
      <evidence>Hardcoded return 'dummy'</evidence>
    </pattern>
  </anti_patterns_detected>
  <feedback_for_worker>Specific remediation instructions</feedback_for_worker>
</critic_verdict>
</output_format>"""
