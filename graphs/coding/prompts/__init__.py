"""The coding graph's prompts, one module per Goldfish worker.

Each is a `core.prompts.Prompt` whose template is the `.md` file beside it, so
the graph's prompts stay with the graph. The coder and critic run on
graph-worker under this graph's grants (utils/worker.py); the spec validator on
graph-worker, from tools/spec_validator.py. Each prompt's output block is the
shape its parser in utils/xml_parsers.py accepts.
"""
from .spec_validator_prompt import SpecValidatorPrompt
from .coder_prompt import CoderPrompt
from .critic_prompt import CriticPrompt

__all__ = [
    "SpecValidatorPrompt",
    "CoderPrompt",
    "CriticPrompt",
]
