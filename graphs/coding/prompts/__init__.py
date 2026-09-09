"""Prompt templates for Goldfish workers in coding graph."""
from .spec_validator_prompt import build_spec_validator_prompt
from .coder_prompt import build_coder_prompt
from .critic_prompt import build_critic_prompt

__all__ = [
    "build_spec_validator_prompt",
    "build_coder_prompt",
    "build_critic_prompt"
]
