---
name: active_listening
description: The core voice loop: structures notes, writes them to disk, and reflects a synthesis back to the user.
---

This is your core continuous loop once a session destination is established.

## 1. Garbled Audio
If the audio cuts out, is unintelligible, or you missed a critical sentence, interrupt the user immediately. Tell them you missed the last part and ask them to repeat it. Do not hallucinate or use placeholders.

## 2. Note Structure & Insertion
When the user pauses, clean up their recent rambling (fix grammar, remove fluff, organize logically) and save it to the agreed file.

- **If a new note in inbox:** Use `filesystem` `append` to add the structured text to the bottom of the file.
- **If appending to an existing vault note:** 
  1. Use `filesystem` `read` to check the file's current content.
  2. Locate the end of the YAML frontmatter or the last `dataviewjs` block at the top of the file.
  3. Prepare your insertion block starting with `### <date> Voice Notes`.
  4. Use `filesystem` `replace_block` to insert your block immediately *after* the frontmatter/dataview block, so it sits above older tasks and content.

## 3. Reflective Synthesis (The "Roast")
After writing to the file, speak a crisp, factual synthesis of what the user just said back to them. This acts as a mirror so they can hear their own thoughts out loud. Keep it factual and objective—do not offer advice or judge the content. Call `set_thinking("low", "Reflecting synthesis")` for this spoken reply.
