"""Vocabulary echoes: the recognizer reciting its word list instead of what was said.

Live transcription is given a `customVocabulary` (stt.py `agent_vocabulary`):
the names of the agents you can reach, and their ids read aloud, so that a
generic recognizer gets "Frankie" and "Aki" right. Given a short noise, a
cough, or a voice that isn't yours, it sometimes returns that list instead of
a transcript. On 2026-10-28 it returned this for 1.8 s of audio that the
voiceprint gate scored 0.06:

    Here is the list of added phrases:

    - Aki, agent designer
    - Butler, home steward
    ...
    - William, wiki gardener

The hand-off rule read the last line's ", wiki gardener" as you calling
William, and the voice room moved to his channel mid-conversation. Shorter
recitals came the same day: "Aki, agent designer." (which moved the room to
Aki), and "Receipt Processor." from 180 ms of sound.

`vocabulary_echo` says whether a transcript is such a recital. Any one of
four tells is enough:

  * its preamble: "Here is the list of added phrases", the recognizer's own
    words introducing the list, however little of the list follows.
  * a roster: three or more different vocabulary terms, making up most of the
    words. "Start with Marvin, then Grace, then William." names three, but
    most of its words are yours.
  * a pair: nothing but vocabulary terms, two or more different ones. An
    agent's name and its id, "Aki, agent designer", is how the list reads,
    not how anyone talks.
  * too fast to have been said: more letters than speech holds in the voiced
    audio. Your fastest in the voiceprint gate's log was about 30 a second
    (over the VAD's voiced span, which trims the edges); the recitals came at
    49 to 234.

One name at a speed a person could say it passes: "Grace." is a fine answer
to "who's next?", and can't be told from a recital of it. Pure functions, so
the rules can be pinned without audio.
"""
import re
from functools import lru_cache
from typing import Iterable, Optional, Pattern, Tuple

# More letters a second of voiced speech than anyone says (see the docstring).
# Provisional, from one week of the gate's log: too low drops a quick real
# phrase, which you then repeat; too high lets a short recital through.
MAX_LETTERS_PER_S = 40
# A roster names at least this many different terms...
ROSTER_MIN_TERMS = 3
# ...and they make up at least this share of its words.
ROSTER_MIN_SHARE = 0.6
# A pair: at least this many different terms, and nothing else.
PAIR_MIN_TERMS = 2

_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)*")
# The recognizer's own words before the list, as it recited them.
_PREAMBLE = re.compile(r"\blist of added phrases\b", re.I)


def vocabulary_echo(text: str, vocabulary: Iterable[str], speech_ms: float = 0) -> Optional[str]:
    """Why `text` is the recognizer reciting `vocabulary` rather than what was
    said -- "a roster of 16 vocabulary terms" -- or None if it reads as speech.

    `speech_ms` is the voiced audio it came from (`Utterance.speech_ms()`);
    without it (0), the speed isn't checked.
    """
    text = (text or "").strip()
    if not text:
        return None
    if _PREAMBLE.search(text):
        return "the recognizer's preamble to its list"
    terms = _pattern(_terms(vocabulary))
    if terms is not None:
        found = [_key(match.group(0)) for match in terms.finditer(text)]
        distinct = set(found)
        named = sum(len(term.split()) for term in found)
        if len(distinct) >= ROSTER_MIN_TERMS and named >= ROSTER_MIN_SHARE * len(_WORD.findall(text)):
            return f"a roster of {len(distinct)} vocabulary terms"
        if len(distinct) >= PAIR_MIN_TERMS and not _WORD.search(terms.sub(" ", text)):
            return f"nothing but vocabulary terms: {', '.join(sorted(distinct))}"
    letters = sum(char.isalnum() for char in text)
    if speech_ms > 0 and letters * 1000 / speech_ms > MAX_LETTERS_PER_S:
        return f"{letters} letters from {speech_ms:.0f} ms of speech"
    return None


def _key(term: str) -> str:
    """'Receipt-Processor' -> 'receipt processor': one form per term."""
    return " ".join(word for word in re.split(r"[\s_-]+", term.lower()) if word)


def _terms(vocabulary: Iterable[str]) -> Tuple[str, ...]:
    """The vocabulary in `_key` form, each term once, longest first: "graph
    worker low" is found before "graph worker"."""
    keys = {_key(term) for term in vocabulary if isinstance(term, str)}
    return tuple(sorted((key for key in keys if key), key=lambda key: (-len(key), key)))


@lru_cache(maxsize=8)
def _pattern(terms: Tuple[str, ...]) -> Optional[Pattern]:
    """Any term as whole words, in any case; spaces, hyphens and underscores
    between its words are interchangeable. None for no terms."""
    if not terms:
        return None
    alternation = "|".join(r"[\s_-]+".join(re.escape(word) for word in term.split()) for term in terms)
    return re.compile(rf"(?<![\w-])(?:{alternation})(?![\w-])", re.I)
