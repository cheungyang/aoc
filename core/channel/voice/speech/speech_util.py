"""Notation that always resolves the same way when text is spoken.

XML blocks, filing hashtags and priority symbols are handled here in code
rather than asked of the model: a prompt rule is followed most of the time, and
a stray "#a/read" read aloud is "hash a slash read". Everything bound for the
synthesizer -- the agent's <speech> and the missing-speech fallback -- goes
through `speakable`.
"""
import re
from typing import List


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


def strip_xml(text: str) -> str:
    """Strips all XML elements and their contents from text before synthesis.

    Agent responses can include internal XML blocks (such as `<memory>...</memory>`,
    `<vote>...</vote>`, `<poll>...</poll>`, etc.) meant for state persistence or
    interactive UI components. These should never be spoken.
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
    slash read" in the middle of a sentence. Doing it here covers every
    path, the way `strip_xml` does.

    Line structure is preserved. A line left with no words after its tags
    are removed is dropped entirely, rather than read as an empty pause.
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


class TextSanitizer:
    """Cleans agent Markdown/XML output into natural spoken text."""
    
    @staticmethod
    def sanitize(text: str) -> str:
        if not text:
            return ""
            
        cleaned = text
        
        # 1. Remove XML/HTML tags and contents (e.g. <memory>...</memory>, <vote>...</vote>, <poll>...</poll>, <job>...</job>, etc.)
        xml_pattern = r"<([a-zA-Z][a-zA-Z0-9_:-]*)(?:\s+[^>]*)?>.*?</\1>"
        prev = None
        while prev != cleaned:
            prev = cleaned
            cleaned = re.sub(xml_pattern, "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"<[a-zA-Z][a-zA-Z0-9_:-]*(?:\s+[^>]*)?/>", "", cleaned)
        cleaned = re.sub(r"<[a-zA-Z][a-zA-Z0-9_:-]*(?:\s+[^>]*)?>.*$", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"</?[a-zA-Z][a-zA-Z0-9_:-]*(?:\s+[^>]*)?>", "", cleaned)
        
        # 2. Replace multi-line code blocks with a brief spoken note
        cleaned = re.sub(r"```[a-zA-Z0-9_-]*\n(.*?)```", " (I have provided the code in the chat.) ", cleaned, flags=re.DOTALL)
        
        # 3. Replace inline code `code` with code
        cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
        
        # 4. Replace markdown links [label](url) with label
        cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
        
        # 5. Remove raw URLs
        cleaned = re.sub(r"https?://\S+", "", cleaned)
        
        # 6. Remove markdown header symbols (#), bold/italic (*, _), blockquotes (>), strikethrough (~~)
        cleaned = re.sub(r"^#{1,6}\s+", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"[*_~]{1,3}", "", cleaned)
        cleaned = re.sub(r"^>\s+", "", cleaned, flags=re.MULTILINE)
        
        # 7. Convert markdown bullet lists to comma pauses
        cleaned = re.sub(r"^\s*[-*+]\s+", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"^\s*\d+\.\s+", "", cleaned, flags=re.MULTILINE)
        
        # 8. Remove all emojis (Unicode emojis, symbols, dingbats, and Discord custom emojis)
        emoji_pattern = (
            r"<a?:[a-zA-Z0-9_]+:[0-9]+>"  # Discord custom emoji
            r"|[\U00010000-\U0010ffff]"   # SMP (emojis, pictographs)
            r"|[\u200d\ufe0e\ufe0f]"      # Zero-width joiner & variation selectors
            r"|[\u2600-\u27bf]"           # Misc symbols & Dingbats (✅, ⚡, ✨, ❌, etc.)
            r"|[\u2300-\u23ff]"           # Misc technical
            r"|[\u2b50-\u2b55]"           # Stars and geometric shapes
            r"|[\u203c\u2049\u2139\u2194-\u21aa\u25aa\u25ab\u25b6\u25c0\u25fb-\u25fe\u2934\u2935\u3030\u303d\u3297\u3299]"
        )
        cleaned = re.sub(emoji_pattern, "", cleaned)
        
        # 9. Collapse whitespace and line breaks
        cleaned = re.sub(r"\n+", ". ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        
        return cleaned


def speakable(text: str) -> str:
    """`text` as the synthesizer should get it: notation in words, markup gone."""
    return TextSanitizer.sanitize(speak_priority_symbols(strip_hashtags(strip_xml(text))))
