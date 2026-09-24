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
reply is the one latency cost a voice conversation cannot hide. Simple
structure -- a heading, a flat list of short items, a two-column table -- is
also rendered by rule (`render_simple_structure`); the model is kept for the
blocks those rules decline.

Notation that always resolves the same way -- XML blocks, filing hashtags,
priority symbols -- is normalised in `core.voice.speech_util` before any of
this runs. Streaming lives in `core.voice.voice_stream`.
"""
import asyncio
import re

from core.util.models import DEFAULT_VERBALIZER_MODEL
from core.voice.prompts import build_verbalizer_prompt
from core.voice.simple_structure import render_simple_structure
from core.voice.speech_util import speak_priority_symbols, strip_hashtags, strip_xml
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

    def _invoke_config(self, session=None) -> dict:
        """Callbacks and tags so the call is billed to the turn that caused it.

        This is the one LLM call made outside an agent graph, so nothing
        attaches `LoggingHandler` for it; without this its tokens are spent
        but recorded nowhere. The session is the voice turn's context (tagged
        surface="voice" by `VoiceManager`), taken from the ambient execution
        context when not passed explicitly.
        """
        if session is None:
            try:
                from core.runtime.execution_context import try_context

                session = try_context()
            except Exception:
                session = None
        config: dict = {"run_name": "verbalizer", "tags": ["verbalizer"]}
        if session is None:
            return config
        try:
            from core.runtime.token_usage_handler import TokenUsageHandler

            config["callbacks"] = [TokenUsageHandler(session=session)]
            config["metadata"] = {
                "agent_id": session.agent_id,
                "session_id": session.session_id,
                "source": session.source,
                "surface": session.get_surface(),
            }
            config["tags"].append(session.agent_id)
        except Exception as e:
            print(f"[Verbalizer] Token logging unavailable ({e}); continuing.")
        return config

    async def verbalize(self, text: str, session=None) -> str:
        """Returns speech-ready text. Never raises.

        `session` attributes the model's token usage; it defaults to the
        ambient execution context.
        """
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

        # Simple structure is rendered by rule; only what the rules decline
        # (nesting, code, wide tables, ...) is worth a model round-trip.
        simple = render_simple_structure(text)
        if simple is not None:
            return TextSanitizer.sanitize(simple)

        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            response = await asyncio.wait_for(
                self._get_llm().ainvoke(
                    [
                        SystemMessage(content=build_verbalizer_prompt()),
                        HumanMessage(content=text),
                    ],
                    config=self._invoke_config(session),
                ),
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
