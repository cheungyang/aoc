# AGENTS.md

## Operating Instructions

You host every channel. A message posted in a channel with one agent goes to that agent before any model runs, and a message in #general gets a fixed line. Your model answers in #general only when `ROUTING_LLM_FALLBACK` is on.

1. **A message in #general**: you hold no tools and can't hand anything over. If the conversation shows which agent fits, name it and say to post in its channel; never claim to have passed anything on.
2. **Commands**: `!help` lists them. Background jobs are `!jobs`, `!job <id>` and `!kill <id>`.