"""What the voiceprint gate answers in Discord: `!voiceprint` and `!gate`.

    !voiceprint          the gate's mode, your voiceprint, who is heard, and
                         the last day's counts
    !voiceprint reset    deletes your voiceprint and the kept clips
    !gate review         posts the last day's utterances worth a check, one
                         message each: every one the gate wouldn't call yours,
                         and yours by a thin margin. React ✅ if it was you, ❌
                         if it wasn't; one you don't mark keeps the gate's
                         verdict. Your marks are what the thresholds are set from.
    !gate review all     the same, for every unmarked utterance: for a
                         passenger the gate took for you

Clips aren't attached: the audio stays on this machine, in
sessions/voice/gate_audio/, named by the number each message starts with.
"""
import asyncio
import time
from typing import Any, Dict, Optional

from core.voice import settings
from core.voice.listening.gate_log import Effect, GateRecord, Label

REVIEW_HOURS = 24
REVIEW_LIMIT = 10
REACTIONS = {"✅": Label.YOU, "❌": Label.NOT_YOU}

_VERDICTS = {"YOU": "you", "NOT_YOU": "not you", "UNDECIDED": "undecided"}
_EFFECTS = {
    Effect.TURN: "it started a turn",
    Effect.MERGE: "it joined your waiting words",
    Effect.BARGE_IN: "it stopped the reply",
    Effect.OVERLAP: "it came over the reply",
}
GATE_USAGE = ("`!gate review` posts the last day's utterances worth a check (`!gate review all`: every "
              "unmarked one). React ✅ if it was you, ❌ if it wasn't.")
VOICEPRINT_USAGE = "`!voiceprint` shows the gate and your voiceprint; `!voiceprint reset` deletes it."


def counts_line(summary: Dict[str, Any]) -> str:
    """The last day's counts, from GateLog.summary."""
    total = summary.get("total") or 0
    if not total:
        return "Nothing scored in the last day."
    verdicts, labels = summary.get("verdicts") or {}, summary.get("labels") or {}
    line = (f"Last day: {total} scored · you {verdicts.get('YOU', 0)} · not you {verdicts.get('NOT_YOU', 0)} "
            f"· undecided {verdicts.get('UNDECIDED', 0)}")
    if labels:
        line += f" · marked ✅ {labels.get('YOU', 0)} ❌ {labels.get('NOT_YOU', 0)}"
    if summary.get("median_compute_ms") is not None:
        line += f" · median scoring {summary['median_compute_ms']} ms"
    return line + "."


def status(gate, now: Optional[float] = None) -> str:
    """What `!voiceprint` says."""
    now = time.time() if now is None else now
    model = gate.model
    lines = [f"🎙️ **Voiceprint gate:** `{gate.mode}`"]
    if model is None:
        lines.append(f"The speaker model `{settings.SPEAKER_MODEL_FILE}` isn't installed: run "
                     f"`scripts/provision_assets.py --stage runtime --only speaker`.")
    saved = gate.saved_voiceprint()
    if saved is None:
        lines.append("No voiceprint yet: join the voice channel and type `!enroll`.")
    else:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(saved.enrolled_at))
        lines.append(f"Voiceprint from {when}: {saved.readings} readings, consistency {saved.consistency:.2f}, "
                     f"account {saved.user_id or 'unknown'}.")
        if model is not None and saved.model != model.name:
            lines.append(f"⚠️ It was enrolled with `{saved.model}`, but the model is now `{model.name}`: "
                         f"type `!enroll` again.")
    owners = gate.owner_ids()
    lines.append("Heard: " + (f"only account {', '.join(sorted(owners))}." if owners
                              else "everyone in the voice channel."))
    lines.append(f"Thresholds (provisional): yours at {settings.SPEAKER_ACCEPT:.2f} or above, "
                 f"not yours at {settings.SPEAKER_REJECT:.2f} or below.")
    lines.append(counts_line(gate.log.summary(since=now - REVIEW_HOURS * 3600)))
    return "\n".join(lines)


def voiceprint_command(gate, args: str) -> str:
    """`!voiceprint [reset]`."""
    action = (args or "").strip().lower()
    if action == "reset":
        return "🗑️ Voiceprint deleted, with the kept clips." if gate.reset() else "There was no voiceprint to delete."
    if action:
        return VOICEPRINT_USAGE
    return status(gate)


def review_line(record: GateRecord) -> str:
    """One utterance to check: its number, verdict and score, what it did, its words."""
    when = time.strftime("%a %H:%M", time.localtime(record.at))
    score = "no score" if record.score is None else f"{record.score:.2f}"
    parts = [f"#{record.id} · {when}", f"**{_VERDICTS.get(record.verdict, record.verdict)}** {score}"]
    if record.voiced_share is not None:
        parts.append(f"voiced {record.voiced_share:.2f}")
    if record.effect is not None:
        parts.append(_EFFECTS[record.effect])
    words = (record.transcript or "").strip() or "(no words)"
    return " · ".join(parts) + f"\n> {words}"


async def post_review(gate, channel, everything: bool = False, now: Optional[float] = None) -> int:
    """Posts the utterances to check, each with ✅ and ❌ to tap. Returns how many."""
    now = time.time() if now is None else now
    # Cosine similarity never reaches 2: "below 2" takes every utterance.
    below = 2.0 if everything else settings.SPEAKER_ACCEPT + settings.SPEAKER_BORDERLINE
    records = await asyncio.to_thread(gate.log.to_review, now - REVIEW_HOURS * 3600, below, REVIEW_LIMIT)
    if not records:
        await channel.send("🗣️ Nothing to review from the last day.")
        return 0
    await channel.send(f"🗣️ **{len(records)} to check.** React ✅ if it was you, ❌ if it wasn't. "
                       f"Unmarked ones keep the gate's verdict.")
    for record in records:
        message = await channel.send(review_line(record))
        await asyncio.to_thread(gate.log.set_review_message, record.id, message.id)
        for emoji in REACTIONS:
            try:
                await message.add_reaction(emoji)
            except Exception as e:
                print(f"[SpeakerGate] Could not add {emoji} to review message: {e}")
    return len(records)


async def gate_command(gate, args: str, channel) -> None:
    """`!gate review [all]`; anything else shows how, and the last day's counts."""
    words = (args or "").strip().lower().split()
    if words[:1] == ["review"]:
        await post_review(gate, channel, everything=words[1:] == ["all"])
        return
    await channel.send(GATE_USAGE + "\n" + counts_line(gate.log.summary(since=time.time() - REVIEW_HOURS * 3600)))


def mark_from_reaction(gate, message_id: Any, emoji: str) -> bool:
    """✅ or ❌ on a `!gate review` message marks its utterance. True if it was one."""
    label = REACTIONS.get(emoji)
    return label is not None and gate.log.label_by_message(message_id, label)
