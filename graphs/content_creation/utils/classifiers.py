"""Deterministic human-intent parsing for the content_creation graph.

Two tiers:

* **T0 — deterministic.** The whole trimmed message must match a finite,
  gate-specific grammar *exactly*. A T0 match is the only thing in this graph
  that is allowed to trigger a paid action.
* **T1 — unclear.** Everything else. The gate is re-presented with a numbered
  menu and the original message is kept verbatim as the *pending instruction*,
  so answering with an ordinal loses nothing. Nothing is generated, nothing is
  overwritten, nothing is spent.

Why this is strict
------------------
The previous implementation matched approval phrases with ``startswith`` /
``endswith`` over bare words, before checking for negation::

    "not good"      -> approved      # ends with "good"
    "no, revise it" -> approved      # starts with "no"... then matched "revise"

and every unrecognised message fell through to a default of ``revise_image``
(gate 1) or ``revise_copy`` (gate 2) — a paid regeneration triggered by a
message nobody understood.

Matching here is **whole-message equality** against an explicit set. There is
no substring path and no default revision target. ``"not good"`` is not equal
to ``"good"``, so the class of bug above cannot recur.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

# --------------------------------------------------------------------------
# Gates and actions
# --------------------------------------------------------------------------

GATE1 = "gate1"
GATE2 = "gate2"

APPROVE = "approve"
REVISE = "revise"
ABORT = "abort"
RETRY = "retry"
STATUS = "status"
UNCLEAR = "unclear"

# Targets each gate can actually act on *today*.
#
# Gate 2 deliberately has no "image" target: there is no edge from gate 2 back
# to ideation, so accepting one would regenerate nothing and then present a
# video still built from the previous image. It is rejected with an
# explanation instead of being silently re-pointed at the copy, which is what
# used to happen.
GATE_TARGETS: Dict[str, Tuple[str, ...]] = {
    GATE1: ("image", "plot"),
    GATE2: ("video", "remix", "copy"),
}

# Legacy decision strings the routers and task guards already understand.
_DECISION: Dict[Tuple[str, str], str] = {
    (GATE1, "image"): "revise_image",
    (GATE1, "plot"): "revise_plot",
    (GATE2, "video"): "revise_video",
    (GATE2, "remix"): "revise_remix",
    (GATE2, "copy"): "revise_copy",
}

_TARGET_LABELS: Dict[str, str] = {
    "image": "🎨 the base image",
    "plot": "🎬 the video plot / motion prompt",
    "video": "🎞️ the animated plate (re-render)",
    "remix": "🔊 the audio / subtitle remix",
    "copy": "✍️ the publication copy",
}

# Words a human might use for each target. Only consulted inside the explicit
# ``revise <target>: <instruction>`` form — never scanned for in free text.
_TARGET_ALIASES: Dict[str, str] = {
    "image": "image", "images": "image", "picture": "image", "pic": "image",
    "photo": "image", "still": "image", "art": "image", "artwork": "image",
    "illustration": "image", "base image": "image", "character": "image",
    "plot": "plot", "motion": "plot", "motion prompt": "plot", "script": "plot",
    "video plot": "plot", "storyboard": "plot", "prompt": "plot",
    "video": "video", "plate": "video", "animation": "video", "clip": "video",
    "render": "video", "raw video": "video", "visual plate": "video",
    "remix": "remix", "audio": "remix", "sound": "remix", "music": "remix",
    "subtitle": "remix", "subtitles": "remix", "subs": "remix", "sub": "remix",
    "overlay": "remix", "font": "remix", "caption timing": "remix",
    "copy": "copy", "caption": "copy", "captions": "copy", "post": "copy",
    "hashtag": "copy", "hashtags": "copy", "description": "copy",
}

# --------------------------------------------------------------------------
# T0 vocabularies — matched against the whole normalised message only
# --------------------------------------------------------------------------

_APPROVALS = frozenset({
    "approve", "approved", "approve it", "approve this", "i approve",
    "lgtm", "ship", "ship it", "looks good", "looks great", "good", "great",
    "nice", "perfect", "yes", "yep", "yeah", "y", "ok", "okay", "k",
    "proceed", "go", "go ahead", "continue", "next", "accept", "accepted",
    "confirm", "confirmed", "done", "finalize", "finalise", "publish",
    "👍", "👌", "🚀", "✅",
})

# Unambiguously negative, but with no target. These exist so that a rejection
# is never mistaken for an approval *and* never guessed into a regeneration.
_REJECTIONS = frozenset({
    "no", "nope", "nah", "not good", "not great", "bad", "reject", "rejected",
    "not yet", "no thanks", "disapprove", "hmm", "meh", "👎", "❌",
})

_ABORTS = frozenset({"abort", "cancel", "stop", "quit", "nevermind", "never mind"})
_RETRIES = frozenset({"retry", "try again", "again"})
_STATUSES = frozenset({"status", "?", "where are we", "what's the status", "whats the status"})

_REJECTION_REASON = (
    "That reads as a rejection, but it doesn't say what to change — "
    "so nothing has been regenerated."
)

# --------------------------------------------------------------------------
# T0 grammars
# --------------------------------------------------------------------------

_ORDINAL_RE = re.compile(r"^(\d{1,2})$")
_MULTI_ORDINAL_RE = re.compile(r"^\d{1,2}(?:\s*[+,&]\s*\d{1,2})+$")

# revise <target>: <instruction>   |   revise <target> - <instruction>
_REVISE_RE = re.compile(
    r"^(?:revise|redo|regenerate|re-generate|change|fix|update)\s+"
    r"(?:the\s+)?(?P<target>[a-z][a-z ]{1,18}?)\s*[:\-–—]\s*(?P<instruction>\S.*)$",
    re.IGNORECASE | re.DOTALL,
)

# revise <target>          (no instruction supplied)
_REVISE_BARE_RE = re.compile(
    r"^(?:revise|redo|regenerate|re-generate)\s+(?:the\s+)?(?P<target>[a-z][a-z ]{1,18})$",
    re.IGNORECASE,
)

# set style <x> / set aspect <x> / set duration <n>
_SET_RE = re.compile(
    r"^set\s+(?P<key>style|aspect|aspect[_ ]ratio|duration)\s+(?P<value>\S.*)$",
    re.IGNORECASE,
)

_SET_DEFERRED_REASON = (
    "Changing a run parameter mid-flight isn't wired up yet: it has to "
    "invalidate several assets at once (style changes the image *and* the "
    "plot), and that dependency cascade lands with the `decide` node. "
    "For now, `abort` and re-run the topic with the parameter you want."
)


# --------------------------------------------------------------------------
# Intent
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Intent:
    """The parsed result of one human message at one gate.

    ``action`` is always set. ``target`` is set only for ``revise``.
    ``reason`` is human-facing text explaining an ``unclear`` result, and is
    rendered on the clarification card.
    """

    action: str
    target: str = ""
    instruction: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    @property
    def is_clear(self) -> bool:
        return self.action != UNCLEAR

    @property
    def may_spend(self) -> bool:
        """True only for outcomes permitted to reach a generating node."""
        return self.action in (APPROVE, REVISE, RETRY)

    def decision(self, gate: str) -> str:
        """The legacy decision string the routers and task guards consume."""
        if self.action == APPROVE:
            return "approved"
        if self.action == REVISE:
            return _DECISION.get((gate, self.target), UNCLEAR)
        return self.action


@dataclass(frozen=True)
class Option:
    """One numbered choice on a gate card. The ordinal is its 1-based index."""

    action: str
    target: str
    label: str


def gate_menu(gate: str, has_pending: bool = False) -> List[Option]:
    """The numbered options offered at ``gate``.

    This is the single definition of what an ordinal means: the card renders
    this list and :func:`parse_intent` resolves ordinals against the same list,
    so the two cannot drift.
    """
    gate = gate if gate in GATE_TARGETS else GATE1
    verb = "Apply my last message to" if has_pending else "Revise"
    options = [
        Option(REVISE, target, f"{verb} {_TARGET_LABELS[target]}")
        for target in GATE_TARGETS[gate]
    ]
    options.append(Option(APPROVE, "", "Approve as-is and continue"))
    options.append(Option(ABORT, "", "Abort this run"))
    return options


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Lowercase, NFKC-fold, collapse whitespace, drop surrounding punctuation.

    Punctuation is stripped from the ends only: ``"approve!"`` is an approval,
    ``"not good"`` still is not ``"good"``.
    """
    folded = unicodedata.normalize("NFKC", text or "").strip().lower()
    folded = re.sub(r"\s+", " ", folded)
    return folded.strip(" \t\r\n.!?,;:\"'()[]。！？、")


