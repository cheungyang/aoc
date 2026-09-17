"""The instruction given to the model that rewrites output for speech.

Kept apart from the pipeline that calls it because this is the part that
gets edited: tuning how a table sounds read aloud is a prose change, and
it should not mean opening a file full of buffering and chunk boundaries.

The whole contract is **no compression**. Speech is slower than reading,
which tempts summarisation, but a spoken answer that quietly drops the
third recommendation is worse than a long one -- the listener cannot tell
that it happened. Every rule below exists to restructure without losing
anything.

The rules are assembled from lists rather than written as one blob so that
adding, removing, or reordering one is a single-line edit, and so no line
here needs horizontal scrolling to read.
"""
from typing import List

# Stated before the rules so they have a subject to attach to.
_ROLE = (
    "You convert written text into text that will be read aloud by a "
    "speech synthesiser."
)

# The contract, first and in the strongest terms available. This is the
# rule a model asked to "rewrite for speech" breaks by default, and the
# naming of specific phrases matters: a general instruction not to
# summarise is easy to satisfy while still eliding a row.
_PRESERVATION: List[str] = [
    "You are NOT summarising.",
    "Preserve every fact, number, name, date, and list item in the input.",
    "If the input has ten items, your output mentions all ten.",
    'Never write "and others", "several more", or any phrase that stands',
    "in for content you left out.",
]

# How to restructure. One idea per bullet, because the model follows a
# short imperative more reliably than a compound sentence.
_REWRITING: List[str] = [
    (
        "Turn bullet points and numbered lists into flowing sentences, "
        "keeping their order."
    ),
    "Turn each table row into its own sentence naming its values.",
    "Turn headings into natural lead-ins rather than announcements.",
    "Expand symbols and abbreviations into spoken words.",
    (
        'Say "$1,234.56" as "one thousand two hundred thirty four '
        'dollars and fifty six cents".'
    ),
    'Say "~" as "about", and "e.g." as "for example".',
    "Replace markdown emphasis and emoji with plain spoken phrasing.",
    "For a link, say its label, never its URL.",
    (
        'Add short connecting phrases ("first", "after that", "the catch '
        'is") so it sounds like a person talking.'
    ),
    (
        "Describe code blocks in one clause instead of reading them; say "
        "the code is in the chat."
    ),
]

# Length is called out on its own because it is counter-intuitive: a model
# told to rewrite will shorten unless told that growing is correct.
_LENGTH = (
    "Your output will usually be LONGER than the input. That is correct."
)

# The result goes straight to a synthesiser, which reads a stray asterisk
# or a "Sure, here you go" out loud.
_OUTPUT: List[str] = [
    "Output only the spoken text.",
    "No preamble, no markdown, no quotation marks around the whole reply.",
]


def build_verbalizer_prompt() -> str:
    """Builds the system prompt for the speech verbalization pass.

    Takes no arguments: the instruction is the same for every turn, and the
    text being rewritten is passed as the user message rather than
    interpolated here, so nothing callers supply can dilute the
    no-compression contract.
    """
    return (
        f"{_ROLE}\n\n"
        f"{' '.join(_PRESERVATION)}\n\n"
        "Rewrite for the ear:\n"
        + "\n".join(f"- {rule}" for rule in _REWRITING)
        + f"\n\n{_LENGTH}\n\n"
        f"{' '.join(_OUTPUT)}"
    )
