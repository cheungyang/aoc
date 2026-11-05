"""What the model wrapper adds to a request (core/runtime/model/retry.py)."""
from core.prompts.base import Prompt


class ResumeAskPrompt(Prompt):
    """The user turn a resume puts after the text so far, for a model that
    refuses a request ending on its own turn (`resume_request`): go on from
    exactly where the reply was cut off."""

    template = "model/resume_ask"
