from typing import List, Optional

def build_coder_prompt(
    workspace_path: str,
    task_id: str,
    spec_path: str,
    allowed_files: List[str],
    acceptance_criteria: str,
    verification_command: str,
    spec_content: Optional[str] = None,
    test_stderr: Optional[str] = None,
    critic_feedback: Optional[str] = None,
    human_feedback: Optional[str] = None,
    privacy_findings: Optional[str] = None
) -> str:
    """Builds the Goldfish 1 Coder Worker prompt with strict boundary and targeted retry delta."""
    files_formatted = "\n".join(f"- {f}" for f in (allowed_files or []))
    if not files_formatted:
        files_formatted = "- (All files within workspace)"

    retry_blocks = []
    if test_stderr:
        retry_blocks.append(f"""<test_failure_traceback>
The previous test run failed with the following error output:
{test_stderr}
Fix the code to resolve this failure.
</test_failure_traceback>""")

    if critic_feedback:
        retry_blocks.append(f"""<critic_anti_pattern_feedback>
Independent QA audit detected the following anti-patterns:
{critic_feedback}
Refactor the implementation to eliminate all listed anti-patterns.
</critic_anti_pattern_feedback>""")

    if human_feedback:
        retry_blocks.append(f"""<human_reviewer_feedback>
The human reviewer requested the following modifications:
{human_feedback}
Address all feedback thoroughly.
</human_reviewer_feedback>""")

    # Where and what kind only: the findings never carry the matched words,
    # and the worker is told not to repeat them, because its summary becomes
    # the public PR body.
    if privacy_findings:
        retry_blocks.append(f"""<privacy_findings>
Your last change was not pushed: the privacy check found lines that look like the user's personal data.
{privacy_findings}
Rewrite each with made-up text in the same shape: same format, invented names, places, amounts and dates; example.com emails and 555-01xx phone numbers.
Don't quote the original anywhere, including your implementation summary.
</privacy_findings>""")

    retry_context_block = "\n\n".join(retry_blocks) if retry_blocks else ""

    spec_section = f"""\n<spec_content>\n{spec_content}\n</spec_content>""" if spec_content else ""

    return f"""<playbook>
Role: Autonomous Software Coder (Goldfish 1)
Execution Boundary: STRICTLY inside workspace {workspace_path}
No-Code Spec Adherence: Do not deviate from the interfaces defined in the task.
Tool Instructions: Use your filesystem tools to create or modify code in {workspace_path}. Only touch files within Allowed Files.
Test Data: Everything you write may be published, so make test data up and never copy it from the user's files or notes: invented names, places, amounts and dates; example.com emails and 555-01xx phone numbers.
</playbook>

<assigned_task>
Task ID: {task_id}
Master Spec: {spec_path}
Allowed Files:
{files_formatted}
Acceptance Criteria:
{acceptance_criteria}
Verification Command: {verification_command}
</assigned_task>
{spec_section}
{retry_context_block}

<output_format>
When you have implemented all changes, output ONLY the <worker_handoff> XML block:
<worker_handoff>
  <status>READY_FOR_TEST</status>
  <modified_files>
    <file>relative/path/to/modified_file</file>
  </modified_files>
  <implementation_summary>Concise description of changes made</implementation_summary>
</worker_handoff>
</output_format>"""
