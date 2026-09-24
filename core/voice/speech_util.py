"""Notation that always resolves the same way when text is spoken.

XML blocks, filing hashtags and priority symbols are handled here in code
rather than asked of the model. A prompt rule only applies on the turns that
reach the model, and these markers turn up just as often in the prose that does
not -- so every path (the verbalizer, the simple-structure renderer and the
stream's prose branch) runs text through these first.
"""
import re
from typing import List, Optional


def strip_completed_xml(text: str) -> str:
    """Strips completed matching XML tag pairs and self-closing tags."""
    if not text:
        return ""
    pattern = r"<([a-zA-Z][a-zA-Z0-9_:-]*)(?:\s+[^>]*)?>.*?</\1>"
    prev = None
    cleaned = text
    while prev != cleaned:
        prev = cleaned
        cleaned = re.sub(pattern, "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<[a-zA-Z][a-zA-Z0-9_:-]*(?:\s+[^>]*)?/>", "", cleaned)
    return cleaned


def find_unclosed_xml_start(text: str) -> Optional[int]:
    """Finds the starting index of the earliest in-flight or unclosed XML tag."""
    earliest = None
    tag_matches = list(re.finditer(r"<([a-zA-Z][a-zA-Z0-9_:-]*)(?:\s+[^>]*)?>", text))
    for tm in tag_matches:
        tag_name = tm.group(1)
        close_tag = f"</{tag_name}>"
        if not re.search(re.escape(close_tag), text[tm.end():], flags=re.IGNORECASE):
            if earliest is None or tm.start() < earliest:
                earliest = tm.start()
                break

    m_partial = re.search(r"<(?=[a-zA-Z/?!]|(?:\s*$))[^>]*$", text)
    if m_partial:
        if earliest is None or m_partial.start() < earliest:
            earliest = m_partial.start()

    return earliest


def strip_xml(text: str) -> str:
    """Strips all XML elements and their contents from text before synthesis.

    Agent responses can include internal XML blocks (such as `<memory>...</memory>`,
    `<vote>...</vote>`, `<poll>...</poll>`, etc.) meant for state persistence or
    interactive UI components. These should never be spoken or sent to the
    speech restructuring model.
    """
    if not text:
        return ""
    cleaned = strip_completed_xml(text)
    # Strip any remaining unclosed or dangling opening tags and subsequent content
    cleaned = re.sub(
        r"<[a-zA-Z][a-zA-Z0-9_:-]*(?:\s+[^>]*)?>.*$",
        "",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Strip stray closing or standalone tags
    cleaned = re.sub(r"</?[a-zA-Z][a-zA-Z0-9_:-]*(?:\s+[^>]*)?>", "", cleaned)
    # Strip partial opening tags at the end
    cleaned = re.sub(r"<(?=[a-zA-Z/?!]|(?:\s*$))[^>]*$", "", cleaned)
    return cleaned


# A tag is a hash followed immediately by a letter, so "# Heading" and the
# "#" of a markdown header are left alone. The lookbehind requires the hash
# to open a word, which keeps "C#", "issue #4", and a URL fragment intact.
_HASHTAG_RE = re.compile(r"(?<![^\s(\[])#[A-Za-z][\w-]*(?:/[\w-]+)*")

# Obsidian task priorities. The sanitizer classes these as emoji and deletes
# them, so unless they are turned into words here the level is lost without
# a trace -- the listener hears the task and never learns it was urgent.
_PRIORITY_WORDS = {
    "\U0001F53A": "highest priority",
    "\u23EB": "high priority",
    "\U0001F53C": "medium priority",
    "\U0001F53D": "low priority",
    "\u23EC": "lowest priority",
}

_PRIORITY_RE = re.compile("|".join(re.escape(s) for s in _PRIORITY_WORDS))

# Bullets and numbering that can sit between the line start and a symbol.
_LIST_PREFIX_RE = re.compile(r"^\s*(?:[-*+>]\s*|\d+[.)]\s*)*")


def strip_hashtags(text: str) -> str:
    """Removes filing tags such as `#a/read` from text bound for speech.

    Tags are notation for the eye. Read aloud, "#a/read" becomes "hash a
    slash read" in the middle of a sentence, and asking the model to drop
    them only works on the turns that reach the model -- plain prose takes
    the fast path. Doing it here covers every path, the way `strip_xml`
    does.

    Line structure is preserved: `has_structure` reads line beginnings, so
    collapsing newlines here would hide a list from the detector. A line
    left with no words after its tags are removed is dropped entirely,
    since a blank line is a block boundary downstream.
    """
    if not text:
        return ""

    kept: List[str] = []
    for line in text.split("\n"):
        stripped = _HASHTAG_RE.sub("", line)
        if stripped == line:
            kept.append(line)
            continue
        stripped = re.sub(r"[ \t]{2,}", " ", stripped)
        # A tag sitting just before punctuation ("and #home.") leaves the
        # space behind it, which the synthesiser pauses on.
        stripped = re.sub(r"[ \t]+([.,;:!?])", r"\1", stripped).rstrip()
        if not re.search(r"[A-Za-z0-9]", stripped):
            # The line was nothing but tags; a bare "-" is not worth saying.
            continue
        kept.append(stripped)

    return "\n".join(kept)


def speak_priority_symbols(text: str) -> str:
    """Replaces the priority symbols with the words for their level.

    Placement follows where the symbol sits. Leading its line it reads as a
    label ("high priority: call the dentist"); trailing a phrase it reads as
    an aside ("call the dentist, high priority"). The comma is what stops
    the synthesiser running the level into the task as one breath.
    """
    if not text:
        return ""

    def replace(match: re.Match) -> str:
        words = _PRIORITY_WORDS[match.group(0)]
        before = text[:match.start()].rsplit("\n", 1)[-1]
        if not _LIST_PREFIX_RE.sub("", before).strip():
            return f"{words}:"
        if before.rstrip()[-1] in ".,;:!?-\u2014":
            return words
        return f", {words}"

    spoken = _PRIORITY_RE.sub(replace, text)
    # The symbol was usually preceded by a space; an inserted comma must not
    # be left floating after it.
    return re.sub(r"[ \t]+,", ",", spoken)
