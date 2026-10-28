"""When a voice reply comes without its <speech> block.

Every voice turn asks the agent for one (`<voice_mode>`), so a reply without
it is a bug to see and fix, not a mode. Every miss is logged and noted in the
room's text channel, right under the reply (core/voice/speech/reply.py). So
the user never hears silence, a deterministic fallback -- no model call --
speaks the reply's first sentence and says where the rest is.
"""
from core.voice.speech.sentence_chunker import SentenceChunker
from core.voice.speech.speech_util import speakable

REST_IN_CHANNEL = "The rest is in the channel."


def fallback_speech(text: str) -> str:
    """The reply's first sentence, and where the rest is if there is more."""
    sentences = SentenceChunker().split_into_sentences(speakable(text))
    if not sentences:
        return ""
    if len(sentences) == 1:
        return sentences[0]
    return f"{sentences[0]} {REST_IN_CHANNEL}"


def alert(agent_id: str) -> str:
    """The note posted under a reply that came without <speech>."""
    return f"⚠️ `{agent_id}` replied without a `<speech>` block; the voice read the fallback."
