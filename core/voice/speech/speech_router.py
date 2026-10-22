"""Splitting the agent's stream into what is said and what is shown.

On a voice turn the agent writes a <speech> block for the ear before its
normal reply (`<voice_mode>`, core/prompts/voice.py). The router runs
upstream of the text channel: the speech lane gets what is inside
<speech>...</speech>, the text lane everything else, so Discord's message
splitting never touches speech and speech never reaches Discord. A tag split
across tokens ("<spe" + "ech>") is held back until the next token decides it.

A closed block ends with a line break, which the voice stream takes as the
end of a chunk: a short preamble is spoken while its tool runs instead of
waiting for the answer, and two blocks never run together ("calendar.You").
"""
from typing import Tuple

OPEN, CLOSE = "<speech>", "</speech>"
BLOCK_END = "\n"


class SpeechTextRouter:
    """Splits streamed tokens into (speech, text) on <speech>...</speech>."""

    def __init__(self):
        self.in_speech = False
        self.pending = ""

    def feed(self, token: str) -> Tuple[str, str]:
        """Routes one token; returns the (speech, text) it completes."""
        buf, speech, text = self.pending + token, [], []
        while buf:
            lane = speech if self.in_speech else text
            tag = CLOSE if self.in_speech else OPEN
            i = buf.lower().find(tag)
            if i >= 0:
                lane.append(buf[:i] + (BLOCK_END if self.in_speech else ""))
                buf, self.in_speech = buf[i + len(tag):], not self.in_speech
                continue
            keep = _partial_suffix(buf.lower(), tag)  # hold "<spe" until the next token
            lane.append(buf[:len(buf) - keep])
            buf = buf[len(buf) - keep:]
            break
        self.pending = buf
        return "".join(speech), "".join(text)

    def flush(self) -> Tuple[str, str]:
        """Releases what was held back for a tag that never completed. Inside
        a block that can only be a cut-off "</speech" -- markup, so it's
        dropped rather than read out."""
        held, self.pending = self.pending, ""
        return ("", "") if self.in_speech else ("", held)


def _partial_suffix(buf: str, tag: str) -> int:
    """The length of the longest end of `buf` that could start `tag`."""
    for n in range(min(len(buf), len(tag) - 1), 0, -1):
        if tag.startswith(buf[-n:]):
            return n
    return 0
