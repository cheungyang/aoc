"""The composition root: plugs the runtime into the packages below it.

The packages the runtime builds on never import it (rule 3 in
core/runtime/__init__.py). Where one of them has to call up, it declares a
port, and `wire()` fills it:

  core.loaders.agents_loader.set_agent_factory   builds an agent from its config
  core.prompts.base.set_worker                   runs a WorkerPrompt
  core.runtime.hooks.set_turn_hooks              a turn's side work: pruning, the job,
                                                 the memory log

main.py and every script that runs agents call `wire()` before anything else;
the tests call it from tests/conftest.py. An unfilled port fails loudly, naming
this function.
"""
from core.loaders import agents_loader
from core.prompts import base as prompts
from core.runtime import hooks
from core.runtime.delegation import ask_worker
from core.runtime.agent_factory import build_agent


def wire() -> None:
    """Fills every port. Safe to call more than once."""
    agents_loader.set_agent_factory(build_agent)
    prompts.set_worker(ask_worker)
    # In this order: a turn waits for the previous prune before its job is
    # registered, and its job is settled before its memory log is written.
    hooks.set_turn_hooks([hooks.PruneHook(), hooks.JobHook(), hooks.MemoryLogHook()])
