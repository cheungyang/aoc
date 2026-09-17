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


def has_structure(text: str) -> bool:
    """Whether the text was laid out visually and needs restructuring for speech."""
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
                return TextSanitizer.sanitize(spoken)
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

        while self._buffer:
            if has_structure(self._buffer):
                block, rest = self._take_block(final)
                if block is None:
                    break
                self._buffer = rest
                spoken = await self.verbalizer.verbalize(block)
                chunks.extend(self._splitter.split_into_sentences(spoken))
            else:
                sentence, rest = self._take_sentence(final)
                if sentence is None:
                    break
                self._buffer = rest
                cleaned = TextSanitizer.sanitize(sentence)
                if cleaned:
                    chunks.append(cleaned)

        return [c for c in chunks if c]

    def _take_block(self, final: bool):
        """Splits off a complete structural block, or (None, buffer) if pending."""
        boundary = self._buffer.find("\n\n")
        if boundary != -1:
            return self._buffer[:boundary], self._buffer[boundary + 2:].lstrip("\n")

        if len(self._buffer) >= self.max_block_chars:
            # A list that never ends still has to be spoken. Break on the last
            # line boundary so a bullet is not split down the middle.
            cut = self._buffer.rfind("\n", 0, self.max_block_chars)
            if cut > 0:
                return self._buffer[:cut], self._buffer[cut + 1:]
            return self._buffer[:self.max_block_chars], self._buffer[self.max_block_chars:]

        if final:
            return self._buffer, ""

        return None, self._buffer

    def _take_sentence(self, final: bool):
        """Splits off one prose sentence, or (None, buffer) if none is complete."""
        position = self._splitter._find_split_position(self._buffer)
        if position is None and len(self._buffer) >= self.max_chars:
            position = self._splitter._find_fallback_split(self._buffer)

        if position is None:
            if final:
                return self._buffer, ""
            return None, self._buffer

        return self._buffer[:position], self._buffer[position:].lstrip()