def _resolve_target(raw_target: str, gate: str) -> Tuple[str, str]:
    """Map a spoken target onto a target this gate can act on.

    Returns ``(target, reason)``. Exactly one of the two is non-empty.
    """
    key = _normalise(raw_target)
    canonical = _TARGET_ALIASES.get(key, "")
    if not canonical:
        return "", f"I don't have a target called '{raw_target.strip()}'."
    if canonical in GATE_TARGETS[gate]:
        return canonical, ""
    if canonical == "image" and gate == GATE2:
        return "", (
            "Image revisions aren't available at Gate 2 yet — there is no route "
            "from here back to ideation, so re-rendering the video would reuse "
            "the current image. `abort` and re-run the topic instead."
        )
    return "", f"'{canonical}' can't be changed at this gate."


# --------------------------------------------------------------------------
# The parser
# --------------------------------------------------------------------------

def parse_intent(
    message: str,
    gate: str = GATE1,
    pending_instruction: str = "",
) -> Intent:
    """Parse one human message at one gate. Never raises, never guesses.

    ``pending_instruction`` is the text of the previous *unclear* message. When
    the human answers the clarification menu with an ordinal, that text becomes
    the revision instruction — so being asked to clarify never costs them their
    original wording.
    """
    gate = gate if gate in GATE_TARGETS else GATE1
    raw = (message or "").strip()
    if not raw:
        return Intent(UNCLEAR, reason="Empty message.")

    norm = _normalise(raw)

    # --- control ---------------------------------------------------------
    if norm in _ABORTS:
        return Intent(ABORT)
    if norm in _RETRIES:
        return Intent(RETRY)
    if norm in _STATUSES:
        return Intent(STATUS)

    # --- approval / rejection -------------------------------------------
    if norm in _APPROVALS:
        return Intent(APPROVE)
    if norm in _REJECTIONS:
        return Intent(UNCLEAR, reason=_REJECTION_REASON)

    # --- ordinal ---------------------------------------------------------
    if _MULTI_ORDINAL_RE.match(norm):
        return Intent(
            UNCLEAR,
            reason="One change at a time for now — reply with a single number.",
        )

    ordinal_match = _ORDINAL_RE.match(norm)
    if ordinal_match:
        menu = gate_menu(gate, has_pending=bool(pending_instruction))
        index = int(ordinal_match.group(1))
        if not 1 <= index <= len(menu):
            return Intent(
                UNCLEAR,
                reason=f"There is no option {index} — pick 1 to {len(menu)}.",
            )
        chosen = menu[index - 1]
        if chosen.action != REVISE:
            return Intent(chosen.action)
        if not pending_instruction:
            return Intent(
                UNCLEAR,
                reason=(
                    f"Tell me what to change about {_TARGET_LABELS[chosen.target]} — "
                    f"e.g. `revise {chosen.target}: make the hat red`."
                ),
            )
        return Intent(REVISE, target=chosen.target, instruction=pending_instruction)

    # --- explicit revision ------------------------------------------------
    revise_match = _REVISE_RE.match(raw)
    if revise_match:
        target, reason = _resolve_target(revise_match.group("target"), gate)
        if not target:
            return Intent(UNCLEAR, reason=reason)
        instruction = revise_match.group("instruction").strip()
        params = extract_remix_parameters(instruction) if target == "remix" else {}
        return Intent(REVISE, target=target, instruction=instruction, params=params)

    bare_match = _REVISE_BARE_RE.match(raw)
    if bare_match:
        target, reason = _resolve_target(bare_match.group("target"), gate)
        if not target:
            return Intent(UNCLEAR, reason=reason)
        if not pending_instruction:
            return Intent(
                UNCLEAR,
                reason=(
                    f"Tell me what to change about {_TARGET_LABELS[target]} — "
                    f"e.g. `revise {target}: make the hat red`."
                ),
            )
        params = extract_remix_parameters(pending_instruction) if target == "remix" else {}
        return Intent(REVISE, target=target, instruction=pending_instruction, params=params)

    # --- run parameters (recognised, deliberately not executed yet) -------
    if _SET_RE.match(norm):
        return Intent(UNCLEAR, reason=_SET_DEFERRED_REASON)

    return Intent(UNCLEAR)


