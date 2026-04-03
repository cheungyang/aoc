---
name: sessions
description: Manage multi-turn conversation memory sessions across channels and threads.
---
## Available scripts
- **`scripts/session_manager.py`** — Append or load session JSON messages history logs.

## Workflow
Automated by the bot lifecycle hooks.
Sessions persistent files are saved in the `sessions/` directory structured by platform:channel:threadId locator.

## Available Tools
- **`read_session_history(session_id: str, limit: int)`** — Programmatically dump inspect inspect past conversation logs bypassing live budget caps caps.
