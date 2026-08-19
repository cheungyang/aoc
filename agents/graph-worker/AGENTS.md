# INSTRUCTIONS: Generic LangGraph Node Execution

## Workflow
1. **State Ingestion:** Always locate the `<current_state>`, `<playbook>`, and `<assigned_task>` in your prompt.
2. **Adopt the Playbook:** Completely assume the role, constraints, and formatting requirements dictated by the provided playbook.
3. **Scoped Actions:** Use the `filesystem` tool ONLY on the specific file paths provided to you dynamically in the `<current_state>`. Never hallucinate paths or guess directory structures.
4. **Output Generation:** Execute the assigned task and output the exact required IPC XML payload. Do not provide conversational markdown outside of the XML tags unless explicitly instructed by the playbook.