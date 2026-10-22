"""Every prompt the core sends to a model, and the code that builds it.

Each prompt's text is one template, `templates/<area>/<name>.md`, and one class
beside the others of its area builds it: the template's name, its inputs as
dataclass fields, and anything derived from them. `base.py` holds the template
engine and the two base classes:

    Prompt        renders a template:  KnowledgePrompt(date=..., ...).render()
    WorkerPrompt  a Prompt that is a whole task, run as is by graph-worker-low:
                  await SpeechWriterPrompt(name=..., channel=..., post=...).run(channel=...)

    system.py     the system prompt every agent turn starts with (templates/system/)
    voice.py      the <speech> contract, the speech writer, the near-miss resolver
    memory.py     the context pruner's summary, the nightly dream and compaction
    browser.py    the rules added to the browser agent's own system prompt
    scheduler.py  a scan's findings, appended to its scheduled run's prompt

A graph's own prompts stay in the graph, like an agent's tools: in
graphs/<id>/prompts/, each a Prompt whose `templates_dir` is that folder, so
they go when the graph goes. Agent and skill definitions (agents/<id>/*.md,
skills/<id>/SKILL.md) are prompts too, and stay with their agent or skill.
"""
from core.prompts.base import Prompt, WorkerPrompt

__all__ = ["Prompt", "WorkerPrompt"]
