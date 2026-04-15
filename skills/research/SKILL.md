---
name: research
description: A structured framework for conducting web research, iteratively refining queries, and outputting machine-readable XML syntheses.
---
## Overview
This skill provides a structured, iterative framework for conducting high-quality web research. It guides agents to formulate strong queries, evaluate source quality based on strict criteria, refine their approach over multiple rounds, and finally deliver the results in a strict XML format designed for inter-agent readability.

## When to Use
Use this skill whenever a task requires finding accurate, high-quality, up-to-date, or comprehensive information from the web.

## Boundaries & Guardrails
- **Search Limit**: NEVER exceed **5 rounds of `web_search`** for a single research goal.
- **Snippet First**: Rely on the text snippets returned by `web_search` first. In many cases, this is enough.
- **Human Steering**: If uncertain about the quality bar or success criteria for a specific topic, pause. Show the human a few examples (snippets or fetched content) and ask for steering feedback before continuing.
- **Formatting**: The final output MUST strictly adhere to the requested XML structure.

## Workflow

### Phase 1: Goal & Query Formulation
- Clearly define the ultimate research goal based on the user's prompt.
- Translate that goal into targeted, high-yield search keywords. Avoid using full, conversational questions as keywords.

### Phase 2: Search & Evaluate (The Loop)
1. **Search**: Execute `web_search` using your targeted keywords.
2. **Review Snippets**: Read the returned snippets to gauge value.
3. **Evaluate**: Filter and judge the gathered information against four strict criteria:
   - *Trustworthiness*: Is the source credible and authoritative?
   - *Relevance*: Does it directly answer or contribute to the current goal?
   - *Freshness*: Is the data up-to-date?
   - *Value-Add*: Does it provide meaningful insights, or does it pollute/confuse the topic, leading in the wrong direction?

### Phase 3: Iteration & Refinement
- Discard any low-quality results that fail the evaluation criteria.
- Based on the high-quality findings (or lack thereof), update and refine your keywords, underlying questions, and sub-goals.
- Repeat Phase 2 with the refined approach.
- *Remember: Do not exceed 5 rounds of `web_search`.*

### Phase 4: Agent-Friendly Synthesis & Output
- **Machine-Readable Deliverable**: Synthesize the high-quality findings and output the final response using the strict XML structure below. This format is required to ensure perfect readability for routing agents.
- **Output Format Structure**:
  ```xml
  <research_response>
    <goal>The original research goal</goal>
    <synthesis>
      The comprehensive, synthesized findings derived from the high-quality search results.
    </synthesis>
    <sources>
      - List of the trusted URLs that contributed to the synthesis.
    </sources>
    <research_learnings>
      - Detail insights on *how* you found the best results for this specific topic (e.g., "Keywords like X yielded better results than Y").
    </research_learnings>
  </research_response>
  ```
- **Memory Trigger**: Always load and use the `memory` skill to record the insights from the `<research_learnings>` tag into the daily log, ensuring the system gets smarter at researching this topic in the future.

## Required Tools
- `web_search`: Required to query the web and retrieve initial search result snippets.