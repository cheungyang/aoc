"""The alias table: which spoken names lead to which focus.

A switch phrase and an alias are compared in one form (`alias_key`): lowercase,
no punctuation, possessives or apostrophes, and none of the filler words around
a name ("the", "room", "channel", ...). `build_aliases` maps each name a focus
answers to -- see `FocusDirectory.alias_sources` -- to its key, and drops a
name two targets claim.

Pure functions, used by the focus (how a focus is spoken about), the switcher
(what you asked for) and the briefings (what to say to get somewhere).
"""
import re
from typing import Dict, Iterable, Tuple

# Words around a name that don't change which target is meant.
_FILLER = re.compile(r"\b(?:the|room|channel|voice|chat|please|now|thanks|thank you)\b")


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


def build_aliases(sources: Iterable[Tuple[str, Iterable[str]]]) -> Dict[str, str]:
    """Alias -> focus key.

    `sources` pairs each focus key with the names that mean it (see
    `FocusDirectory.alias_sources`): a channel's own name, the agent whose home
    it is ("Daisy"), "concierge" for the lobby. An alias claimed by two targets
    is dropped rather than guessed.
    """
    aliases: Dict[str, str] = {}
    ambiguous = set()
    for key, names in sources:
        for name in names:
            alias = alias_key(name.replace("-", " ")) if isinstance(name, str) else ""
            if not alias:
                continue
            if alias in aliases and aliases[alias] != key:
                ambiguous.add(alias)
            aliases[alias] = key
    for alias in ambiguous:
        del aliases[alias]
    return aliases
