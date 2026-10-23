# note-taker Classification

| Part | Classification | Details |
| :--- | :--- | :--- |
| **Identity & Tone** | `agent.json` & `SOUL.md` | Persona: sounding board, factual synthesis, no life advice. Model: FLASH. Voice: Puck. |
| **Permissions** | `agent.json` | `filesystem`: `@read`, `@write` on `pkm/inbox/` and `pkm/vault/`. `set_thinking` to control voice depth. |
| **Setup** | Skill: `setup_session` | Asks if new note or append. If append, takes keyword, searches, confirms path before rambling starts. |
| **Active Listening & Note Maintenance** | Skill: `active_listening` | Listens, structures notes, writes to file, speaks synthesis back. Interrupts if garbled. |
| **Thresholds** | `RULES.md` | `new_file_prefix` |