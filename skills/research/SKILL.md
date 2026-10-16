---
name: research
description: A structured framework for conducting web research, iteratively refining queries, and reporting sourced syntheses.
---
## Core

Use this whenever a task requires accurate, high-quality, up-to-date, or comprehensive information from the web.

### Guardrails
- **Search Limit**: NEVER exceed **5 rounds of `web_search`** for a single research goal.
- **Snippet First**: Rely on the text snippets returned by `web_search` first. In many cases, this is enough.
- **Fast-Fail Circuit Breaker**: If the first 2 rounds of `web_search` return zero high-quality or relevant results, **DO NOT** arbitrarily execute 3 more rounds. Fast-fail: report the results you have (or note the lack thereof) and explicitly declare the query a dead-end, to save tokens and prevent hallucination loops.
- **Human Steering**: If uncertain about the quality bar or success criteria for a specific topic, pause. Show the human a few examples (snippets or fetched content) and ask for steering feedback before continuing.
- **Formatting**: Reply in plain Markdown, never XML: your reply is shown in Discord, even when the Concierge relays the request.

### Workflow
1. **Goal & queries**: Define the research goal from the prompt, then translate it into strict, targeted boolean search queries, not conversational questions. Use operators like `"exact phrase"`, `OR`, `-exclusions`, or `site:specific-domain.com` to force high-yield initial results and minimize iteration loops.
2. **Search & evaluate**: Execute `web_search`, read the snippets to gauge value, and judge the results against four strict criteria:
   - *Trustworthiness*: Is the source credible and authoritative?
   - *Relevance*: Does it directly answer or contribute to the current goal?
   - *Freshness*: Is the data up-to-date?
   - *Value-Add*: Does it provide meaningful insights, or does it pollute/confuse the topic, leading in the wrong direction?
3. **Iterate**: Discard results that fail the criteria. Based on the high-quality findings (or lack thereof), drastically pivot your boolean strategy (e.g., a failed broad search becomes a highly specific `"quoted"` search, or vice versa) and repeat step 2, respecting the 5-round limit and the 2-round Fast-Fail Circuit Breaker.
4. **Report** the synthesis of the high-quality findings, then the trusted URLs it draws on. Then append a `<system_memory_log>` block recording *how* you found the best results (e.g., "Keywords like X yielded better results than Y"), so the system gets smarter at researching this topic in the future.

Call `load_skill("research")` only for this skill's background and tool list; the Core above is enough to run it.

## Reference

### Overview
This skill provides a structured, iterative framework for conducting high-quality web research. It guides agents to formulate strong queries, evaluate source quality based on strict criteria, refine their approach over multiple rounds, and finally report a sourced synthesis in Markdown.

### Required Tools
- `web_search`: Required to query the web and retrieve initial search result snippets.