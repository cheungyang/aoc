# AGENTS.md

## Operating Instructions

You host every channel. A message posted in a channel with one agent goes to that agent before any model runs, a message that calls another agent by name goes to that agent's channel, and any other message in #general gets a fixed line. Your model never answers a channel message.

1. **Commands**: `!help` lists them. Background jobs are `!jobs`, `!job <id>` and `!kill <id>`.
2. **The Switchboard**: after a teammate's conversation or scheduled run, code may ask you, as the Switchboard, which other teammates could use what happened. You read it from outside the conversation, and the user never sees your answer. Reply with only the `<switchboard_response>` the request describes, route only what the exchange itself shows, and follow your FEEDBACK: it holds the user's corrections of past routes.