"""Who a message is addressed to, read from its words (hand-offs).

"Ted, create a syllabus on THIS topic", typed in #pkm-wiki, is for Ted, not
for William, who answers there. graphs/main hands such a message to Ted in
his own channel, with a capsule of the conversation it came from
(core/runtime/history/handoff.py). This module says whether a message calls
an agent by name, and which one. It never calls a model: routing stays a pure
function of the message and the configuration (routing.py).

The vocative forms, in the order they are tried:

  Ted, create ... / hey Ted: ...   the name first, set off by punctuation
  Ted create a syllabus ...        the name first, then the request
  ask Ted to ... / tell Ted ...    the name after a verb that hands over
  ..., Ted?                        the name last, after a comma
  ... @Ted ...                     an @-mention, anywhere

Naming an agent is not addressing it: "William said it leans on caching" is
about William. When the name comes first with no punctuation after it, the
next word decides. These words make the agent the subject, not the listener
(the reporting-verb stoplist):

  - a reporting verb, or another verb with the agent as its subject: "said",
    "thinks", "wants", or any regular past tense ("posted");
  - a copula, auxiliary or modal ("is", "has", "will", "can"), unless a
    question follows: "Miles can you ..." and "ramsay did you ..." ask him;
  - a possessive ("Ted's"), or a joining word or preposition ("Ted and
    William", "Elephant in the room").

An adverb in between is skipped: "Ted just said" is about Ted; "Ted just
start" is to him. The rule can't tell a name from the same common word
opening a sentence ("Grace period ends Friday"); the pointer the hand-off
leaves shows where the message went.

Names match whole words, in any case, with an optional "@": an agent's
`name` ("Ted", "Receipt Processor") or its id, hyphenated or spaced
("topic-researcher"). An all-caps name without "@" is an acronym ("TED talk").
Mentions of two different agents address neither.

A message naming three or more different agents is about them -- a roster, a
list of who did what -- and addresses none of them, whatever its form. The
voice room learned this from speech-to-text reciting its word list
("- Aki, agent designer ... - William, wiki gardener"): the last line put a
name after a comma, and the room moved to William's channel mid-conversation.
In the channels' history, 2 of 574 messages named three agents, and neither
spoke to one of them.

What the Discord runner and the agent wrap around your words is left out: the
message you replied to, a new thread's starter, a delegation's <caller> tag,
and the local time every message is stamped with ("[Wed 18:44] ",
core/prompts/system.py). In front of your words, the stamp hid every
name-first form.
"""
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Mapping, NamedTuple, Optional, Pattern, Set, Tuple, Union

from core.prompts.system import TIME_STAMP

Prompt = Union[str, list]

# `Address.form`: which vocative form named the agent.
LEADING = "leading"        # "Ted, ..." / "Ted create ..."
DIRECTIVE = "directive"    # "ask Ted to ..."
TRAILING = "trailing"      # "..., Ted?"
MENTION = "mention"        # "... @Ted ..."

# Words that may open a message before the name: "hey Ted", "ok so Ted".
OPENERS = frozenset({
    "hey", "hi", "hello", "yo", "ok", "okay", "oh", "so", "and", "also", "but", "now", "then",
    "well", "please", "pls", "thanks", "thx", "dear", "right", "alright",
})
# Verbs that hand a request to the agent they name: "ask Ted to ...", "have Ted ...".
HANDOVER_VERBS = frozenset({"ask", "tell", "have", "get", "let"})

