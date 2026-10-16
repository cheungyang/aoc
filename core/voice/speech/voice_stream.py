"""Streaming a turn's speech into sentences to synthesize as they're written."""
from typing import List

from core.voice.speech.sentence_chunker import SentenceChunker
from core.voice.speech.speech_util import speakable


class VoiceStream:
    """Accumulates the turn's <speech> text and yields sentences as they
    complete, so audio starts while the agent is still writing.

    A stretch of speech opens on its first sentence however short it is
    ("Sure."): Chirp starts speaking at a sentence's end, and not before -- a
    clause sent early waits there for the rest of its sentence -- so the first
    sentence end is the soonest audio can start. Later chunks are sentences of
    at least `min_chars`. A line break ends a chunk however short it is: the
    router puts one after every <speech> block, so a block is spoken as soon as
    it closes, and the next one opens on its first sentence again.

    The text was written for the ear (`<voice_mode>`), so nothing is rewritten
    here: each chunk only gets the notation pass and the sanitizer
    (`speakable`), in case a hashtag or a stray asterisk slipped in.
    """

    def __init__(self, min_chars: int = 20, max_chars: int = 140):
        self._splitter = SentenceChunker(min_chars=min_chars, max_chars=max_chars)
        self._opener = SentenceChunker(min_chars=1, max_chars=max_chars)
        self._buffer = ""
        self._opening = True  # the next chunk opens a stretch of speech

    def add(self, text: str) -> List[str]:
        """Appends speech text and returns any sentences ready to speak."""
        if not text:
            return []
        self._buffer += text
        return self._drain(final=False)

    def flush(self) -> List[str]:
        """Returns whatever remains; the next text opens a new stretch."""
        chunks = self._drain(final=True)
        self._opening = True
        return chunks

    def _drain(self, final: bool) -> List[str]:
        chunks: List[str] = []
        while self._buffer:
            splitter = self._opener if self._opening else self._splitter
            position = splitter._find_split_position(self._buffer)
            line_end = self._buffer.find("\n")
            if line_end >= 0 and (position is None or line_end < position):
                position = line_end + 1
            if position is None and len(self._buffer) >= self._splitter.max_chars:
                position = self._splitter._find_fallback_split(self._buffer)
            if position is None:
                if not final:
                    break
                position = len(self._buffer)
            chunk, rest = self._buffer[:position], self._buffer[position:]
            self._buffer = rest.lstrip()
            ends_line = "\n" in chunk or "\n" in rest[:len(rest) - len(self._buffer)]
            spoken = speakable(chunk.strip())  # the sanitizer reads a line break as a full stop
            if spoken:
                chunks.append(spoken)
                self._opening = False
            if ends_line:
                self._opening = True
        return chunks
