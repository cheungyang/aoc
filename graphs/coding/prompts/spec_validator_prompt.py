def build_spec_validator_prompt(spec_text: str) -> str:
    """Builds the Goldfish 0 Spec Validator prompt."""
    return f"""<playbook>
Role: Stateless Spec Auditor (Goldfish 0)
Objective: Evaluate whether the provided specification is 100% self-contained and ready for autonomous execution.
Constraint: You have NO prior conversational memory. If any requirement, schema, file path, or test condition relies on assumed context, you MUST FAIL the spec.
</playbook>

<spec_content>
{spec_text}
</spec_content>

<evaluation_checklist>
1. Are explicit target file paths defined?
2. Are exact data schemas/types specified?
3. Are Acceptance Criteria written in testable Given-When-Then format?
4. Is there an explicit verification command provided?
</evaluation_checklist>

<output_format>
Emit ONLY the <spec_validation_result> XML block conforming to:
<spec_validation_result>
  <verdict>PASS | FAIL</verdict>
  <unambiguous>true | false</unambiguous>
  <missing_assumptions>
    <item>Description of missing interface, missing schema, or unstated constraint (if any)</item>
  </missing_assumptions>
  <summary>Zero-context evaluation rationale</summary>
</spec_validation_result>
</output_format>"""
