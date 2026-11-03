"""The alias table: which spoken names lead to which focus.

A switch phrase and an alias are compared in one form, `alias_key`: lowercase,
no punctuation, possessives or apostrophes, and none of the filler words around
a name ("the", "room", "channel", ...). It is how spoken words are compared
everywhere (core/runtime/commands/spoken/words.py). `build_aliases` maps each
name a focus answers to -- see `FocusDirectory.alias_sources` -- to its key,
and drops a name two targets claim.

Pure functions, used by the focus (how a focus is spoken about), the switcher
(what you asked for) and the briefings (what to say to get somewhere).
"""
from typing import Dict, Iterable, Tuple

from core.runtime.commands.spoken.words import alias_key


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
