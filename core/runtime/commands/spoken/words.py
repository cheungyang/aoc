"""How spoken words are compared: the forms an utterance is reduced to before a
spoken command matches its phrases, and before the voice room's alias table
(core/voice/room/aliases.py) looks a name up."""
import re
from typing import List

_WORD = re.compile(r"[a-z0-9']+")
# Words around a name that don't change which target is meant.
_FILLER = re.compile(r"\b(?:the|room|channel|voice|chat|please|now|thanks|thank you)\b")


def words(text: str) -> List[str]:
    """Lower-case words, with hyphens split ("mm-hmm" -> ["mm", "hmm"])."""
    return _WORD.findall((text or "").lower().replace("-", " "))


def clean(text: str) -> str:
    """Lowercase, punctuation removed except apostrophes, single spaces."""
    text = (text or "").lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9'\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize(text: str) -> str:
    """The comparable form of a phrase or alias: possessives and apostrophes dropped too."""
    text = re.sub(r"'s\b", "", clean(text)).replace("'", "")
    return re.sub(r"\s+", " ", text).strip()


def alias_key(text: str) -> str:
    """A phrase or alias with the filler words gone: what the two are compared as."""
    return re.sub(r"\s+", " ", _FILLER.sub(" ", normalize(text))).strip()
