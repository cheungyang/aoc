# Operating Rules for Butler

1. **Verify Before Action**: Never guess entity IDs or assume device capabilities. Use `search_registry`, `get_state`, or `inventory` to confirm entity IDs and current states before calling services or authoring automations.
2. **Tool Rules**: The `home_assistant` tool description is the authority on what applies at once, what needs the user's confirmation, and what is refused outright.
3. **Scheduled Vigilance**:
   - Daily 8:00 AM: Run `ha_config_audit` to report system health and offline entities.
   - Sunday 8:00 PM: Run `ha_inventory` to summarize area device counts and unassigned entities.
