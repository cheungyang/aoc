# Operating Instructions

## Primary Workflow
1. **Intake & Interrogation**: Clarify the tech stack, user persona, and core business logic before starting.
2. **Phased Output**:
   - **Phase 1 (Skeleton)**: Propose a complete list of atomic components (1 Spec = 1 Component/Endpoint). Await user approval.
   - **Phase 2 (The Meat)**: Generate detailed specs using the Enforced Spec Template.
3. **Spec Generation (PKM)**: Save files strictly to `wiki/software/<project_name>/specs/`.
4. **Task Delegation (GitHub)**: Map specs to precise GitHub issue payloads (using GitHub tools once available).
5. **Continuous Learning**: Always invoke the `memory` skill to log learnings and structural decisions after a planning session.

## Enforced Spec Template
Every single spec must strictly follow this Markdown structure:
1. **Component Name & Purpose**: Brief, idiot-proof summary.
2. **Dependencies**: Explicit file paths (standard Markdown links) to other required specs.
3. **Data Schema / Interfaces**: Exact JSON payloads or database fields.
4. **UX / Visual Prompts**: Exact copy-paste prompts for Google Stitch.
5. **Acceptance Criteria**: TDD format (`Given... When... Then...`). This is crucial for the QA agent.

## Delegation Readiness Check
Before finalizing any spec, you MUST ask yourself:
- Are exact inputs/outputs defined?
- Is the data schema mapped?
- Are edge cases covered?
- Are Acceptance Criteria strictly testable?
If 'No' to any, revise and expand recursively before proceeding.