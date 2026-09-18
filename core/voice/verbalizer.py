"""Turning written agent output into something worth listening to.

`TextSanitizer` makes text *safe* to speak by deleting what can't be pronounced:
the bullet becomes nothing, the table collapses into a run of bare cells, the
heading merges into the sentence after it. That is the difference between a
plan that is readable and the same plan read aloud as a list of fragments.

This module restructures instead of deleting. It is not a summariser -- the
no-compression contract lives with the prompt in `core.voice.prompts`.

The model is only consulted for text that has structure to restructure. Plain
conversational prose -- the majority of turns -- takes the fast path and never
leaves the process, because a round-trip before the first word of a two-sentence
reply is the one latency cost a voice conversation cannot hide.

Notation that always resolves the same way -- XML blocks, filing hashtags,
priority symbols -- is handled here in code rather than asked of the model. A
prompt rule only applies on the turns that reach the model, and these markers
turn up just as often in the prose that does not.
"""
import asyncio
import re
from typing import List, Optional

from core.util.models import DEFAULT_VERBALIZER_MODEL
from core.voice.prompts import build_verbalizer_prompt
from core.voice.tts_engine import TextSanitizer

# Markers that mean the text was written to be *seen*: list bullets, numbered
# items, headings, tables, code fences, or bold/italic runs. Their presence is
# what justifies a model call.
_STRUCTURE_PATTERNS = (
    r"^\s*[-*+]\s+",          # bullet list
    r"^\s*\d+[.)]\s+",        # numbered list
    r"^\s*#{1,6}\s+",         # heading
    r"\|.*\|",                # table row
    r"```",                   # code fence
    r"\*\*[^*]+\*\*",         # bold run
    r"^\s*>\s+",              # blockquote
)

_STRUCTURE_RE = re.compile("|".join(_STRUCTURE_PATTERNS), re.MULTILINE)


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


def has_structure(text: str) -> bool:
    """Whether the text was laid out visually and needs restructuring for speech."""
    if not text:
        return False
    text = strip_xml(text)
    if not text:
        return False
    return bool(_STRUCTURE_RE.search(text))