def looks_like_instruction(message: str) -> bool:
    """True when an unclear message reads as a fresh instruction.

    Used to decide whether a new unclear message *replaces* the pending one.
    A menu-shaped reply ("3", "1+2") or a bare rejection ("no") is an answer to
    the card, not a new request, so it must not overwrite the wording the human
    typed earlier. Anything else is treated as the latest instruction --
    otherwise "the hat should be red", then "actually make it blue", then "1"
    would apply the red one.
    """
    norm = _normalise(message)
    if not norm:
        return False
    if _ORDINAL_RE.match(norm) or _MULTI_ORDINAL_RE.match(norm):
        return False
    return norm not in (_REJECTIONS | _APPROVALS | _ABORTS | _RETRIES | _STATUSES)


def extract_remix_parameters(feedback: str) -> dict:
    """Extracts audio timing, subtitle timing, font size, colour and position.

    This is a *parameter* extractor, not an intent classifier: it only runs
    once the target is already known to be the remix, so a stray number in an
    unrelated sentence can no longer reach ffmpeg.
    """
    params: Dict[str, Any] = {}
    if not feedback:
        return params

    f_clean = feedback.strip()

    # 1. Audio start time: "audio should be inserted at 4s", "audio at 4s", "audio: 3s"
    audio_m = re.search(
        r'(?:audio|sound|track)\s+(?:should\s+(?:also\s+)?(?:be\s+)?(?:inserted\s+)?at|starts?\s+at|inserted\s+at|at|from|timing[:\s]+)?\s*(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?\b',
        f_clean,
        re.IGNORECASE
    )
    if audio_m:
        try:
            params["audio_start_time"] = float(audio_m.group(1))
        except (ValueError, TypeError):
            pass

    # 2. Subtitle / text start time: "subtitles should also appear at 4s", "text at 4s"
    text_start_m = re.search(
        r'(?:subtitles?|subs?|overlay|text)\s+(?:should\s+(?:also\s+)?(?:appear|be\s+inserted)\s+at|starts?\s+at|inserted\s+at|at|from|timing[:\s]+)?\s*(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?\b',
        f_clean,
        re.IGNORECASE
    )
    if text_start_m:
        try:
            params["text_start_time"] = float(text_start_m.group(1))
        except (ValueError, TypeError):
            pass

    # 3. Subtitle end time or duration: "until 6s", "end at 5s", "to 7s"
    text_end_m = re.search(
        r'(?:until|to|end\s+at|ends?\s+at)\s*(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?\b',
        f_clean,
        re.IGNORECASE
    )
    if text_end_m:
        try:
            params["text_end_time"] = float(text_end_m.group(1))
        except (ValueError, TypeError):
            pass

    # 4. Font size: "font size 60", "fontsize 48", "size 54"
    font_size_m = re.search(
        r'(?:font\s*size|fontsize|size)[:\s]+(\d+)\b',
        f_clean,
        re.IGNORECASE
    )
    if font_size_m:
        try:
            params["font_size"] = int(font_size_m.group(1))
        except (ValueError, TypeError):
            pass

    # 5. Position: "position: top", "at the top", "center", "middle"
    if re.search(r'\b(top|upper)\b', f_clean, re.IGNORECASE):
        params["position"] = "top"
    elif re.search(r'\b(bottom|lower)\b', f_clean, re.IGNORECASE):
        params["position"] = "bottom"
    elif re.search(r'\b(center|middle)\b', f_clean, re.IGNORECASE):
        params["position"] = "center"

    # 6. Font colour, only when the word "color"/"colour" is present
    color_m = re.search(r'\b(yellow|white|red|green|blue|black|cyan|magenta|orange|gold)\b', f_clean, re.IGNORECASE)
    if color_m and ("color" in f_clean.lower() or "colour" in f_clean.lower()):
        params["font_color"] = color_m.group(1).lower()

    return params
