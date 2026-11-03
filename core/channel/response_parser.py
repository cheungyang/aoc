"""A reply, read: the text the channel shows, and the control tags around it.

An agent's reply is markdown for the channel plus tags the runtime acts on:
<switch> (hand the user to a teammate), <speech> (what a voice turn says
aloud), <poll>, <images>, <videos> and <system_memory_log>.
`AgentResponse.from_string` takes each out of the text, <switch> first, so one
written inside <speech> is never read out.

A tag inside code is text, not a directive. A reply that explains the syntax
quotes it -- "the syntax is `<switch to="ID"/>`" -- and that must neither
switch the user nor open a memory log that swallows the rest of the reply.
Code is a fenced block that was closed, or an inline span closed on its own
line (`code_spans`): an unclosed fence or a stray backtick doesn't turn the
rest of the reply into code, so it can't hide a real tag after it. The tags
are found on a copy of the reply with code masked (`mask_code`) and taken out
of the reply itself, whose code stays as it was written. The Discord stream
buffer hides tags by the same rule (core/channel/discord/stream_buffer.py).
"""
from dataclasses import dataclass
from typing import Iterable, Optional, Dict, List, Tuple
import re

# Code in a reply. A fenced block opens on a line of three or more backticks or
# tildes and closes on a line of the same character, at least as long; a
# backtick fence's info string has no backtick, so ```this``` on one line is
# inline code. An inline span is a run of backticks closed by a run of the same
# length on the same line.
_FENCED_RE = re.compile(
    r"^[ \t]{0,3}(?:(?P<ticks>`{3,})[^`\n]*|(?P<tildes>~{3,})[^\n]*)$"
    r".*?"
    r"^[ \t]{0,3}(?:(?P=ticks)`*|(?P=tildes)~*)[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
_INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
# What a "<" inside code becomes in the masked copy: no tag can start there.
_MASK = "\x00"


def code_spans(text: str) -> List[Tuple[int, int]]:
    """Where `text` holds code, as sorted (start, end) offsets: each closed
    fenced block, fences included, and each inline span outside them."""
    fenced = [m.span() for m in _FENCED_RE.finditer(text)]
    inline = [m.span() for m in _INLINE_CODE_RE.finditer(text)
              if not any(start <= m.start() < end for start, end in fenced)]
    return sorted(fenced + inline)


def mask_code(text: str) -> str:
    """`text` with every "<" inside code masked, so no tag pattern matches
    there. As long as `text`: a match's offsets are `text`'s."""
    spans = code_spans(text)
    if not spans:
        return text
    parts, last = [], 0
    for start, end in spans:
        parts += [text[last:start], text[start:end].replace("<", _MASK)]
        last = end
    parts.append(text[last:])
    return "".join(parts)


def _cut(text: str, spans: Iterable[Tuple[int, int]]) -> str:
    """`text` without the (start, end) spans, which are in order and don't overlap."""
    kept, last = [], 0
    for start, end in spans:
        kept.append(text[last:start])
        last = end
    kept.append(text[last:])
    return "".join(kept)


def _group(text: str, match: "re.Match", group: int) -> Optional[str]:
    """`match`'s group, read from `text`: the match was made on its masked copy."""
    start, end = match.span(group)
    return text[start:end] if start >= 0 else None


_POLL_RE = re.compile(r'<poll(.*?)>(.*?)</poll>', re.DOTALL)


def _extract_poll(text: str) -> tuple[str, Optional[Dict]]:
    """Extracts and strips the first <poll> XML block outside code."""
    poll_match = _POLL_RE.search(mask_code(text))
    if not poll_match:
        return text, None

    cleaned_text = _cut(text, [poll_match.span()]).strip()

    poll_attrs = _group(text, poll_match, 1)
    poll_content = _group(text, poll_match, 2)
    allow_multiple = 'allow_multiple="true"' in poll_attrs

    poll_data = {
        "question": "",
        "allow_multiple": allow_multiple,
        "options": []
    }

    question_match = re.search(
        r'<question>(.*?)</question>', poll_content, re.DOTALL
    )
    if question_match:
        poll_data["question"] = question_match.group(1).strip()

    options_match = re.search(
        r'<options>(.*?)</options>', poll_content, re.DOTALL
    )
    if options_match:
        options_content = options_match.group(1)
        for opt_match in re.finditer(
            r'<option>(.*?)</option>', options_content, re.DOTALL
        ):
            opt_content = opt_match.group(1)
            opt_data = {}
            for field in ["text", "emoji", "response"]:
                f_match = re.search(
                    f'<{field}>(.*?)</{field}>', opt_content, re.DOTALL
                )
                opt_data[field] = f_match.group(1).strip() if f_match else ""
            poll_data["options"].append(opt_data)

    return cleaned_text, poll_data


_IMAGES_RE = re.compile(r'<images>(.*?)</images>', re.DOTALL)


def _extract_images(text: str) -> tuple[str, Optional[List[str]]]:
    """Extracts and strips the first <images> XML block outside code."""
    images_match = _IMAGES_RE.search(mask_code(text))
    if not images_match:
        return text, None

    cleaned_text = _cut(text, [images_match.span()]).strip()

    image_paths = []
    images_content = _group(text, images_match, 1)
    for img_match in re.finditer(
        r'<image\s+path="(.*?)"\s*/>', images_content
    ):
        image_paths.append(img_match.group(1))

    return cleaned_text, image_paths


_VIDEOS_RE = re.compile(r'<videos>(.*?)</videos>', re.DOTALL)


def _extract_videos(text: str) -> tuple[str, Optional[List[str]]]:
    """Extracts and strips the first <videos> XML block outside code."""
    videos_match = _VIDEOS_RE.search(mask_code(text))
    if not videos_match:
        return text, None

    cleaned_text = _cut(text, [videos_match.span()]).strip()

    video_paths = []
    videos_content = _group(text, videos_match, 1)
    for vid_match in re.finditer(
        r'<video\s+path="(.*?)"\s*/>', videos_content
    ):
        video_paths.append(vid_match.group(1))

    return cleaned_text, video_paths


# A memory log runs from the nearest opener before its closer: an earlier
# opener with no closer of its own -- the tag named in passing -- doesn't
# swallow the text up to the real log.
_MEMORY_LOG_RE = re.compile(
    r'<system_memory_log>((?:(?!<system_memory_log>).)*?)</system_memory_log>', re.DOTALL
)


def _extract_system_memory_log(text: str) -> tuple[str, Optional[str]]:
    """Extracts and strips the <system_memory_log> blocks outside code. The last
    one is the log: it belongs at the end of the reply."""
    blocks = list(_MEMORY_LOG_RE.finditer(mask_code(text)))
    if not blocks:
        return text, None

    cleaned_text = _cut(text, [block.span() for block in blocks]).strip()
    system_memory_log = _group(text, blocks[-1], 1).strip()

    return cleaned_text, system_memory_log


_SPEECH_BLOCK_RE = re.compile(r"<speech\s*>(.*?)</speech\s*>", re.DOTALL | re.IGNORECASE)
_SPEECH_OPEN_RE = re.compile(r"<speech\s*>", re.IGNORECASE)
_SPEECH_TAG_RE = re.compile(r"</?speech\s*>", re.IGNORECASE)


def _extract_speech(text: str) -> tuple[str, Optional[str]]:
    """Extracts and strips <speech> blocks: what a voice turn says aloud.

    A reply can hold several -- a sentence before a tool call, then the
    answer's -- and they are joined in order. A block the model never closed
    is speech too, but its words also stay in the text: the channel must not
    lose the reply to a missing tag. Tags inside code are text.
    """
    masked = mask_code(text)
    if not _SPEECH_TAG_RE.search(masked):
        return text, None

    closed = list(_SPEECH_BLOCK_RE.finditer(masked))
    blocks = [_group(text, m, 1).strip() for m in closed]
    spans = [m.span() for m in closed]
    cleaned_text, masked = _cut(text, spans), _cut(masked, spans)
    unclosed = _SPEECH_OPEN_RE.search(masked)
    if unclosed:
        blocks.append(cleaned_text[unclosed.end():].strip())
    cleaned_text = _cut(cleaned_text, [m.span() for m in _SPEECH_TAG_RE.finditer(masked)]).strip()
    speech = " ".join(block for block in blocks if block)
    return cleaned_text, speech or None


_QUESTION_BLOCK_RE = re.compile(r"<question\s*>(.*?)</question\s*>", re.DOTALL | re.IGNORECASE)
_QUESTION_TAG_RE = re.compile(r"</?question\s*>", re.IGNORECASE)


def split_question(speech: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """A speech version without its <question> tags, and the question they marked.

    The question's words stay where they were in the speech: they're heard in
    place the first time, and replayed alone while the post waits for an
    answer (core/channel/voice/briefing). Only <question> inside <speech> is meant here; a
    poll's <question> lives outside it and is never passed in.
    """
    if not speech or not _QUESTION_TAG_RE.search(speech):
        return speech, None
    marked = [" ".join(m.group(1).split()) for m in _QUESTION_BLOCK_RE.finditer(speech)]
    cleaned = _QUESTION_TAG_RE.sub("", speech).strip()
    for q in filter(None, marked):
        # A model that marks a copy instead of the question itself says it twice.
        while f"{q} {q}" in cleaned:
            cleaned = cleaned.replace(f"{q} {q}", q)
    question = " ".join(q for q in marked if q)
    return cleaned or None, question or None


# A reply that hands the user to a teammate ends with <switch to="goal-setter"/>
# (core/prompts/templates/system/team.md): the voice room moves there, and the
# channel shows where to go. When the teammate should pick up the work, the
# agent writes a brief for it inside: <switch to="goal-setter">brief</switch>.
# The brief never crosses another <switch>, so a bare opener can't take a
# later directive in as its brief.
_SWITCH_RE = re.compile(
    r"<switch\b[^>]*?\bto\s*=\s*[\"']([^\"']*)[\"'][^>]*?"
    r"(?:/>(?:\s*</switch\s*>)?|>(?:((?:(?!<switch\b).)*?)</switch\s*>)?)",
    re.IGNORECASE | re.DOTALL,
)
# The longest brief kept, in characters: it goes into the teammate's capsule
# (core/runtime/history/handoff.py), which is never cut to fit around it.
SWITCH_BRIEF_CHARS = 1000


def _extract_switch(text: str) -> tuple[str, Optional[str], Optional[str]]:
    """Extracts and strips <switch> directives outside code, wherever they are
    (inside <speech> too, so the voice never reads one out), briefs and all.
    The last one counts: its target, and its brief."""
    found = list(_SWITCH_RE.finditer(mask_code(text)))
    if not found:
        return text, None, None
    target = _group(text, found[-1], 1).strip()
    brief = _brief(_group(text, found[-1], 2))
    return _cut(text, [m.span() for m in found]).strip(), target or None, brief


def _brief(raw: Optional[str]) -> Optional[str]:
    """A switch's brief on one line, at most SWITCH_BRIEF_CHARS; None if empty."""
    brief = " ".join((raw or "").split())
    if len(brief) > SWITCH_BRIEF_CHARS:
        brief = brief[:SWITCH_BRIEF_CHARS - 1].rstrip() + "…"
    return brief or None


@dataclass
class AgentResponse:
    text: str
    poll_data: Optional[Dict] = None
    image_paths: Optional[List[str]] = None
    video_paths: Optional[List[str]] = None
    system_memory_log: Optional[str] = None
    # The reply's <speech> blocks, on a voice turn (see <voice_mode>).
    speech: Optional[str] = None
    # The direct question marked inside <speech>, on a scheduled run whose
    # post may be heard later (see core/prompts/templates/voice/scheduled_voice_mode.md).
    question: Optional[str] = None
    # The teammate the reply hands the user to: the agent id in <switch to="..."/>.
    switch_to: Optional[str] = None
    # What the agent wrote inside that tag for the teammate, on one line: it goes
    # into the teammate's hand-off capsule (graphs/main), never to the user.
    switch_brief: Optional[str] = None

    @classmethod
    def from_string(cls, reply_text: str) -> 'AgentResponse':
        text = reply_text
        text, switch_to, switch_brief = _extract_switch(text)
        text, speech = _extract_speech(text)
        speech, question = split_question(speech)
        text, poll_data = _extract_poll(text)
        text, image_paths = _extract_images(text)
        text, video_paths = _extract_videos(text)
        text, system_memory_log = _extract_system_memory_log(text)

        return cls(
            text=text,
            poll_data=poll_data,
            image_paths=image_paths,
            video_paths=video_paths,
            system_memory_log=system_memory_log,
            speech=speech,
            question=question,
            switch_to=switch_to,
            switch_brief=switch_brief,
        )
