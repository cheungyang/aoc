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

A hand-over written inside a block -- <switch to="..."/>, or one carrying a
brief for the teammate, <switch to="...">brief</switch> -- is never said
(`_BriefFilter`). team.md asks for the tag outside <speech>, but when a model
puts it inside anyway, a brief spans sentences, and the voice stream strips
tags one sentence at a time (speech_util.strip_xml): it would read the rest of
the brief out.
"""
import re
from typing import Tuple

OPEN, CLOSE = "<speech>", "</speech>"
BLOCK_END = "\n"
SWITCH_CLOSE = "</switch>"
# "<switch" opens a tag only before a space, "/" or ">": "<switchboard>" is not one.
_SWITCH_OPEN = re.compile(r"<switch(?=[\s/>])", re.IGNORECASE)
_SWITCH_END = re.compile(r"</switch\s*>", re.IGNORECASE)
# What may start a switch tag, for holding back the end of a token: "<switch"
# and the character after it.
_SWITCH_PREFIX = "<switch "
# Where _BriefFilter is in a block: in what's said, inside a switch's opening
# tag, or in its brief.
_SAID, _TAG, _BRIEF = "said", "tag", "brief"


class SpeechTextRouter:
    """Splits streamed tokens into (speech, text) on <speech>...</speech>."""

    def __init__(self):
        self.in_speech = False
        self.pending = ""
        self._briefs = _BriefFilter()

    def feed(self, token: str) -> Tuple[str, str]:
        """Routes one token; returns the (speech, text) it completes."""
        buf, speech, text = self.pending + token, [], []
        while buf:
            tag = CLOSE if self.in_speech else OPEN
            i = buf.lower().find(tag)
            if i >= 0:
                if self.in_speech:
                    speech.append(self._briefs.feed(buf[:i]) + self._briefs.end() + BLOCK_END)
                else:
                    text.append(buf[:i])
                buf, self.in_speech = buf[i + len(tag):], not self.in_speech
                continue
            keep = _partial_suffix(buf.lower(), tag)  # hold "<spe" until the next token
            part = buf[:len(buf) - keep]
            if self.in_speech:
                speech.append(self._briefs.feed(part))
            else:
                text.append(part)
            buf = buf[len(buf) - keep:]
            break
        self.pending = buf
        return "".join(speech), "".join(text)

    def flush(self) -> Tuple[str, str]:
        """Releases what was held back for a tag that never completed. Inside
        a block that can only be a cut-off "</speech" -- markup, so it's
        dropped rather than read out."""
        held, self.pending = self.pending, ""
        if self.in_speech:
            self._briefs.end()
            return "", ""
        return "", held


class _BriefFilter:
    """Takes <switch ...> elements, brief and all, out of one <speech> block's
    text as it streams. A tag split across tokens is held back until the next
    token decides it; a tag or brief the block ends inside is markup, dropped."""

    def __init__(self):
        self.state = _SAID
        self.pending = ""    # held back: an end of the text that may start a tag
        self.slash = False   # inside the opening tag: its last character so far was "/"

    def feed(self, chunk: str) -> str:
        """What of `chunk` is said."""
        buf, said = self.pending + chunk, []
        self.pending = ""
        while buf:
            if self.state == _SAID:
                found = _SWITCH_OPEN.search(buf)
                if found is None:
                    keep = _partial_suffix(buf.lower(), _SWITCH_PREFIX)
                    said.append(buf[:len(buf) - keep])
                    self.pending = buf[len(buf) - keep:]
                    break
                said.append(buf[:found.start()])
                buf, self.state, self.slash = buf[found.end():], _TAG, False
            elif self.state == _TAG:
                end = buf.find(">")
                if end < 0:  # all of it is the tag
                    self.slash = buf.endswith("/")
                    break
                closed = buf[end - 1] == "/" if end > 0 else self.slash
                buf, self.state = buf[end + 1:], (_SAID if closed else _BRIEF)
            else:
                found = _SWITCH_END.search(buf)
                if found is None:  # all of it is the brief, but maybe the start of its end
                    keep = _partial_suffix(buf.lower(), SWITCH_CLOSE)
                    self.pending = buf[len(buf) - keep:]
                    break
                buf, self.state = buf[found.end():], _SAID
        return "".join(said)

    def end(self) -> str:
        """The block is over: what was held back, if it's part of what's said
        (a "<sw" that never became a tag). Ready for the next block."""
        held, said = self.pending, self.state == _SAID
        self.pending, self.state, self.slash = "", _SAID, False
        return held if said else ""


def _partial_suffix(buf: str, tag: str) -> int:
    """The length of the longest end of `buf` that could start `tag`."""
    for n in range(min(len(buf), len(tag) - 1), 0, -1):
        if tag.startswith(buf[-n:]):
            return n
    return 0
