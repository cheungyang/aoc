"""Streaming agent tokens into speech-ready chunks."""
from typing import List, Optional

from core.voice.speech_util import (
    find_unclosed_xml_start,
    speak_priority_symbols,
    strip_completed_xml,
    strip_hashtags,
    strip_xml,
)
from core.voice.tts_engine import TextSanitizer
from core.voice.verbalizer import Verbalizer, has_structure


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
        session=None,
    ):
        self.verbalizer = verbalizer or Verbalizer()
        self.min_chars = min_chars
        self.max_chars = max_chars
        self.max_block_chars = max_block_chars
        # The turn's ExecutionContext, so model calls made while restructuring
        # a block are billed to it. Optional: without it the verbalizer falls
        # back to the ambient context.
        self.session = session
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
                if self.session is not None:
                    spoken = await self.verbalizer.verbalize(block, session=self.session)
                else:
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