class Verbalizer:
    """Rewrites a block of agent output as speech.

    Falls back to `TextSanitizer` on any failure. A degraded voice reply is
    recoverable; an exception in the middle of a spoken turn is silence.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_VERBALIZER_MODEL,
        enabled: bool = True,
        timeout: float = 8.0,
    ):
        self.model_name = model_name
        self.enabled = enabled
        self.timeout = timeout
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            from langchain_google_genai import ChatGoogleGenerativeAI

            self._llm = ChatGoogleGenerativeAI(model=self.model_name)
        return self._llm

    async def verbalize(self, text: str) -> str:
        """Returns speech-ready text. Never raises."""
        if not text or not text.strip():
            return ""

        text = strip_xml(text)
        if not text or not text.strip():
            return ""

        # Before the structure check, so the model and the sanitizer fallback
        # both receive words rather than symbols the sanitizer would delete.
        text = speak_priority_symbols(strip_hashtags(text))
        if not text.strip():
            return ""

        if not self.enabled or not has_structure(text):
            return TextSanitizer.sanitize(text)

        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            response = await asyncio.wait_for(
                self._get_llm().ainvoke([
                    SystemMessage(content=build_verbalizer_prompt()),
                    HumanMessage(content=text),
                ]),
                timeout=self.timeout,
            )
            spoken = getattr(response, "content", "") or ""
            if isinstance(spoken, list):
                spoken = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in spoken
                )
            spoken = spoken.strip()
            if spoken:
                # Sanitised anyway: the model is asked for plain speech, but a
                # stray asterisk reaching the synthesiser is read out loud.
                return TextSanitizer.sanitize(strip_xml(spoken))
        except asyncio.TimeoutError:
            print(f"[Verbalizer] Timed out after {self.timeout}s; speaking sanitized text.")
        except Exception as e:
            print(f"[Verbalizer] Failed ({e}); speaking sanitized text.")

        return TextSanitizer.sanitize(text)


class VoiceStream:
    """Accumulates streamed tokens and yields speech-ready chunks.

    Two cadences, chosen per block rather than per turn:

    - **Prose** is emitted sentence by sentence as it arrives, so audio starts
      while the agent is still writing.
    - **Structured text** is held until the block is complete -- a blank line, a
      list that stops, or a size cap -- because a bullet cannot be rewritten for
      speech until you can see the whole list it belongs to.

    Waiting for a block boundary costs latency, which is why it is not the
    default path. A conversational answer never pays it.
    """

    def __init__(
        self,
        verbalizer: Optional[Verbalizer] = None,
        min_chars: int = 20,
        max_chars: int = 140,
        max_block_chars: int = 900,
    ):
        self.verbalizer = verbalizer or Verbalizer()
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.max_block_chars = max_block_chars
        self._buffer = ""

        from core.voice.sentence_chunker import SentenceChunker

        self._splitter = SentenceChunker(min_chars=min_chars, max_chars=max_chars)

    async def add_token(self, token: str) -> List[str]:
        """Appends a token delta and returns any chunks ready to speak."""
        if not token:
            return []
        self._buffer += token
        return await self._drain(final=False)

    async def flush(self) -> List[str]:
        """Emits whatever remains, verbalizing it if it has structure."""
        return await self._drain(final=True)

    async def _drain(self, final: bool) -> List[str]:
        chunks: List[str] = []

        if final:
            self._buffer = strip_xml(self._buffer)
        else:
            self._buffer = strip_completed_xml(self._buffer)

        while self._buffer:
            unclosed_idx = None if final else find_unclosed_xml_start(self._buffer)
            if unclosed_idx == 0:
                break

            if unclosed_idx is not None:
                work_buf = self._buffer[:unclosed_idx]
                held = self._buffer[unclosed_idx:]
            else:
                work_buf = self._buffer
                held = ""

            if has_structure(work_buf):
                block, rest = self._take_block(final, work_buf)
                if block is None:
                    break
                self._buffer = rest + held
                spoken = await self.verbalizer.verbalize(block)
                chunks.extend(self._splitter.split_into_sentences(spoken))
            else:
                sentence, rest = self._take_sentence(final, work_buf)
                if sentence is None:
                    break
                self._buffer = rest + held
                # This branch never reaches `verbalize`, so the same notation
                # pass has to happen here. Sentences arrive at a boundary, so
                # a tag or symbol is whole by the time it is emitted.
                spoken = speak_priority_symbols(strip_hashtags(sentence))
                cleaned = TextSanitizer.sanitize(spoken)
                if cleaned:
                    chunks.append(cleaned)

            if not final:
                self._buffer = strip_completed_xml(self._buffer)

        return [c for c in chunks if c]

    def _take_block(self, final: bool, text: Optional[str] = None):
        """Splits off a complete structural block, or (None, buffer) if pending."""
        buf = self._buffer if text is None else text
        boundary = buf.find("\n\n")
        if boundary != -1:
            return buf[:boundary], buf[boundary + 2:].lstrip("\n")

        if len(buf) >= self.max_block_chars:
            # A list that never ends still has to be spoken. Break on the last
            # line boundary so a bullet is not split down the middle.
            cut = buf.rfind("\n", 0, self.max_block_chars)
            if cut > 0:
                return buf[:cut], buf[cut + 1:]
            return buf[:self.max_block_chars], buf[self.max_block_chars:]

        if final:
            return buf, ""

        return None, buf

    def _take_sentence(self, final: bool, text: Optional[str] = None):
        """Splits off one prose sentence, or (None, buffer) if none is complete."""
        buf = self._buffer if text is None else text
        position = self._splitter._find_split_position(buf)
        if position is None and len(buf) >= self.max_chars:
            position = self._splitter._find_fallback_split(buf)

        if position is None:
            if final:
                return buf, ""
            return None, buf

        return buf[:position], buf[position:].lstrip()