# --- the reporting-verb stoplist: after a leading name, these make it the subject
ADVERBS = frozenset({
    "just", "also", "already", "always", "never", "still", "really", "actually", "probably",
    "usually", "often", "sometimes", "again", "once", "only", "even", "definitely", "apparently",
    "clearly", "basically", "literally", "totally", "simply", "kindly", "quickly", "now",
})
AUXILIARIES = frozenset({
    "is", "was", "are", "were", "am", "been", "being", "has", "had", "does", "did",
    "will", "would", "can", "could", "should", "shall", "may", "might", "must",
    "isn't", "wasn't", "aren't", "weren't", "hasn't", "hadn't", "doesn't", "didn't",
    "won't", "wouldn't", "can't", "cannot", "couldn't", "shouldn't", "mustn't",
})
# After an auxiliary, these make the message a question to the agent: "can you", "is this".
QUESTION_SUBJECTS = frozenset({
    "you", "u", "ya", "we", "i", "it", "this", "that", "there", "these", "those", "they",
    "anyone", "anything", "everything", "someone", "something",
})
REPORTING_VERBS = frozenset({
    # what the agent said, wrote or thinks
    "said", "says", "told", "tells", "asked", "asks", "thought", "thinks", "wrote", "writes",
    "mentioned", "mentions", "suggested", "suggests", "recommended", "recommends", "replied",
    "replies", "reported", "reports", "noted", "notes", "claimed", "claims", "agreed", "agrees",
    "believes", "knows", "knew", "meant", "means", "wants", "needs", "likes", "loves", "hates",
    "prefers", "seems", "sounds", "looks", "feels", "felt", "found", "finds", "heard", "hears",
    # irregular past tenses, and third-person forms of common verbs
    "made", "makes", "gave", "gives", "got", "gets", "sent", "sends", "went", "goes", "came",
    "comes", "took", "takes", "saw", "sees", "ran", "runs", "built", "builds", "left", "leaves",
    "kept", "keeps", "brought", "brings", "bought", "buys", "began", "begins", "won", "wins",
    "lost", "loses", "paid", "pays", "sold", "sells", "spent", "spends", "taught", "teaches",
    "understood", "understands", "held", "holds", "led", "met", "meets", "spoke", "speaks",
    "stood", "forgot", "forgets", "chose", "chooses", "tries", "uses", "works", "handles",
    "tracks", "covers", "owns", "posts", "shares",
})
JOINERS = frozenset({
    "and", "or", "nor", "&", "+", "vs", "versus", "of", "to", "than", "as",
    "in", "at", "from", "by", "with", "without", "into", "via", "per",
})
# Words ending in -ed that are no past tense: "Ted proceed", with the comma left out.
NOT_PAST = frozenset({
    "need", "feed", "seed", "speed", "embed", "proceed", "succeed", "exceed", "shred", "heed",
    "bed", "red", "shed", "wed", "indeed", "hundred",
})
# How many adverbs may stand between the name and the word that decides.
_MAX_ADVERBS = 2
# A message naming this many different agents is a roster: it addresses none.
ROSTER_MIN_AGENTS = 3

# --- what the Discord runner, delegation and the agent wrap around the user's words
_REPLYING_TO = re.compile(r"<replying_to\b[^>]*>.*?</replying_to>", re.S | re.I)
_CALLER = re.compile(r"<caller>.*?</caller>", re.S | re.I)
_THREAD_STARTER = re.compile(r"^\s*\[Thread starter message from .*?\"\]\s*", re.S)
# The local time core/runtime/agent.py puts in front of every user message;
# more than one when a prompt passed on already carried its own.
_TIME_STAMP = re.compile(rf"^\s*(?:{TIME_STAMP.pattern}\s*)+")

_SEPARATOR = re.compile(r"\s*(?:[,:;!?.…–—]|-(?=\s|$))")
_POSSESSIVE = re.compile(r"'s?(?!\w)", re.I)
_WORD = re.compile(r"[\w']+|[&+]")


@dataclass(frozen=True)
class Address:
    """The agent a message calls by name, and how."""
    agent_id: str
    form: str    # LEADING, DIRECTIVE, TRAILING or MENTION
    name: str    # the words that named it, as written


def message_text(prompt: Prompt) -> str:
    """The user's own words in a prompt: its text parts, without what the
    Discord runner, delegation and the agent's time stamp add around them."""
    if isinstance(prompt, str):
        text = prompt
    elif isinstance(prompt, list):
        parts = []
        for part in prompt:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text") or "")
            elif isinstance(part, str):
                parts.append(part)
        text = "".join(parts)
    else:
        return ""
    text = _REPLYING_TO.sub("", text)
    text = _CALLER.sub("", text)
    # The stamp comes first (the agent stamps the finished prompt), or right
    # after a delegation's <caller> tag; a thread's starter comes after it.
    text = _TIME_STAMP.sub("", text)
    text = _THREAD_STARTER.sub("", text)
    return text.replace("\u2019", "'").strip()


def aliases_for(agents: Mapping[str, Mapping]) -> Dict[str, str]:
    """Alias -> agent id, for the agents a message may address (id -> config).

    Each agent answers to its `name` and its id, with hyphens read as spaces.
    An alias two agents share is dropped rather than guessed.
    """
    aliases: Dict[str, str] = {}
    ambiguous = set()
    for agent_id, config in agents.items():
        name = config.get("name") if isinstance(config, Mapping) else None
        for alias in (name, agent_id):
            key = _alias_key(alias) if isinstance(alias, str) else ""
            if not key:
                continue
            if key in aliases and aliases[key] != agent_id:
                ambiguous.add(key)
            aliases[key] = agent_id
    for key in ambiguous:
        del aliases[key]
    return aliases


def addressed_agent(prompt: Prompt, aliases: Mapping[str, str]) -> Optional[Address]:
    """The agent `prompt` addresses by name, if any (see the module docstring).

    `aliases` maps lowercase names to agent ids (`aliases_for`); only those
    agents can be addressed, and only those count toward a roster.
    """
    text = message_text(prompt)
    if not text or not aliases:
        return None
    names = _patterns(tuple(sorted(aliases, key=len, reverse=True)))
    if len(_named(text, names, aliases)) >= ROSTER_MIN_AGENTS:
        return None  # a roster: about the agents it names, spoken to none of them
    for find in (_leading, _trailing, _mention):
        found = find(text, names, aliases)
        if found is not None:
            return found
    return None


