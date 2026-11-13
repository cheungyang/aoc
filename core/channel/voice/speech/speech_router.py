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

A directive written inside a block is never said (`_BriefFilter`): a
hand-over -- <switch to="..."/>, or one carrying a brief for the teammate,
<switch to="...">brief</switch> -- or a pass, <pass to="...">brief</pass>,
which leaves the teammate a brief and keeps the user. team.md asks for both
tags outside <speech>, but when a model puts one inside anyway, a brief spans
sentences, and the voice stream strips tags one sentence at a time
(speech_util.strip_xml): it would read the rest of the brief out.
"""
import re
from typing import Tuple

OPEN, CLOSE = "<speech>", "</speech>"
BLOCK_END = "\n"
# The directives a block never says. "<switch" or "<pass" opens a tag only
# before a space, "/" or ">": "<switchboard>" and "<password>" are not ones.
_DIRECTIVE_OPEN = re.compile(r"<(switch|pass)(?=[\s/>])", re.IGNORECASE)
# What ends each one's brief: its own closing tag, never the other's.
_DIRECTIVE_END = {tag: re.compile(rf"</{tag}\s*>", re.IGNORECASE) for tag in ("switch", "pass")}
# What may start a directive's tag, for holding back the end of a token: its
# name and the character after it.
_DIRECTIVE_PREFIXES = ("<switch ", "<pass ")
# Where _BriefFilter is in a block: in what's said, inside a directive's
# opening tag, or in its brief.
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
    """Takes <switch ...> and <pass ...> elements, brief and all, out of one
    <speech> block's text as it streams. Only the closing tag of the directive
    that opened ends its brief. A tag split across tokens is held back until
    the next token decides it; a tag or brief the block ends inside is markup,
    dropped."""

    def __init__(self):
        self.state = _SAID
        self.pending = ""    # held back: an end of the text that may start a tag
        self.slash = False   # inside the opening tag: its last character so far was "/"
        self.tag = ""        # the directive whose tag or brief this is: "switch" or "pass"

    def feed(self, chunk: str) -> str:
        """What of `chunk` is said."""
        buf, said = self.pending + chunk, []
        self.pending = ""
        while buf:
            if self.state == _SAID:
                found = _DIRECTIVE_OPEN.search(buf)
                if found is None:
                    keep = max(_partial_suffix(buf.lower(), prefix) for prefix in _DIRECTIVE_PREFIXES)
                    said.append(buf[:len(buf) - keep])
                    self.pending = buf[len(buf) - keep:]
                    break
                said.append(buf[:found.start()])
                buf, self.state, self.slash, self.tag = buf[found.end():], _TAG, False, found.group(1).lower()
            elif self.state == _TAG:
                end = buf.find(">")
                if end < 0:  # all of it is the tag
                    self.slash = buf.endswith("/")
                    break
                closed = buf[end - 1] == "/" if end > 0 else self.slash
                buf, self.state = buf[end + 1:], (_SAID if closed else _BRIEF)
            else:
                found = _DIRECTIVE_END[self.tag].search(buf)
                if found is None:  # all of it is the brief, but maybe the start of its end
                    keep = _partial_suffix(buf.lower(), f"</{self.tag}>")
                    self.pending = buf[len(buf) - keep:]
                    break
                buf, self.state = buf[found.end():], _SAID
        return "".join(said)

    def end(self) -> str:
        """The block is over: what was held back, if it's part of what's said
        (a "<sw" or "<pa" that never became a tag). Ready for the next block."""
        held, said = self.pending, self.state == _SAID
        self.pending, self.state, self.slash, self.tag = "", _SAID, False, ""
        return held if said else ""


def _partial_suffix(buf: str, tag: str) -> int:
    """The length of the longest end of `buf` that could start `tag`."""
    for n in range(min(len(buf), len(tag) - 1), 0, -1):
        if tag.startswith(buf[-n:]):
            return n
    return 0
