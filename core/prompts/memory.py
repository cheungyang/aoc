"""Memory prompts: the context pruner's summary of earlier turns, and the
nightly dream and compaction (scripts/dream_standup.py).

The dream's reply format, in templates/memory/dream.md and compaction.md, is
the contract `core.knowledge.memory.dream_ops.parse_reply` enforces.
"""
import re
import sys
from dataclasses import dataclass

from core.prompts.base import Prompt, WorkerPrompt


@dataclass
class ContextSummaryPrompt(WorkerPrompt):
    """Earlier conversation turns, distilled into a dense, machine-readable
    summary that replaces them (core/runtime/context_pruner.py). The answer
    is the summary text; "" when the reply had none, and the pruner falls back
    to its heuristic summary. Its timeout is configured: the pruner passes it."""

    template = "memory/context_summary"
    caller = "context-pruner"

    transcript: str
    previous_summary: str = ""
    max_summary_tokens: int = 1000

    def values(self):
        return {
            "transcript": self.transcript.strip(),
            "previous_summary": (self.previous_summary or "").strip(),
            "max_summary_tokens": self.max_summary_tokens,
        }

    def parse(self, result) -> str:
        return parse_summary(result.text)


def parse_summary(reply) -> str:
    """The summary in the worker's reply: its <summary>, else its <payload>,
    else the reply itself; "" for an error or an empty reply, logged."""
    if not reply:
        return ""
    raw_str = str(reply)
    err_m = re.search(r"<errors?>(.*?)</errors?>", raw_str, re.DOTALL | re.IGNORECASE)
    if err_m and err_m.group(1).strip() and err_m.group(1).strip().lower() != "none":
        err_text = err_m.group(1).strip()
        print(f"[ContextPruner] Warning: graph-worker-low returned error: '{err_text}'. Falling back to heuristic summary.", file=sys.stderr)
        return ""

    m = re.search(r"<summary>(.*?)</summary>", raw_str, re.DOTALL)
    if m and m.group(1).strip():
        summary_text = m.group(1).strip()
        print(f"[ContextPruner] Successfully generated graph-worker-low summary (~{len(summary_text)} chars).")
        return summary_text
    payload_m = re.search(r"<payload>(.*?)</payload>", raw_str, re.DOTALL)
    if payload_m and payload_m.group(1).strip():
        payload_text = payload_m.group(1).strip()
        print(f"[ContextPruner] Extracted graph-worker-low payload summary (~{len(payload_text)} chars).")
        return payload_text
    if raw_str.strip():
        print(f"[ContextPruner] Extracted raw graph-worker-low summary (~{len(raw_str.strip())} chars).")
        return raw_str.strip()

    print("[ContextPruner] Warning: graph-worker-low returned empty or unrecognized response. Falling back to heuristic summary.", file=sys.stderr)
    return ""


@dataclass
class DreamPrompt(Prompt):
    """One agent's dream, with everything it needs inlined: the rules from the
    dream skill's Core section, its logs and every memory entry it may act on.
    The agent dreams on its own model, not a worker.

    The reply format is stated here as well as in the skill: this prompt is the
    contract the parser enforces, and weaker models follow what is in front of
    them more reliably than a loaded skill.
    """

    template = "memory/dream"
    rules: str        # the Core section of skills/dream/SKILL.md
    dream_input: str  # DreamInput.xml


@dataclass
class CompactionPrompt(Prompt):
    """Shared memory topics that need tidying, for graph-worker to compact."""

    template = "memory/compaction"
    compaction_input: str  # DreamInput.xml, from build_compaction_input
