# AGENTS.md

## Operating Instructions
1. **Chat / Voice (Sparring)**: follow the `google_sparring` skill. Use the `filesystem` tool to read recent daily journals from `pkm/vault/journals/google/` for context. Stress-test decisions using Altitude, Depth, and Range.
2. **Weekly Rollups**: Handled by a scheduled script (to be requested via workflow_change) that extracts high-altitude signals into `pkm/vault/journals/google/weekly_rollups.md`.