# --- the forms ------------------------------------------------------------------

def _leading(text: str, names: "_Names", aliases: Mapping[str, str]) -> Optional[Address]:
    """The name first, after any openers ("hey", "ok so"), or after a handover
    verb ("ask Ted to ...")."""
    rest = text
    for _ in range(3):
        opener = re.match(r"([A-Za-z]+)\b[\s,!.]*", rest)
        if not opener or opener.group(1).lower() not in OPENERS:
            break
        rest = rest[opener.end():]

    form = LEADING
    verb = re.match(r"([A-Za-z]+)\s+", rest)
    if verb and verb.group(1).lower() in HANDOVER_VERBS:
        form, rest = DIRECTIVE, rest[verb.end():]

    match = names.leading.match(rest)
    if not match:
        return None
    written, mentioned = match.group("name"), bool(match.group("at"))
    if _is_acronym(written) and not mentioned:
        return None
    after = rest[match.end():]
    agent_id = aliases[_alias_key(written)]

    if _POSSESSIVE.match(after) and not mentioned:
        return None  # "Ted's syllabus", "ask Ted's opinion"
    if mentioned or form == DIRECTIVE or _SEPARATOR.match(after) or not _WORD.search(after):
        return Address(agent_id, form, written)
    words = [w.lower() for w in _WORD.findall(after)[:2 + _MAX_ADVERBS]]
    return None if _names_the_subject(words) else Address(agent_id, form, written)


def _trailing(text: str, names: "_Names", aliases: Mapping[str, str]) -> Optional[Address]:
    """The name last, after a comma: "what do you make of this, Ted?"."""
    match = names.trailing.search(text)
    if not match or (_is_acronym(match.group("name")) and not match.group("at")):
        return None
    return Address(aliases[_alias_key(match.group("name"))], TRAILING, match.group("name"))


def _mention(text: str, names: "_Names", aliases: Mapping[str, str]) -> Optional[Address]:
    """An @-mention anywhere, if it names exactly one agent."""
    found: Dict[str, str] = {}
    for match in names.mention.finditer(text):
        found.setdefault(aliases[_alias_key(match.group("name"))], match.group("name"))
    if len(found) != 1:
        return None
    agent_id, written = next(iter(found.items()))
    return Address(agent_id, MENTION, written)


def _named(text: str, names: "_Names", aliases: Mapping[str, str]) -> Set[str]:
    """Every agent `text` names, anywhere, by any of its names: what makes a
    roster. An all-caps name without "@" is an acronym here too."""
    return {aliases[_alias_key(match.group("name"))] for match in names.anywhere.finditer(text)
            if match.group("at") or not _is_acronym(match.group("name"))}


def _names_the_subject(words: List[str]) -> bool:
    """Whether the words after a leading name make the agent the subject of the
    sentence rather than the one spoken to (the stoplist in the docstring)."""
    i = 0
    while i < len(words) - 1 and i < _MAX_ADVERBS and words[i] in ADVERBS:
        i += 1
    word = words[i]
    following = words[i + 1] if i + 1 < len(words) else ""
    if word in AUXILIARIES:
        return following not in QUESTION_SUBJECTS
    if word in JOINERS or word in REPORTING_VERBS:
        return True
    return len(word) >= 4 and word.endswith("ed") and word not in NOT_PAST


# --- names ------------------------------------------------------------------------

class _Names(NamedTuple):
    """An alias set, as the patterns the forms and the roster look for."""
    leading: Pattern    # at the start: "@"? then the name
    trailing: Pattern   # at the end, after a comma
    mention: Pattern    # "@" then the name, anywhere
    anywhere: Pattern   # "@"? then the name, anywhere: what a roster counts


def _alias_key(alias: str) -> str:
    """'Topic-Researcher' -> 'topic researcher'."""
    return " ".join(w for w in re.split(r"[\s_-]+", alias.lower()) if w)


@lru_cache(maxsize=32)
def _patterns(aliases: Tuple[str, ...]) -> _Names:
    """Any alias as whole words, longest first; hyphens, underscores and spaces
    between an alias's words are interchangeable."""
    alternation = "|".join(r"[\s_-]+".join(re.escape(w) for w in alias.split()) for alias in aliases)
    name = rf"(?P<name>(?:{alternation})(?![\w-]))"
    return _Names(
        leading=re.compile(rf"(?P<at>@)?{name}", re.I),
        trailing=re.compile(rf",\s*(?P<at>@)?{name}\W*\Z", re.I),
        mention=re.compile(rf"(?<![\w@])@{name}", re.I),
        anywhere=re.compile(rf"(?<![\w@-])(?P<at>@)?{name}", re.I),
    )


def _is_acronym(written: str) -> bool:
    letters = [c for c in written if c.isalpha()]
    return len(letters) > 1 and all(c.isupper() for c in letters)

