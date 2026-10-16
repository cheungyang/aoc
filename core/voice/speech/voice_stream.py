"""Streaming a turn's speech into sentences to synthesize one at a time."""
from typing import List

from core.voice.speech.sentence_chunker import SentenceChunker
from core.voice.speech.speech_util import speakable


class VoiceStream:
    """Accumulates the turn's <speech> text and yields sentences as they
    complete, so audio starts while the agent is still writing.

    The text was written for the ear (`<voice_mode>`), so nothing is rewritten
    here: each sentence only gets the notation pass and the sanitizer
    (`speakable`), in case a hashtag or a stray asterisk slipped in. A line
    break ends a chunk however short it is: the router puts one after every
    <speech> block, so a block is spoken as soon as it closes.
    """

    def __init__(self, min_chars: int = 20, max_chars: int = 140):
        self._splitter = SentenceChunker(min_chars=min_chars, max_chars=max_chars)
        self._buffer = ""

    def add(self, text: str) -> List[str]:
        """Appends speech text and returns any sentences ready to speak."""
        if not text:
            return []
        self._buffer += text
        return self._drain(final=False)

    def flush(self) -> List[str]:
        """Returns whatever remains, sentence by sentence."""
        return self._drain(final=True)

    def _drain(self, final: bool) -> List[str]:
        chunks: List[str] = []
        while self._buffer:
            position = self._splitter._find_split_position(self._buffer)
            line_end = self._buffer.find("\n")
            if line_end >= 0 and (position is None or line_end < position):
                position = line_end + 1
            if position is None and len(self._buffer) >= self._splitter.max_chars:
                position = self._splitter._find_fallback_split(self._buffer)
            if position is None:
                if not final:
                    break
                sentence, self._buffer = self._buffer, ""
            else:
                sentence, self._buffer = self._buffer[:position], self._buffer[position:].lstrip()
            spoken = speakable(sentence.strip())  # the sanitizer reads a line break as a full stop
            if spoken:
                chunks.append(spoken)
        return chunks
