---
name: research
description: A structured framework for conducting high-quality web research, evaluating search snippets, selectively fetching full pages, and iteratively refining queries.
---
## Overview
This skill provides a structured, iterative framework for conducting high-quality web research. It guides agents to formulate strong queries, evaluate source quality based on strict criteria, and refine their approach over multiple rounds to deliver the best possible results.

## When to Use
Use this skill whenever a task requires finding accurate, high-quality, up-to-date, or comprehensive information from the web.

## Boundaries & Guardrails
- **Search Limit**: NEVER exceed **5 rounds of `web_search`** for a single research goal.
- **Snippet First**: Rely on the text snippets returned by `web_search` first. In many cases, this is enough.
- **Selective Fetching**: Use `web_fetch` ONLY to read the full page when the snippet proves the page is highly valuable but requires deeper reading. Do not blindly fetch every search result.
- **Human Steering**: If uncertain about the quality bar or success criteria for a specific topic, pause. Show the human a few examples (snippets or fetched content) and ask for steering feedback before continuing.

## Workflow

### Phase 1: Goal & Query Formulation
- Clearly define the ultimate research goal based on the user's prompt.
- Translate that goal into targeted, high-yield search keywords. Avoid using full, conversational questions as keywords.

### Phase 2: Search & Evaluate (The Loop)
1. **Search**: Execute `web_search` using your targeted keywords.
2. **Review Snippets**: Read the returned snippets to gauge value.
3. **Deep Read (Optional)**: If a snippet indicates high value but lacks detail, use `web_fetch` on that specific URL.
4. **Evaluate**: Filter and judge the gathered information against four strict criteria:
   - *Trustworthiness*: Is the source credible and authoritative?
   - *Relevance*: Does it directly answer or contribute to the current goal?
   - *Freshness*: Is the data up-to-date?
   - *Value-Add*: Does it provide meaningful insights, or does it pollute/confuse the topic, leading in the wrong direction?

### Phase 3: Iteration & Refinement
- Discard any low-quality results that fail the evaluation criteria.
- Based on the high-quality findings (or lack thereof), update and refine your keywords, underlying questions, and sub-goals.
- Repeat Phase 2 with the refined approach.
- *Remember: Do not exceed 5 rounds of `web_search`.*

### Phase 4: Synthesis & Meta-Learning
- **Deliverable**: Synthesize the high-quality findings and produce the final results formatted exactly as requested by the initial user prompt.
- **Learnings Extraction**: Conclude your output with a distinct `### Research Learnings` section. Detail insights on *how* you found the best results for this specific topic (e.g., "Keywords like X yielded better results than Y," or "Criteria Z proved to be the best indicator of trustworthiness").
- **Memory Trigger**: Always load and use the `memory` skill to record these Research Learnings, ensuring the system gets smarter at researching this topic in the future.

## Required Tools
- `web_search`: Required to query the web and retrieve initial search result snippets.
- `web_fetch`: Required to read the full contents of a webpage when a snippet indicates high value.
